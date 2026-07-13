"""Entrada única do Apolo.

`apolo run` varre e classifica; `apolo review` abre a TUI pra despachar a fila;
`apolo block`/`allow` editam as regras pelo terminal; `apolo rules` lista o
config; `apolo status` mostra os contadores; `apolo setup` instala o timer do
systemd. (undo chega depois.)
"""

import argparse
import logging
import shutil
import sys
import tomllib
from pathlib import Path

from apolo.ai.ollama import OllamaClient
from apolo.clean import clean_for_classification, message_to_text
from apolo.config import AccountConfig, Config, load_accounts
from apolo.fetch.imap import BridgeClient
from apolo.logging_setup import current_log_path
from apolo.notify import notify
from apolo.rules.engine import RuleEngine, acao_efetiva, eh_recente
from apolo.rules.writer import add_rule_entry, detect_tipo
from apolo.storage.db import STATUS_AGUARDANDO, STATUS_CLASSIFICADO, Storage
from apolo.verify import VerifyConfig, apply_ia_decision

logger = logging.getLogger("apolo.cli")


def _gmail_run(
    config: Config,
    account: AccountConfig,
    store: Storage,
    engine: RuleEngine,
    ollama: OllamaClient,
    ai_ready: bool,
    verify_config: VerifyConfig,
) -> tuple[int, int, int, int, dict]:
    """Roda fetch+classifica pra uma conta Gmail. Retorna (total_novos, analisados, revisar, preservados, acoes)."""
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
        return 0, 0, 0, 0, {}

    total_novos = analisados = revisar = preservados = 0
    acoes: dict[str, int] = {}

    for pasta in account.folders:
        meta_key = f"{conta_id}:{pasta}"
        meta = store.get_folder_meta(meta_key)
        last_uid = meta[1] if meta else 0

        result = client.fetch_new(pasta, last_uid)

        pasta_db = f"{conta_id}:{pasta}"  # namespace único no DB

        # Decisões já tomadas (por message_id) preservadas através do resync.
        decididos: dict[str, str] = {}
        if result.resynced and meta is not None:
            print(f"[{conta_id}/{pasta}] resync completo.")
            decididos = store.decided_message_ids(pasta_db)  # ANTES de apagar
            store.reset_folder(meta_key)

        novos_pasta = 0
        residuo = []

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

            # Já despachado antes (sobreviveu ao resync via message_id): não
            # re-enfileira.
            mid = (m.message_id or "").strip()
            if mid and mid in decididos:
                store.mark_dispatched(
                    pasta=pasta_db,
                    uidvalidity=result.uidvalidity,
                    uid=m.uid,
                    acao_aplicada=decididos[mid] or "manter",
                )
                preservados += 1
                continue

            decisao = engine.classify(
                remetente=m.remetente,
                assunto=m.assunto,
                list_unsubscribe=m.list_unsubscribe,
            )
            novo_status = STATUS_AGUARDANDO if decisao.precisa_revisao else STATUS_CLASSIFICADO
            analisados += 1
            if novo_status == STATUS_AGUARDANDO:
                revisar += 1
            recente = eh_recente(m.data, config.ai_max_dias)
            efetiva = acao_efetiva(decisao, ai_ready, recente)
            store.classify_email(
                pasta=pasta_db,
                uidvalidity=result.uidvalidity,
                uid=m.uid,
                status=novo_status,
                categoria=decisao.categoria,
                acao_sugerida=efetiva,
                regra_casada=decisao.regra_casada,
            )
            acoes[efetiva] = acoes.get(efetiva, 0) + 1
            if decisao.regra_casada == "default" and recente:
                residuo.append(m)

        if ai_ready and residuo:
            n_ia = _ai_pass_gmail(client, store, ollama, verify_config, pasta_db, result.uidvalidity, residuo)
            print(f"[{conta_id}/{pasta}] IA classificou {n_ia}/{len(residuo)} do resíduo.")

        # Reconciliação: pendentes que já saíram da INBOX por fora do Apolo
        # (lixeira/arquivado direto no Gmail) saem da fila aqui. Pulado num
        # resync: reset_folder já zerou o estado da pasta.
        if not result.resynced:
            label = "INBOX" if pasta.upper() == "INBOX" else pasta
            try:
                removidos = _reconciliar_gmail(client, store, pasta_db, label)
            except Exception as e:
                logger.warning(
                    "[%s/%s] reconciliação Gmail falhou; mantendo pendentes como estão: %s: %s",
                    conta_id, pasta, type(e).__name__, e,
                )
            else:
                if removidos:
                    print(f"[{conta_id}/{pasta}] {removidos} email(s) saíram da INBOX por fora — removido(s) da fila.")

        store.set_folder_meta(meta_key, result.uidvalidity, result.ultimo_uid)
        total_novos += novos_pasta
        print(f"[{conta_id}/{pasta}] {novos_pasta} novo(s) (historyId={result.ultimo_uid}).")

    return total_novos, analisados, revisar, preservados, acoes


def _imap_account_run(
    config: Config,
    account: AccountConfig,
    store: Storage,
    engine: RuleEngine,
    ollama: OllamaClient,
    ai_ready: bool,
    verify_config: VerifyConfig,
) -> tuple[int, int, int, int, dict]:
    """Roda fetch+classifica incremental pra uma conta IMAP adicional (ex.:
    Outlook). Mesma lógica do bloco Proton em `cmd_run`, mas com login e
    pastas namespaced por conta — ver `_sync_imap_pasta` em apolo.sync pro
    equivalente do sync completo ("Sincronizar" da UI)."""
    from apolo import secrets

    conta_id = f"imap:{account.name}"
    senha = secrets.lookup_account_password(conta_id)
    if not (account.host and account.username and senha):
        print(f"[{conta_id}] credenciais incompletas — configure via: apolo accounts add --provider imap --name {account.name} …")
        return 0, 0, 0, 0, {}

    total_novos = analisados = revisar = preservados = 0
    acoes: dict[str, int] = {}

    with BridgeClient(account.host, account.port, account.security) as client:
        client.login(account.username, senha)
        for pasta in account.folders:
            pasta_db = f"{conta_id}:{pasta}"
            meta = store.get_folder_meta(pasta_db)
            known_uidvalidity = meta[0] if meta else None
            last_uid = meta[1] if meta else 0

            result = client.fetch_new(pasta, known_uidvalidity, last_uid)

            decididos: dict[str, str] = {}
            if result.resynced and meta is not None:
                print(f"[{conta_id}/{pasta}] UIDVALIDITY mudou — ressincronizando.")
                decididos = store.decided_message_ids(pasta_db)
                store.reset_folder(pasta_db)

            novos_pasta = 0
            residuo = []
            for m in result.novos:
                if store.insert_email(
                    conta=conta_id, pasta=pasta_db, uidvalidity=result.uidvalidity, uid=m.uid,
                    message_id=m.message_id, remetente=m.remetente, assunto=m.assunto, data=m.data,
                ):
                    novos_pasta += 1

                mid = (m.message_id or "").strip()
                if mid and mid in decididos:
                    store.mark_dispatched(
                        pasta=pasta_db, uidvalidity=result.uidvalidity, uid=m.uid,
                        acao_aplicada=decididos[mid] or "manter",
                    )
                    preservados += 1
                    continue

                decisao = engine.classify(
                    remetente=m.remetente, assunto=m.assunto, list_unsubscribe=m.list_unsubscribe,
                )
                novo_status = STATUS_AGUARDANDO if decisao.precisa_revisao else STATUS_CLASSIFICADO
                analisados += 1
                if novo_status == STATUS_AGUARDANDO:
                    revisar += 1
                recente = eh_recente(m.data, config.ai_max_dias)
                efetiva = acao_efetiva(decisao, ai_ready, recente)
                store.classify_email(
                    pasta=pasta_db, uidvalidity=result.uidvalidity, uid=m.uid,
                    status=novo_status, categoria=decisao.categoria,
                    acao_sugerida=efetiva, regra_casada=decisao.regra_casada,
                )
                acoes[efetiva] = acoes.get(efetiva, 0) + 1
                if decisao.regra_casada == "default" and recente:
                    residuo.append(m)

            if ai_ready and residuo:
                n_ia = _ai_pass(client, store, ollama, verify_config, pasta_db, result.uidvalidity, residuo)
                print(f"[{conta_id}/{pasta}] IA classificou {n_ia}/{len(residuo)} do resíduo.")

            if not result.resynced:
                removidos = _reconciliar_imap(client, store, pasta_db, pasta)
                if removidos:
                    print(f"[{conta_id}/{pasta}] {removidos} email(s) saíram da pasta por fora — removido(s) da fila.")

            store.set_folder_meta(pasta_db, result.uidvalidity, result.ultimo_uid)
            total_novos += novos_pasta
            print(f"[{conta_id}/{pasta}] {novos_pasta} novo(s) (UID até {result.ultimo_uid}).")

    return total_novos, analisados, revisar, preservados, acoes


def _ai_pass_gmail(gmail_client, store, ollama, verify_config, pasta_db, uidvalidity, residuo) -> int:
    classificados = 0
    for m in residuo:
        try:
            trecho = gmail_client.fetch_message(m.provider_id) if m.provider_id else ""
            trecho = clean_for_classification(trecho)
            decisao = apply_ia_decision(
                ollama, verify_config, assunto=m.assunto, remetente=m.remetente, trecho=trecho
            )
        except Exception as e:
            logger.warning("[%s] IA falhou pro UID %d: %s", pasta_db, m.uid, e)
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


def _retry_stuck_ai(
    config: Config,
    store: Storage,
    ollama: OllamaClient,
    verify_config: VerifyConfig,
    *,
    client: BridgeClient | None = None,
) -> int:
    """Reclassifica pelo Ollama pendentes que a cascata deixou em 'default' mas
    que nunca chegaram a passar pela IA (ver Storage.stuck_default_rows).

    Reusa a conexão IMAP já aberta se `client` for passado (chamada dentro de
    cmd_run); senão abre a dela própria (chamada avulsa, ex.: botão do Hub).
    """
    rows = store.stuck_default_rows()
    if not rows:
        logger.info("retry_stuck_ai: nenhum pendente preso.")
        return 0

    proton_rows = [r for r in rows if r["conta"] == "proton"]
    gmail_rows = [r for r in rows if r["conta"].startswith("gmail:")]
    imap_rows = [r for r in rows if r["conta"].startswith("imap:")]
    logger.info("retry_stuck_ai: %d pendente(s) preso(s) (%d proton, %d gmail, %d imap).",
                len(rows), len(proton_rows), len(gmail_rows), len(imap_rows))

    total = 0
    if proton_rows:
        if client is not None:
            total += _retry_proton_rows(client, store, ollama, verify_config, proton_rows)
        else:
            config.require_credentials()
            with BridgeClient(config.imap_host, config.imap_port, config.imap_security) as c:
                c.login(config.username, config.password)
                total += _retry_proton_rows(c, store, ollama, verify_config, proton_rows)

    if gmail_rows:
        accounts_by_name = {
            a.name: a for a in load_accounts(config.accounts_path) if a.provider == "gmail"
        }
        total += _retry_gmail_rows(config, store, ollama, verify_config, gmail_rows, accounts_by_name)

    if imap_rows:
        imap_accounts_by_name = {
            a.name: a for a in load_accounts(config.accounts_path) if a.provider == "imap"
        }
        total += _retry_imap_account_rows(store, ollama, verify_config, imap_rows, imap_accounts_by_name)
    logger.info("retry_stuck_ai: %d/%d reclassificado(s).", total, len(rows))

    return total


def _retry_proton_rows(client, store: Storage, ollama: OllamaClient, verify_config: VerifyConfig, rows) -> int:
    n = 0
    for r in rows:
        try:
            msg = client.fetch_message_from(r["pasta"], r["uid"])
            trecho = clean_for_classification(message_to_text(msg)) if msg else ""
            decisao = apply_ia_decision(
                ollama, verify_config, assunto=r["assunto"], remetente=r["remetente"], trecho=trecho
            )
        except Exception as e:
            logger.warning("[%s] IA (retry) falhou pro UID %d: %s", r["pasta"], r["uid"], e)
            decisao = None
        if decisao is None:
            continue
        store.classify_email(
            pasta=r["pasta"], uidvalidity=r["uidvalidity"], uid=r["uid"],
            status=STATUS_AGUARDANDO, categoria=decisao.categoria,
            acao_sugerida=decisao.acao, regra_casada=f"ia:{decisao.categoria}",
        )
        n += 1
    return n


def _retry_gmail_rows(
    config: Config, store: Storage, ollama: OllamaClient, verify_config: VerifyConfig, rows, accounts_by_name
) -> int:
    from apolo.fetch.gmail import GmailClient

    clients: dict[str, GmailClient] = {}
    n = 0
    for r in rows:
        name = r["conta"].removeprefix("gmail:")
        if name not in clients:
            account = accounts_by_name.get(name)
            if account is None:
                logger.warning("retry gmail: conta %r não encontrada em accounts.toml — pulando.", name)
                continue
            clients[name] = GmailClient(
                name, account.client_id, account.client_secret,
                config.tokens_dir / f"{name}.json",
            )
        gclient = clients[name]
        if not r["provider_id"]:
            logger.warning("retry gmail: %s UID %d sem provider_id — pulando.", r["pasta"], r["uid"])
            continue
        try:
            trecho = clean_for_classification(gclient.fetch_message(r["provider_id"]))
            decisao = apply_ia_decision(
                ollama, verify_config, assunto=r["assunto"], remetente=r["remetente"], trecho=trecho
            )
        except Exception as e:
            logger.warning("[%s] IA (retry gmail) falhou pro UID %d: %s", r["pasta"], r["uid"], e)
            decisao = None
        if decisao is None:
            continue
        store.classify_email(
            pasta=r["pasta"], uidvalidity=r["uidvalidity"], uid=r["uid"],
            status=STATUS_AGUARDANDO, categoria=decisao.categoria,
            acao_sugerida=decisao.acao, regra_casada=f"ia:{decisao.categoria}",
        )
        n += 1
    return n


def _retry_imap_account_rows(
    store: Storage, ollama: OllamaClient, verify_config: VerifyConfig, rows, accounts_by_name
) -> int:
    from apolo import secrets

    n = 0
    by_name: dict[str, list] = {}
    for r in rows:
        by_name.setdefault(r["conta"].removeprefix("imap:"), []).append(r)

    for name, name_rows in by_name.items():
        account = accounts_by_name.get(name)
        if account is None:
            logger.warning("retry imap: conta %r não encontrada em accounts.toml — pulando.", name)
            continue
        senha = secrets.lookup_account_password(f"imap:{name}")
        if not (account.host and account.username and senha):
            logger.warning("retry imap: conta %r sem credenciais completas — pulando.", name)
            continue
        # Cada linha tem a pasta namespaced ("imap:<nome>:<pasta>") gravada
        # como `pasta` no banco; `fetch_message_from` precisa do nome real da
        # pasta IMAP, então destrinchamos o prefixo da conta pra buscar, mas
        # gravamos de volta com a chave namespaced (igual ao resto do banco).
        prefixo = f"imap:{name}:"
        try:
            with BridgeClient(account.host, account.port, account.security) as client:
                client.login(account.username, senha)
                for r in name_rows:
                    pasta_real = r["pasta"].removeprefix(prefixo)
                    try:
                        msg = client.fetch_message_from(pasta_real, r["uid"])
                        trecho = clean_for_classification(message_to_text(msg)) if msg else ""
                        decisao = apply_ia_decision(
                            ollama, verify_config, assunto=r["assunto"], remetente=r["remetente"], trecho=trecho
                        )
                    except Exception as e:
                        logger.warning("[%s] IA (retry) falhou pro UID %d: %s", r["pasta"], r["uid"], e)
                        decisao = None
                    if decisao is None:
                        continue
                    store.classify_email(
                        pasta=r["pasta"], uidvalidity=r["uidvalidity"], uid=r["uid"],
                        status=STATUS_AGUARDANDO, categoria=decisao.categoria,
                        acao_sugerida=decisao.acao, regra_casada=f"ia:{decisao.categoria}",
                    )
                    n += 1
        except Exception as e:
            logger.warning("retry imap: conta %r falhou: %s", name, e)
    return n


def cmd_retry_ia(config: Config) -> int:
    """Chamada avulsa: só reclassifica pendentes presos, sem buscar emails novos."""
    ollama = OllamaClient(config.ollama_url, config.ollama_model, keep_alive=config.ollama_keep_alive)
    if not ollama.available():
        print("Ollama indisponível — nada a fazer.")
        return 0
    verify_config = VerifyConfig.from_file(config.rules_path)
    with Storage(config.db_path) as store:
        n = _retry_stuck_ai(config, store, ollama, verify_config)
    ollama.unload()
    print(f"{n} pendente(s) reclassificado(s).")
    return 0


def cmd_run(config: Config, notify_enabled: bool = True) -> int:
    """Uma passada: busca os novos, classifica pela cascata e manda o resíduo pra IA."""
    logger.info("cmd_run: início (pastas=%s).", config.folders)
    config.require_credentials()
    engine = RuleEngine.from_file(config.rules_path)
    verify_config = VerifyConfig.from_file(config.rules_path)

    # A IA é opcional: se o Ollama estiver fora, o resíduo só fica em 'revisar'.
    ollama = OllamaClient(
        config.ollama_url, config.ollama_model, keep_alive=config.ollama_keep_alive
    )
    ai_ready = config.ai_enabled and ollama.available()
    if config.ai_enabled and not ai_ready:
        logger.warning("Ollama indisponível — resíduo fica como 'revisar'.")
        print("IA indisponível (Ollama fora do ar) — resíduo fica como 'revisar'.")

    # "Analisando…" só abre DEPOIS que o Bridge conecta (ver dentro do `with`):
    # com o Bridge fora, o __enter__ levanta antes e nada aparece na tela. O
    # resumo no fim substitui esta (replace_id), então fica uma só por execução.
    nid: int | None = None
    total_novos = 0
    analisados = 0  # todos os emails percorridos nesta passada (pro resumo)
    revisar = 0  # foram pra fila (aguardando)
    mantidos = 0  # terminaram como 'classificado' (sem ação)
    preservados = 0  # já decididos antes; carregados sem voltar pra fila (resync)
    fila_total = 0
    acoes: dict[str, int] = {}
    with Storage(config.db_path) as store:
        with BridgeClient(
            config.imap_host, config.imap_port, config.imap_security
        ) as client:
            # Bridge respondeu (o __enter__ conectou): agora vale avisar na tela.
            if notify_enabled:
                nid = notify("Apolo", "Analisando emails novos…", urgency="low")
            client.login(config.username, config.password)

            for pasta in config.folders:
                meta = store.get_folder_meta(pasta)
                known_uidvalidity = meta[0] if meta else None
                last_uid = meta[1] if meta else 0

                result = client.fetch_new(pasta, known_uidvalidity, last_uid)

                # Decisões já tomadas (por message_id) — preservadas se houver
                # resync, pra um email já despachado não voltar pra fila só
                # porque o Bridge trocou os UIDs.
                decididos: dict[str, str] = {}
                if result.resynced and meta is not None:
                    print(f"[{pasta}] UIDVALIDITY mudou — ressincronizando.")
                    decididos = store.decided_message_ids(pasta)  # ANTES de apagar
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

                    # Já despachado num ciclo anterior (sobreviveu ao resync via
                    # message_id): re-grava como despachado e NÃO re-enfileira.
                    mid = (m.message_id or "").strip()
                    if mid and mid in decididos:
                        store.mark_dispatched(
                            pasta=pasta,
                            uidvalidity=result.uidvalidity,
                            uid=m.uid,
                            acao_aplicada=decididos[mid] or "manter",
                        )
                        preservados += 1
                        continue

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
                    recente = eh_recente(m.data, config.ai_max_dias)
                    efetiva = acao_efetiva(decisao, ai_ready, recente)
                    store.classify_email(
                        pasta=pasta,
                        uidvalidity=result.uidvalidity,
                        uid=m.uid,
                        status=novo_status,
                        categoria=decisao.categoria,
                        acao_sugerida=efetiva,
                        regra_casada=decisao.regra_casada,
                    )
                    acoes[efetiva] = acoes.get(efetiva, 0) + 1
                    if decisao.regra_casada == "default" and recente:
                        residuo.append(m)

                # 2ª passada: só o resíduo vai pro Ollama (já quente). A pasta
                # segue selecionada do fetch_new, então fetch_message funciona.
                if ai_ready and residuo:
                    n_ia = _ai_pass(client, store, ollama, verify_config, pasta, result.uidvalidity, residuo)
                    print(f"[{pasta}] IA classificou {n_ia}/{len(residuo)} do resíduo.")

                # Reconciliação: o que ainda está pendente no banco mas já saiu
                # da pasta de origem (lixeira/movido por fora do Apolo) sai da
                # fila aqui — não faz sentido revisar algo que já não está mais
                # lá. Pulado num resync: reset_folder já zerou o estado da pasta.
                if not result.resynced:
                    removidos = _reconciliar_imap(client, store, pasta)
                    if removidos:
                        print(f"[{pasta}] {removidos} email(s) saíram da pasta por fora — removido(s) da fila.")

                store.set_folder_meta(pasta, result.uidvalidity, result.ultimo_uid)
                total_novos += novos_pasta
                print(f"[{pasta}] {novos_pasta} novo(s) (UID até {result.ultimo_uid}).")

        # Contas Gmail adicionais (mesma conexão)
        gmail_accounts = [a for a in load_accounts(config.accounts_path) if a.provider == "gmail"]
        for account in gmail_accounts:
            gn, ga, gr, gp, gacoes = _gmail_run(config, account, store, engine, ollama, ai_ready, verify_config)
            total_novos += gn
            analisados += ga
            revisar += gr
            preservados += gp
            for k, v in gacoes.items():
                acoes[k] = acoes.get(k, 0) + v

        # Contas IMAP adicionais (ex.: Outlook) — conexão própria por conta.
        imap_accounts = [a for a in load_accounts(config.accounts_path) if a.provider == "imap"]
        for account in imap_accounts:
            try:
                iN, iA, iR, iP, iacoes = _imap_account_run(config, account, store, engine, ollama, ai_ready, verify_config)
            except Exception as e:
                logger.exception("sync incremental de imap:%s falhou", account.name)
                print(f"[imap:{account.name}] erro: {e}")
                continue
            total_novos += iN
            analisados += iA
            revisar += iR
            preservados += iP
            for k, v in iacoes.items():
                acoes[k] = acoes.get(k, 0) + v

        # Pendentes presos numa passada anterior sem IA (Ollama estava fora do
        # ar naquela hora): tenta de novo agora que ele já respondeu acima.
        retried = 0
        if ai_ready:
            retried = _retry_stuck_ai(config, store, ollama, verify_config)
            if retried:
                print(f"IA reclassificou {retried} pendente(s) que ainda não tinham passado por ela.")

        fila_total = store.count_queue()

    if ai_ready:
        ollama.unload()

    logger.info(
        "cmd_run: fim — %d novo(s), %d preservado(s), %d na fila, ações=%s.",
        total_novos, preservados, fila_total, acoes,
    )
    print(f"\n{total_novos} email(s) novo(s).")
    if preservados:
        print(f"{preservados} já decidido(s) antes — preservado(s) no resync (não voltaram pra fila).")
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


def _ai_pass(client, store, ollama, verify_config, pasta, uidvalidity, residuo) -> int:
    """Classifica o resíduo pelo Ollama: corpo limpo -> sugestão. Falha por email é ignorada.

    A IA só enriquece a sugestão; o email continua na fila (aguardando) pro dono
    confirmar. Nada é executado aqui.
    """
    classificados = 0
    for m in residuo:
        try:
            msg = client.fetch_message(m.uid)
            trecho = clean_for_classification(message_to_text(msg)) if msg else ""
            decisao = apply_ia_decision(
                ollama, verify_config, assunto=m.assunto, remetente=m.remetente, trecho=trecho
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


def _reconciliar_imap(client, store, pasta_db: str, pasta: str | None = None) -> int:
    """Confere se os pendentes dessa pasta ainda existem lá; marca os sumidos.

    `pasta_db` é a chave no banco; `pasta` é o nome real da pasta IMAP pro
    UID SEARCH (default = `pasta_db`, caso do Proton sem namespace — contas
    IMAP adicionais namespaced passam os dois separados).  Um único UID SEARCH
    cobre o lote — sem custo por mensagem. Devolve quantos foram removidos.
    """
    pasta = pasta or pasta_db
    pendentes = store.pending_rows(pasta_db)
    if not pendentes:
        return 0
    presentes = client.uids_presentes(pasta, [r["uid"] for r in pendentes])
    removidos = 0
    for r in pendentes:
        if r["uid"] not in presentes:
            store.mark_removed(pasta=pasta_db, uidvalidity=r["uidvalidity"], uid=r["uid"])
            removidos += 1
    return removidos


def _reconciliar_gmail(client, store, pasta_db: str, label: str) -> int:
    """Mesma ideia da reconciliação IMAP, via labelIds do Gmail.

    Checa só o que está pendente no banco (não a INBOX inteira) — um GET por
    mensagem com format=minimal.
    """
    pendentes = [r for r in store.pending_rows(pasta_db) if r["provider_id"]]
    if not pendentes:
        return 0
    presentes = client.uids_presentes([r["provider_id"] for r in pendentes], label=label)
    removidos = 0
    for r in pendentes:
        if r["provider_id"] not in presentes:
            store.mark_removed(pasta=pasta_db, uidvalidity=r["uidvalidity"], uid=r["uid"])
            removidos += 1
    return removidos


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
    imap_accounts_by_name = {
        a.name: a for a in load_accounts(config.accounts_path) if a.provider == "imap"
    }
    contas_ativas = (
        {"proton"} | {f"gmail:{n}" for n in accounts_by_name} | {f"imap:{n}" for n in imap_accounts_by_name}
    )

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


def _write_accounts_toml(path: Path, accounts: list[dict]) -> None:
    """Serializa `[[accounts]]` genericamente — cada entrada escreve só as
    chaves que tem, então contas gmail (client_id/client_secret) e imap
    (host/port/…) convivem no mesmo arquivo sem campos vazios de sobra."""
    lines: list[str] = []
    for acc in accounts:
        if lines:
            lines.append("")
        lines.append("[[accounts]]")
        for key, val in acc.items():
            if isinstance(val, bool):
                lines.append(f'{key} = {"true" if val else "false"}')
            elif isinstance(val, str):
                lines.append(f'{key} = "{val}"')
            elif isinstance(val, list):
                lines.append(f'{key} = {val!r}')
            else:
                lines.append(f'{key} = {val}')
    path.write_text("\n".join(lines) + "\n", encoding="utf-8")


def cmd_accounts_add(
    config: Config, name: str, provider: str, *,
    client_id: str = "", client_secret: str = "",
    host: str = "", port: int = 993, security: str = "SSL",
    username: str = "", trash_folder: str = "Trash", folders: str = "INBOX", chunk_size: int = 50,
) -> int:
    """Adiciona uma conta ao accounts.toml.

    Gmail: inicia o fluxo de autorização OAuth2 (device flow, ver GmailClient).
    IMAP genérico (Outlook e afins): pede a senha (ou senha de app) por
    prompt e guarda no keyring — nunca no accounts.toml em texto puro.
    """
    import tomllib

    if provider == "gmail" and not (client_id and client_secret):
        print("erro: provider 'gmail' precisa de --client-id e --client-secret.")
        return 1
    if provider == "imap" and not (host and username):
        print("erro: provider 'imap' precisa de --host e --username.")
        return 1

    path = config.accounts_path
    path.parent.mkdir(parents=True, exist_ok=True)

    existing: dict = {}
    if path.is_file():
        path.chmod(0o600)
        with path.open("rb") as f:
            existing = tomllib.load(f)

    accounts = existing.get("accounts", [])
    folders_list = [f.strip() for f in folders.split(",") if f.strip()] or ["INBOX"]

    for acc in accounts:
        if acc.get("name") == name:
            print(f"Conta '{name}' já existe em {path}.")
            # Ainda permite reautorizar/regravar a senha abaixo.
            break
    else:
        entry: dict = {"name": name, "provider": provider}
        if provider == "gmail":
            entry["client_id"] = client_id
            entry["client_secret"] = client_secret
            entry["folders"] = folders_list
        elif provider == "imap":
            entry["host"] = host
            entry["port"] = port
            entry["security"] = security
            entry["username"] = username
            entry["trash_folder"] = trash_folder
            entry["chunk_size"] = chunk_size
            entry["folders"] = folders_list
        accounts.append(entry)
        _write_accounts_toml(path, accounts)
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
    elif provider == "imap":
        import getpass

        from apolo import secrets

        if not secrets.disponivel():
            print(
                "aviso: keyring (secret-tool/libsecret) indisponível — a senha "
                "não pode ser guardada. Instale libsecret e rode de novo."
            )
            return 0
        senha = getpass.getpass(f"Senha (ou senha de app) para {username}: ")
        if senha and secrets.store_account_password(f"imap:{name}", senha):
            print(f"Senha de '{name}' guardada no keyring.")
        else:
            print("aviso: não consegui guardar a senha no keyring.")
    return 0


def cmd_accounts_list(config: Config) -> int:
    from apolo import secrets

    accounts = load_accounts(config.accounts_path)
    if not accounts:
        print(f"Nenhuma conta em {config.accounts_path}")
        return 0
    for acc in accounts:
        if acc.provider == "gmail":
            token_path = config.tokens_dir / f"{acc.name}.json"
            status = "autorizada" if token_path.is_file() else "NÃO autorizada"
        elif acc.provider == "imap":
            tem_senha = secrets.lookup_account_password(f"imap:{acc.name}") is not None
            status = f"{acc.host}:{acc.port} ({acc.security}) — {'senha no keyring' if tem_senha else 'SEM senha'}"
        else:
            status = "?"
        print(f"  {acc.name}  ({acc.provider})  — {status}")
    return 0


def cmd_accounts_remove(config: Config, name: str) -> int:
    """Remove uma conta de accounts.toml. Gmail: apaga o token salvo; IMAP:
    apaga a senha do keyring. A fila/banco não são tocados — emails já
    processados dessa conta continuam no histórico."""
    import tomllib

    path = config.accounts_path
    if not path.is_file():
        print(f"Nenhuma conta em {path}.")
        return 1
    with path.open("rb") as f:
        existing = tomllib.load(f)

    accounts = existing.get("accounts", [])
    found = next((a for a in accounts if a.get("name") == name), None)
    if found is None:
        print(f"Conta '{name}' não encontrada em {path}.")
        return 1

    accounts = [a for a in accounts if a.get("name") != name]
    _write_accounts_toml(path, accounts)
    path.chmod(0o600)

    provider = found.get("provider")
    if provider == "gmail":
        token_path = config.tokens_dir / f"{name}.json"
        if token_path.is_file():
            token_path.unlink()
            print(f"Token de '{name}' removido.")
    elif provider == "imap":
        from apolo import secrets

        if secrets.clear_account_password(f"imap:{name}"):
            print(f"Senha de '{name}' removida do keyring.")

    print(f"Conta '{name}' removida de {path}.")
    return 0


def cmd_repair_gmail(config: Config) -> int:
    """Re-busca cabeçalhos (From/Subject/Date) de emails Gmail gravados com remetente vazio."""
    from apolo.fetch.gmail import GmailClient

    accounts = [a for a in load_accounts(config.accounts_path) if a.provider == "gmail"]
    if not accounts:
        print("Nenhuma conta Gmail configurada.")
        return 0

    total_corrigidos = 0
    total_falhas = 0

    with Storage(config.db_path) as store:
        for account in accounts:
            conta_prefix = f"gmail:{account.name}"
            rows = store.emails_sem_remetente(conta_prefix)
            if not rows:
                print(f"[{conta_prefix}] Nenhum email com remetente vazio.")
                continue

            print(f"[{conta_prefix}] {len(rows)} email(s) para reparar…")
            token_path = config.tokens_dir / f"{account.name}.json"
            client = GmailClient(
                account.name, account.client_id, account.client_secret, token_path
            )

            for row in rows:
                fetched = client._fetch_headers(row["provider_id"], client._ensure_token())
                if fetched is None:
                    print(f"  aviso: não consegui buscar {row['provider_id']!r}")
                    total_falhas += 1
                    continue
                store.update_email_headers(
                    pasta=row["pasta"],
                    uidvalidity=row["uidvalidity"],
                    uid=row["uid"],
                    remetente=fetched.remetente or "",
                    assunto=fetched.assunto or "",
                    data=fetched.data or "",
                )
                total_corrigidos += 1

    print(f"\nTotal: {total_corrigidos} corrigido(s), {total_falhas} falha(s).")
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
    sub.add_parser(
        "retry-ia",
        help="reclassifica pela IA pendentes presos em 'default' que nunca passaram por ela (sem buscar emails novos).",
    )

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
    p_acc_remove = accsub.add_parser("remove", help="remove conta (Gmail: apaga token; IMAP: apaga senha do keyring).")
    p_acc_remove.add_argument("--name", required=True, help="identificador da conta a remover.")
    p_acc_add = accsub.add_parser("add", help="adiciona conta (gmail via OAuth2, ou imap genérico — ex.: Outlook).")
    p_acc_add.add_argument("--name", required=True, help="identificador da conta (ex.: pessoal).")
    p_acc_add.add_argument("--provider", default="gmail", choices=["gmail", "imap"], help="provedor.")
    p_acc_add.add_argument("--client-id", dest="client_id", default="", help="OAuth2 client_id (provider gmail).")
    p_acc_add.add_argument("--client-secret", dest="client_secret", default="", help="OAuth2 client_secret (provider gmail).")
    p_acc_add.add_argument("--host", default="", help="host IMAP, ex.: outlook.office365.com (provider imap).")
    p_acc_add.add_argument("--port", type=int, default=993, help="porta IMAP (provider imap; padrão 993).")
    p_acc_add.add_argument(
        "--security", default="SSL", choices=["SSL", "STARTTLS"],
        help="segurança IMAP: SSL direto (porta 993) ou STARTTLS (provider imap; padrão SSL).",
    )
    p_acc_add.add_argument("--username", default="", help="usuário/email de login (provider imap).")
    p_acc_add.add_argument(
        "--trash-folder", dest="trash_folder", default="Trash",
        help='pasta de lixeira (provider imap; Outlook costuma ser "Deleted Items").',
    )
    p_acc_add.add_argument(
        "--chunk-size", dest="chunk_size", type=int, default=50,
        help="tamanho do lote de COPY/STORE ao mover pra lixeira (provider imap; padrão 50).",
    )
    p_acc_add.add_argument("--folders", default="INBOX", help="pastas a vigiar, separadas por vírgula.")

    sub.add_parser(
        "repair-gmail",
        help="re-busca cabeçalhos (remetente/assunto) de emails Gmail gravados com dados vazios.",
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
        if args.comando == "retry-ia":
            return cmd_retry_ia(config)
        if args.comando == "accounts":
            if args.acc_comando == "list":
                return cmd_accounts_list(config)
            if args.acc_comando == "remove":
                return cmd_accounts_remove(config, args.name)
            if args.acc_comando == "add":
                return cmd_accounts_add(
                    config, args.name, args.provider,
                    client_id=args.client_id, client_secret=args.client_secret,
                    host=args.host, port=args.port, security=args.security,
                    username=args.username, trash_folder=args.trash_folder, folders=args.folders,
                    chunk_size=args.chunk_size,
                )
        if args.comando == "repair-gmail":
            return cmd_repair_gmail(config)
        if args.comando == "block":
            return cmd_block(config, args.valor, args.tipo)
        if args.comando == "allow":
            return cmd_allow(config, args.valor, args.tipo)
    except ConnectionRefusedError as e:
        # Bridge fora / ainda subindo: falha temporária, não erro de verdade.
        # 75 = EX_TEMPFAIL; o apolo.service o lista em SuccessExitStatus pra não
        # marcar a unidade como 'failed' — a próxima passada do timer reentará.
        logger.warning("Bridge indisponível: %s", e)
        print(f"Bridge indisponível: {e}", file=sys.stderr)
        print(f"log: {current_log_path()}", file=sys.stderr)
        return 75
    except Exception as e:
        logger.exception("comando %r falhou", args.comando)
        print(f"erro: {e}", file=sys.stderr)
        print(f"log: {current_log_path()}", file=sys.stderr)
        return 1
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
