"""Entrada única do Apolo.

`apolo run` faz a varredura incremental, passa cada email novo pela cascata de
regras e grava a ação sugerida; `apolo status` mostra os contadores. Os demais
comandos (review, block/allow, rules, undo, setup) chegam nos passos seguintes.
"""

import argparse
import sys

from apolo.config import Config
from apolo.fetch.imap import BridgeClient
from apolo.rules.engine import RuleEngine
from apolo.storage.db import STATUS_AGUARDANDO, STATUS_CLASSIFICADO, Storage


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


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(prog="apolo", description="Triador pessoal de emails.")
    sub = parser.add_subparsers(dest="comando", required=True)
    sub.add_parser("run", help="dispara uma passada (fetch incremental + grava).")
    sub.add_parser("status", help="última execução, contadores.")
    return parser


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config = Config.load()
    try:
        if args.comando == "run":
            return cmd_run(config)
        if args.comando == "status":
            return cmd_status(config)
    except Exception as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
