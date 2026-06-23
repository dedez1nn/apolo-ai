"""Entrada única do Apolo.

`apolo run` varre e classifica; `apolo review` abre a TUI pra despachar a fila;
`apolo block`/`allow` editam as regras pelo terminal; `apolo rules` lista o
config; `apolo status` mostra os contadores. (undo e setup chegam depois.)
"""

import argparse
import sys
import tomllib

from apolo.actions import dispatch
from apolo.config import Config
from apolo.fetch.imap import BridgeClient
from apolo.rules.engine import RuleEngine
from apolo.rules.writer import add_rule_entry, detect_tipo
from apolo.storage.db import STATUS_AGUARDANDO, STATUS_CLASSIFICADO, Storage
from apolo.tui import review_queue


def cmd_run(config: Config) -> int:
    """Uma passada: busca os UIDs novos, classifica pela cascata e grava."""
    config.require_credentials()
    engine = RuleEngine.from_file(config.rules_path)

    total_novos = 0
    acoes: dict[str, int] = {}
    with Storage(config.db_path) as store:
        with BridgeClient(
            config.imap_host, config.imap_port, config.imap_security
        ) as client:
            client.login(config.username, config.password)

            for pasta in config.folders:
                meta = store.get_folder_meta(pasta)
                known_uidvalidity = meta[0] if meta else None
                last_uid = meta[1] if meta else 0

                result = client.fetch_new(pasta, known_uidvalidity, last_uid)

                if result.resynced and meta is not None:
                    # UIDVALIDITY mudou: zera o estado da pasta antes de regravar.
                    print(f"[{pasta}] UIDVALIDITY mudou — ressincronizando.")
                    store.reset_folder(pasta)

                novos_pasta = 0
                for m in result.novos:
                    if store.insert_email(
                        pasta=pasta,
                        uidvalidity=result.uidvalidity,
                        uid=m.uid,
                        message_id=m.message_id,
                        remetente=m.remetente,
                        assunto=m.assunto,
                        data=m.data,
                    ):
                        novos_pasta += 1

                    # Cascata determinística (sem IA). Tudo é sugestão: 'manter'
                    # é terminal; o resto entra na fila de revisão (aguardando).
                    decisao = engine.classify(
                        remetente=m.remetente,
                        assunto=m.assunto,
                        list_unsubscribe=m.list_unsubscribe,
                    )
                    novo_status = (
                        STATUS_AGUARDANDO if decisao.precisa_revisao else STATUS_CLASSIFICADO
                    )
                    store.classify_email(
                        pasta=pasta,
                        uidvalidity=result.uidvalidity,
                        uid=m.uid,
                        status=novo_status,
                        categoria=decisao.categoria,
                        acao_sugerida=decisao.acao_sugerida,
                        regra_casada=decisao.regra_casada,
                    )
                    acoes[decisao.acao_sugerida] = acoes.get(decisao.acao_sugerida, 0) + 1

                store.set_folder_meta(pasta, result.uidvalidity, result.ultimo_uid)
                total_novos += novos_pasta
                print(f"[{pasta}] {novos_pasta} novo(s) (UID até {result.ultimo_uid}).")

    print(f"\n{total_novos} email(s) novo(s).")
    if acoes:
        resumo = ", ".join(f"{n} {acao}" for acao, n in sorted(acoes.items()))
        print(f"Sugestões desta passada: {resumo}.")
    return 0


def cmd_status(config: Config) -> int:
    """Última execução e contadores por status."""
    with Storage(config.db_path) as store:
        ultima = store.last_processed_at()
        counts = store.status_counts()
        acoes = store.acao_counts()

    print(f"Banco:           {config.db_path}")
    print(f"Regras:          {config.rules_path}")
    print(f"Última execução: {ultima or '(nunca)'}")
    if not counts:
        print("Nenhum email registrado ainda.")
        return 0
    print("Por status:")
    for status, n in sorted(counts.items()):
        print(f"  {status:<14} {n}")
    print(f"  {'total':<14} {sum(counts.values())}")
    if acoes:
        print("Ação sugerida:")
        for acao, n in sorted(acoes.items()):
            print(f"  {acao:<14} {n}")
    return 0


def cmd_review(config: Config) -> int:
    """Abre a TUI pra despachar a fila; aplica as ações via IMAP ao sair."""
    with Storage(config.db_path) as store:
        rows = store.fetch_queue()
        if not rows:
            print("Fila vazia — nada pra revisar.")
            return 0

        itens = review_queue(rows, config.rules_path)
        if not itens:
            print("Nada despachado.")
            return 0

        precisa_imap = any(i.acao == "lixeira" for i in itens)
        if precisa_imap:
            config.require_credentials()
            with BridgeClient(
                config.imap_host, config.imap_port, config.imap_security
            ) as client:
                client.login(config.username, config.password)
                res = dispatch(client, store, itens, trash_folder=config.trash_folder)
        else:
            res = dispatch(_NoClient(), store, itens, trash_folder=config.trash_folder)

    print(f"Despachado: {res.lixeira} pra lixeira, {res.mantidos} mantido(s).")
    return 0


class _NoClient:
    """Sentinela: usado quando nenhum item vai pra lixeira (sem IMAP)."""

    def copy_to(self, *a, **k):  # pragma: no cover - nunca chamado
        raise AssertionError("dispatch sem IMAP não deveria mover emails")

    def expunge(self, *a, **k):  # pragma: no cover
        raise AssertionError("dispatch sem IMAP não deveria expurgar")


def _cmd_rule(config: Config, lista: str, valor: str, tipo: str | None) -> int:
    tipo = tipo or detect_tipo(valor)
    status = add_rule_entry(config.rules_path, lista=lista, tipo=tipo, valor=valor)
    verbo = "já estava em" if status == "exists" else "adicionado a"
    print(f"{tipo} {valor!r} {verbo} {lista} ({config.rules_path}).")
    return 0


def cmd_block(config: Config, valor: str, tipo: str | None) -> int:
    return _cmd_rule(config, "blocklist", valor, tipo)


def cmd_allow(config: Config, valor: str, tipo: str | None) -> int:
    return _cmd_rule(config, "allowlist", valor, tipo)


def cmd_rules(config: Config) -> int:
    """Lista o que está configurado no TOML."""
    path = config.rules_path
    print(f"Regras: {path}")
    if not path.is_file():
        print("(arquivo de regras ainda não existe)")
        return 0
    with path.open("rb") as f:
        data = tomllib.load(f)

    for lista in ("allowlist", "blocklist"):
        secao = data.get(lista, {}) or {}
        rem = secao.get("remetentes", []) or []
        dom = secao.get("dominios", []) or []
        print(f"\n[{lista}]  {len(rem)} remetente(s), {len(dom)} domínio(s)")
        for x in rem + dom:
            print(f"  - {x}")

    unsub = data.get("unsubscribe", {}) or {}
    print(f"\n[unsubscribe] ativo={unsub.get('ativo', False)} acao={unsub.get('acao', '-')}")

    keywords = data.get("keywords", []) or []
    print(f"\n[keywords] {len(keywords)} grupo(s)")
    for g in keywords:
        print(f"  - {g.get('nome')}: {g.get('acao')} (campo={g.get('campo')}) {g.get('padroes')}")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apolo", description="Triador pessoal de emails.")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("run", help="dispara uma passada (fetch incremental + classifica).")
    sub.add_parser("status", help="última execução, contadores.")
    sub.add_parser("review", help="abre a TUI pra despachar a fila de revisão.")
    sub.add_parser("rules", help="lista as regras configuradas.")

    p_block = sub.add_parser("block", help="adiciona remetente/domínio à blocklist.")
    p_block.add_argument("valor", help="email ou domínio (ex.: promo.x.com).")
    p_allow = sub.add_parser("allow", help="adiciona remetente/domínio à allowlist.")
    p_allow.add_argument("valor", help="email ou domínio (ex.: chefe@x.com).")
    for p in (p_block, p_allow):
        grupo = p.add_mutually_exclusive_group()
        grupo.add_argument("--dominio", action="store_const", const="dominio", dest="tipo")
        grupo.add_argument("--remetente", action="store_const", const="remetente", dest="tipo")
        p.set_defaults(tipo=None)
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.load()
    try:
        if args.comando == "run":
            return cmd_run(config)
        if args.comando == "status":
            return cmd_status(config)
        if args.comando == "review":
            return cmd_review(config)
        if args.comando == "rules":
            return cmd_rules(config)
        if args.comando == "block":
            return cmd_block(config, args.valor, args.tipo)
        if args.comando == "allow":
            return cmd_allow(config, args.valor, args.tipo)
    except Exception as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
