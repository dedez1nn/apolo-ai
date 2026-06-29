"""Entrada única do Apolo.

`apolo run` varre e classifica; `apolo review` abre a TUI pra despachar a fila;
`apolo block`/`allow` editam as regras pelo terminal; `apolo rules` lista o
config; `apolo status` mostra os contadores; `apolo setup` instala o timer do
systemd. (undo chega depois.)
"""

import argparse
import shutil
import sys
import tomllib
from pathlib import Path

from apolo.ai.ollama import OllamaClient
from apolo.clean import clean_for_classification, message_to_text
from apolo.config import AccountConfig, Config, load_accounts
from apolo.fetch.imap import BridgeClient
from apolo.notify import notify
from apolo.rules.engine import RuleEngine
from apolo.rules.writer import add_rule_entry, detect_tipo
from apolo.storage.db import STATUS_AGUARDANDO, STATUS_CLASSIFICADO, Storage


def _gmail_run(
    config: Config,
    account: AccountConfig,
    store: Storage,
    engine: RuleEngine,
    ollama: OllamaClient,
    ai_ready: bool,
) -> tuple[int, int, int, dict]:
    """Roda fetch+classifica pra uma conta Gmail. Retorna (total_novos, analisados, revisar, acoes)."""
    from apolo.fetch.gmail import GmailClient

    conta_id = f"gmail:{account.name}"
    token_path = config.tokens_dir / f"{account.name}.json"
    client = GmailClient(
        account.name,
        account.client_id,
        account.client_secret,
        token_path,
        folders=account.folders,
    )

    if not client.is_authorized():
        print(f"[{conta_id}] conta não autorizada — execute: apolo accounts add --name {account.name}")
        return 0, 0, 0, {}

    total_novos = analisados = revisar = 0
    acoes: dict[str, int] = {}

    for pasta in account.folders:
        meta_key = f"{conta_id}:{pasta}"
        meta = store.get_folder_meta(meta_key)
        last_uid = meta[1] if meta else 0

        result = client.fetch_new(pasta, last_uid)

        if result.resynced and meta is not None:
            print(f"[{conta_id}/{pasta}] resync completo.")
            store.reset_folder(meta_key)

        novos_pasta = 0
        residuo = []
        pasta_db = f"{conta_id}:{pasta}"  # namespace único no DB

        for m in result.novos:
            if store.insert_email(
                conta=conta_id,
                pasta=pasta_db,
                uidvalidity=result.uidvalidity,
                uid=m.uid,
                message_id=m.message_id,
                remetente=m.remetente,
                assunto=m.assunto,
                data=m.data,
                provider_id=m.provider_id,
            ):
                novos_pasta += 1

            decisao = engine.classify(
                remetente=m.remetente,
                assunto=m.assunto,
                list_unsubscribe=m.list_unsubscribe,
            )
            novo_status = STATUS_AGUARDANDO if decisao.precisa_revisao else STATUS_CLASSIFICADO
            analisados += 1
            if novo_status == STATUS_AGUARDANDO:
                revisar += 1
            store.classify_email(
                pasta=pasta_db,
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

        if ai_ready and residuo:
            n_ia = _ai_pass_gmail(client, store, ollama, pasta_db, result.uidvalidity, residuo)
            print(f"[{conta_id}/{pasta}] IA classificou {n_ia}/{len(residuo)} do resíduo.")

        store.set_folder_meta(meta_key, result.uidvalidity, result.ultimo_uid)
        total_novos += novos_pasta
        print(f"[{conta_id}/{pasta}] {novos_pasta} novo(s) (historyId={result.ultimo_uid}).")

    return total_novos, analisados, revisar, acoes


def _ai_pass_gmail(gmail_client, store, ollama, pasta_db, uidvalidity, residuo) -> int:
    classificados = 0
    for m in residuo:
        try:
            trecho = gmail_client.fetch_message(m.provider_id) if m.provider_id else ""
            trecho = clean_for_classification(trecho)
            decisao = ollama.classify(assunto=m.assunto, remetente=m.remetente, trecho=trecho)
        except Exception:
            decisao = None
        if decisao is None:
            continue
        store.classify_email(
            pasta=pasta_db,
            uidvalidity=uidvalidity,
            uid=m.uid,
            status=STATUS_AGUARDANDO,
            categoria=decisao.categoria,
            acao_sugerida=decisao.acao,
            regra_casada=f"ia:{decisao.categoria}",
        )
        classificados += 1
    return classificados


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

        # Contas Gmail adicionais (mesma conexão)
        gmail_accounts = [a for a in load_accounts(config.accounts_path) if a.provider == "gmail"]
        for account in gmail_accounts:
            gn, ga, gr, gacoes = _gmail_run(config, account, store, engine, ollama, ai_ready)
            total_novos += gn
            analisados += ga
            revisar += gr
            for k, v in gacoes.items():
                acoes[k] = acoes.get(k, 0) + v

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


def _rules_count(rules_path: Path) -> int:
    """Quantas entradas de allow/blocklist existem (pro Hub/Status)."""
    if not rules_path.is_file():
        return 0
    with rules_path.open("rb") as f:
        data = tomllib.load(f)
    total = 0
    for lista in ("allowlist", "blocklist"):
        secao = data.get(lista, {}) or {}
        total += len(secao.get("remetentes", []) or []) + len(secao.get("dominios", []) or [])
    return total


def cmd_review(config: Config) -> int:
    """Abre o hub (UI Textual) pra revisar a fila.

    O despacho real (mover pra lixeira via IMAP/Gmail) acontece DENTRO da TUI,
    na hora em que o dono aperta Enter pra aplicar — ver apolo.ui.queue. Aqui só
    montamos os dados, abrimos a UI e, por garantia, despachamos qualquer item
    que por algum motivo tenha voltado sem ter sido aplicado inline.
    """
    from apolo.actions import apply_decisions
    from apolo.ui import run_ui
    from apolo.ui.app import UiStats

    accounts_by_name = {
        a.name: a for a in load_accounts(config.accounts_path) if a.provider == "gmail"
    }
    contas_ativas = {"proton"} | {f"gmail:{n}" for n in accounts_by_name}

    with Storage(config.db_path) as store:
        rows = store.fetch_queue()
        stats = UiStats(
            last_run=store.last_processed_at(),
            rules_count=_rules_count(config.rules_path),
            status_counts=store.status_counts(),
            acao_counts=store.acao_counts(),
        )

    itens = run_ui(rows, config.rules_path, stats, config, contas_ativas=contas_ativas)
    if not itens:
        return 0

    # Fallback: a TUI normalmente já despachou inline; se algo voltou, aplica.
    res = apply_decisions(config, itens)
    print(f"Despachado: {res.lixeira} pra lixeira, {res.mantidos} mantido(s).")
    return 0


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


def cmd_accounts_add(config: Config, name: str, provider: str, client_id: str, client_secret: str) -> int:
    """Adiciona uma conta ao accounts.toml e inicia o fluxo de autorização OAuth2."""
    import tomllib

    path = config.accounts_path
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if path.is_file():
        path.chmod(0o600)
        with path.open("rb") as f:
            existing = tomllib.load(f)

    accounts = existing.get("accounts", [])

    # Verifica se já existe
    for acc in accounts:
        if acc.get("name") == name:
            print(f"Conta '{name}' já existe em {path}.")
            # Ainda permite reautorizar
            break
    else:
        accounts.append({
            "name": name,
            "provider": provider,
            "client_id": client_id,
            "client_secret": client_secret,
            "folders": ["INBOX"],
        })
        existing["accounts"] = accounts
        lines = ["[[accounts]]"]
        for acc in existing["accounts"]:
            if lines[-1] != "[[accounts]]":
                lines.append("")
                lines.append("[[accounts]]")
            lines.append(f'name = "{acc["name"]}"')
            lines.append(f'provider = "{acc["provider"]}"')
            lines.append(f'client_id = "{acc["client_id"]}"')
            lines.append(f'client_secret = "{acc["client_secret"]}"')
            lines.append(f'folders = {acc["folders"]!r}')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.chmod(0o600)
        print(f"Conta '{name}' adicionada em {path}.")

    if provider == "gmail":
        from apolo.fetch.gmail import GmailClient

        token_path = config.tokens_dir / f"{name}.json"
        client = GmailClient(name, client_id, client_secret, token_path)
        if client.is_authorized():
            print(f"Conta '{name}' já está autorizada.")
        else:
            client.authorize()
    return 0


def cmd_accounts_list(config: Config) -> int:
    accounts = load_accounts(config.accounts_path)
    if not accounts:
        print(f"Nenhuma conta em {config.accounts_path}")
        return 0
    for acc in accounts:
        token_path = config.tokens_dir / f"{acc.name}.json"
        status = "autorizada" if token_path.is_file() else "NÃO autorizada"
        print(f"  {acc.name}  ({acc.provider})  — {status}")
    return 0


def cmd_setup(config: Config, interval: str, enable: bool = True) -> int:
    """Renderiza as units do systemd (user) e ativa o timer.

    Detecta o interpretador e a raiz do projeto na hora, então a instalação não
    depende de venv nem de caminho chumbado. Reentrante: rodar de novo regrava as
    units (útil pra trocar o intervalo) e recarrega o systemd.
    """
    from apolo import scheduler

    for destino in scheduler.escrever_units(interval):
        print(f"escrito: {destino}")

    if shutil.which("systemctl") is None:
        print("systemctl não encontrado — units escritas, mas não ativadas.")
        return 0

    if enable:
        print(scheduler.ativar(interval))
        print("Status: systemctl --user status apolo.timer")
    else:
        scheduler._systemctl("daemon-reload")
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

    p_accounts = sub.add_parser("accounts", help="gerencia contas externas (Gmail, etc.).")
    accsub = p_accounts.add_subparsers(dest="acc_comando", required=True)
    accsub.add_parser("list", help="lista contas configuradas.")
    p_acc_add = accsub.add_parser("add", help="adiciona conta e autoriza via OAuth2.")
    p_acc_add.add_argument("--name", required=True, help="identificador da conta (ex.: pessoal).")
    p_acc_add.add_argument("--provider", default="gmail", choices=["gmail"], help="provedor.")
    p_acc_add.add_argument("--client-id", required=True, dest="client_id", help="OAuth2 client_id.")
    p_acc_add.add_argument("--client-secret", required=True, dest="client_secret", help="OAuth2 client_secret.")

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
        if args.comando == "accounts":
            if args.acc_comando == "list":
                return cmd_accounts_list(config)
            if args.acc_comando == "add":
                return cmd_accounts_add(config, args.name, args.provider, args.client_id, args.client_secret)
        if args.comando == "block":
            return cmd_block(config, args.valor, args.tipo)
        if args.comando == "allow":
            return cmd_allow(config, args.valor, args.tipo)
    except ConnectionRefusedError as e:
        # Bridge fora / ainda subindo: falha temporária, não erro de verdade.
        # 75 = EX_TEMPFAIL; o apolo.service o lista em SuccessExitStatus pra não
        # marcar a unidade como 'failed' — a próxima passada do timer reentará.
        print(f"Bridge indisponível: {e}", file=sys.stderr)
        return 75
    except Exception as e:
        print(f"erro: {e}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
