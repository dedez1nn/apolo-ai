"""Entrada única do Apolo.

`apolo run` varre e classifica; `apolo review` abre a TUI pra despachar a fila;
`apolo block`/`allow` editam as regras pelo terminal; `apolo rules` lista o
config; `apolo status` mostra os contadores; `apolo setup` instala o timer do
systemd. (undo chega depois.)
"""

import argparse
import os
import shutil
import subprocess
import sys
import tomllib
from pathlib import Path

from apolo.actions import dispatch
from apolo.ai.ollama import OllamaClient
from apolo.clean import clean_for_classification, message_to_text
from apolo.config import Config
from apolo.fetch.imap import BridgeClient
from apolo.notify import notify
from apolo.rules.engine import RuleEngine
from apolo.rules.writer import add_rule_entry, detect_tipo
from apolo.storage.db import STATUS_AGUARDANDO, STATUS_CLASSIFICADO, Storage
from apolo.tui import review_queue


def cmd_run(config: Config, notify_enabled: bool = True) -> int:
    """Uma passada: busca os novos, classifica pela cascata e manda o resíduo pra IA."""
    config.require_credentials()
    engine = RuleEngine.from_file(config.rules_path)

    # "Analisando…" abre a notificação; o resumo no fim a substitui (replace_id),
    # então fica uma só na tela em vez de empilhar duas por execução.
    nid = (
        notify("Apolo", "Analisando emails novos…", urgency="low")
        if notify_enabled
        else None
    )

    # A IA é opcional: se o Ollama estiver fora, o resíduo só fica em 'revisar'.
    ollama = OllamaClient(
        config.ollama_url, config.ollama_model, keep_alive=config.ollama_keep_alive
    )
    ai_ready = config.ai_enabled and ollama.available()
    if config.ai_enabled and not ai_ready:
        print("IA indisponível (Ollama fora do ar) — resíduo fica como 'revisar'.")

    total_novos = 0
    analisados = 0  # todos os emails percorridos nesta passada (pro resumo)
    revisar = 0  # foram pra fila (aguardando)
    mantidos = 0  # terminaram como 'classificado' (sem ação)
    fila_total = 0
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
                residuo = []  # emails que a cascata não resolveu (vão pra IA)
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
                    analisados += 1
                    if novo_status == STATUS_AGUARDANDO:
                        revisar += 1
                    else:
                        mantidos += 1
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
                    if decisao.regra_casada == "default":
                        residuo.append(m)

                # 2ª passada: só o resíduo vai pro Ollama (já quente). A pasta
                # segue selecionada do fetch_new, então fetch_message funciona.
                if ai_ready and residuo:
                    n_ia = _ai_pass(client, store, ollama, pasta, result.uidvalidity, residuo)
                    print(f"[{pasta}] IA classificou {n_ia}/{len(residuo)} do resíduo.")

                store.set_folder_meta(pasta, result.uidvalidity, result.ultimo_uid)
                total_novos += novos_pasta
                print(f"[{pasta}] {novos_pasta} novo(s) (UID até {result.ultimo_uid}).")

        fila_total = store.count_queue()

    print(f"\n{total_novos} email(s) novo(s).")
    if acoes:
        resumo = ", ".join(f"{n} {acao}" for acao, n in sorted(acoes.items()))
        print(f"Sugestões da cascata: {resumo}.")

    if notify_enabled:
        _notify_resumo(analisados, mantidos, revisar, fila_total, replace_id=nid)
    return 0


def _notify_resumo(
    analisados: int, mantidos: int, revisar: int, fila_total: int, *, replace_id: int | None
) -> None:
    """Substitui a notificação de "analisando…" pelo resumo da passada.

    Sem novidade vira um aviso curto e de baixa urgência (o timer roda direto,
    não vale empurrar popup chamativo à toa). Com algo pra revisar, urgência
    normal pra puxar o olho.
    """
    if analisados == 0:
        notify(
            "Apolo: nada novo",
            f"Fila de revisão: {fila_total}.",
            urgency="low",
            expire_ms=4000,
            replace_id=replace_id,
        )
        return
    titulo = f"Apolo: {analisados} analisado(s)"
    corpo = f"{mantidos} mantido(s), {revisar} pra revisar · fila: {fila_total}."
    notify(
        titulo,
        corpo,
        urgency="normal" if revisar else "low",
        replace_id=replace_id,
    )


def _ai_pass(client, store, ollama, pasta, uidvalidity, residuo) -> int:
    """Classifica o resíduo pelo Ollama: corpo limpo -> sugestão. Falha por email é ignorada.

    A IA só enriquece a sugestão; o email continua na fila (aguardando) pro dono
    confirmar. Nada é executado aqui.
    """
    classificados = 0
    for m in residuo:
        try:
            msg = client.fetch_message(m.uid)
            trecho = clean_for_classification(message_to_text(msg)) if msg else ""
            decisao = ollama.classify(
                assunto=m.assunto, remetente=m.remetente, trecho=trecho
            )
        except Exception:
            decisao = None
        if decisao is None:
            continue
        store.classify_email(
            pasta=pasta,
            uidvalidity=uidvalidity,
            uid=m.uid,
            status=STATUS_AGUARDANDO,
            categoria=decisao.categoria,
            acao_sugerida=decisao.acao,
            regra_casada=f"ia:{decisao.categoria}",
        )
        classificados += 1
    return classificados


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


_SYSTEMD_TEMPLATE_DIR = Path(__file__).resolve().parent / "systemd"
_USER_UNIT_DIR = Path(os.path.expanduser("~/.config/systemd/user"))


def cmd_setup(config: Config, interval: str, enable: bool = True) -> int:
    """Renderiza as units do systemd (user) e ativa o timer.

    Detecta o interpretador e a raiz do projeto na hora, então a instalação não
    depende de venv nem de caminho chumbado. Reentrante: rodar de novo regrava as
    units (útil pra trocar o intervalo) e recarrega o systemd.
    """
    workdir = Path(__file__).resolve().parent.parent  # raiz do projeto
    fields = {"python": sys.executable, "workdir": str(workdir), "interval": interval}

    _USER_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    for nome in ("apolo.service", "apolo.timer"):
        template = (_SYSTEMD_TEMPLATE_DIR / nome).read_text(encoding="utf-8")
        destino = _USER_UNIT_DIR / nome
        destino.write_text(template.format(**fields), encoding="utf-8")
        print(f"escrito: {destino}")

    if shutil.which("systemctl") is None:
        print("systemctl não encontrado — units escritas, mas não ativadas.")
        return 0

    subprocess.run(["systemctl", "--user", "daemon-reload"], check=False)
    if enable:
        subprocess.run(
            ["systemctl", "--user", "enable", "--now", "apolo.timer"], check=False
        )
        print(f"timer ativado (a cada {interval}). Status: systemctl --user status apolo.timer")
    else:
        print("units escritas. Ative com: systemctl --user enable --now apolo.timer")
    print("Logs: journalctl --user -u apolo -f")
    return 0


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apolo", description="Triador pessoal de emails.")
    sub = parser.add_subparsers(dest="comando", required=True)
    p_run = sub.add_parser("run", help="dispara uma passada (fetch incremental + classifica).")
    p_run.add_argument(
        "-q", "--quiet", action="store_true", help="não envia notificações de desktop."
    )
    sub.add_parser("status", help="última execução, contadores.")
    sub.add_parser("review", help="abre a TUI pra despachar a fila de revisão.")
    sub.add_parser("rules", help="lista as regras configuradas.")

    p_setup = sub.add_parser("setup", help="instala/atualiza o timer do systemd (user).")
    p_setup.add_argument(
        "--interval", default="15min", help="frequência do timer (ex.: 15min, 1h). Padrão 15min."
    )
    p_setup.add_argument(
        "--no-enable", action="store_true", help="escreve as units sem ativar o timer."
    )

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
            return cmd_run(config, notify_enabled=not args.quiet)
        if args.comando == "status":
            return cmd_status(config)
        if args.comando == "setup":
            return cmd_setup(config, args.interval, enable=not args.no_enable)
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
