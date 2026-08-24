"""Sincronização completa sob demanda — botão "Sincronizar" da UI.

Diferente do `cmd_run` incremental (só o delta desde o último UID/historyId,
que no Gmail ficava travado nas últimas 50 mensagens do primeiro sync), aqui a
pasta inteira é revarrida até `limit` mensagens mais recentes por conta/pasta.

Não é destrutivo: UIDs que já existem no banco são ignorados (via
`Storage.email_exists`) — só o que faltava é inserido, passa pela cascata de
regras e, se ela não resolver, vai pro Ollama. `on_event` deixa quem chamar
(a QueueScreen, via bind "S") acompanhar cada email em tempo real: aparece na
lista assim que a cascata decide, e se depender da IA, passa por "analisando"
até a resposta — sem travar a tela, é um worker em thread separada.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from typing import Callable

from apolo.actions import DispatchItem, dispatch_lixeira_gmail, dispatch_lixeira_imap
from apolo.ai.ollama import OllamaClient
from apolo.clean import clean_for_classification, message_to_text
from apolo.config import Config, load_accounts
from apolo.fetch.imap import BridgeClient
from apolo.rules.engine import (
    ACAO_LIXEIRA,
    ACAO_REVISAR,
    RuleEngine,
    acao_efetiva,
    descartar_codigo_lido,
    eh_recente,
)
from apolo.storage.db import STATUS_AGUARDANDO, STATUS_CLASSIFICADO, Storage
from apolo.verify import VerifyConfig, apply_ia_decision

logger = logging.getLogger("apolo.sync")

OnEvent = Callable[..., None]

# Ver MAX_FALHAS_SEGUIDAS em apolo.fetch.imap: mesmo raciocínio aqui — parar
# depois de falhas seguidas em vez de estourar e perder o residuo já achado.
MAX_FALHAS_SEGUIDAS = 5


@dataclass
class SyncItem:
    """Um email descoberto pelo sync — estado exibido ao vivo na fila de revisão."""

    conta: str
    pasta: str
    uidvalidity: int
    uid: int
    message_id: str
    remetente: str
    assunto: str
    data: str
    status: str  # acao_sugerida da cascata; vira "analisando" e depois a acao final
    categoria: str = ""
    sera_analisado: bool = False  # regra_casada == "default": ainda vai pro Ollama
    provider_id: str | None = None
    favorito: bool = False


def run_sync(
    config: Config, *, limit: int, on_event: OnEvent,
    skip_contas: set[str] | None = None, only_conta: str | None = None,
) -> None:
    """Varre as contas vinculadas e classifica o que ainda não estava no banco.

    Erros por conta/pasta (Bridge fora, conta Gmail não autorizada, etc.) são
    reportados via `on_event("erro", ...)` e não interrompem as demais contas.
    `skip_contas` (ids tipo "gmail:<nome>") pula contas já sabidamente com
    credencial inválida — a checagem de token da abertura do app alimenta isso.
    `only_conta` restringe a UMA conta ("proton" ou "gmail:<nome>") — é o filtro
    de conta (⇥) da fila de revisão; None varre todas.
    """
    engine = RuleEngine.from_file(config.rules_path)
    verify_config = VerifyConfig.from_file(config.rules_path)
    ollama = OllamaClient(config.ollama_url, config.ollama_model, keep_alive=config.ollama_keep_alive)
    ai_ready = config.ai_enabled and ollama.available()

    with Storage(config.db_path) as store:
        if only_conta in (None, "proton"):
            try:
                config.require_credentials()
                with BridgeClient(config.imap_host, config.imap_port, config.imap_security) as client:
                    client.login(config.username, config.password)
                    for pasta in config.folders:
                        _sync_imap_pasta(
                            store, engine, ollama, verify_config, ai_ready, client, pasta, limit, on_event,
                            ai_max_dias=config.ai_max_dias, trash_folder=config.trash_folder,
                        )
            except Exception as exc:
                logger.exception("sync completo do Proton (pasta 'proton') falhou")
                on_event("erro", conta="proton", pasta="", msg=str(exc))

        accounts = [a for a in load_accounts(config.accounts_path) if a.provider == "gmail"]
        for account in accounts:
            conta_id = f"gmail:{account.name}"
            if only_conta is not None and conta_id != only_conta:
                continue
            if skip_contas and conta_id in skip_contas:
                logger.info("pulando %s — credencial marcada como inválida.", conta_id)
                on_event("erro", conta=conta_id, pasta="",
                         msg="ignorada: token inválido — reautorize em Configurar Gmail")
                continue
            for pasta in account.folders:
                try:
                    _sync_gmail_pasta(
                        config, store, engine, ollama, verify_config, ai_ready, account, pasta, limit, on_event,
                        ai_max_dias=config.ai_max_dias,
                    )
                except Exception as exc:
                    logger.exception("sync completo de gmail:%s/%s falhou", account.name, pasta)
                    on_event("erro", conta=f"gmail:{account.name}", pasta=pasta, msg=str(exc))

        imap_accounts = [a for a in load_accounts(config.accounts_path) if a.provider == "imap"]
        for account in imap_accounts:
            conta_id = f"imap:{account.name}"
            if only_conta is not None and conta_id != only_conta:
                continue
            if skip_contas and conta_id in skip_contas:
                logger.info("pulando %s — credencial marcada como inválida.", conta_id)
                on_event("erro", conta=conta_id, pasta="", msg="ignorada: credencial inválida")
                continue
            from apolo import secrets

            senha = secrets.lookup_account_password(conta_id)
            if not (account.host and account.username and senha):
                logger.warning("%s: credenciais incompletas — pulando.", conta_id)
                on_event("erro", conta=conta_id, pasta="", msg="credenciais incompletas — configure via 'apolo accounts add'")
                continue
            try:
                with BridgeClient(account.host, account.port, account.security) as client:
                    client.login(account.username, senha)
                    for pasta in account.folders:
                        _sync_imap_pasta(
                            store, engine, ollama, verify_config, ai_ready, client, pasta, limit, on_event,
                            ai_max_dias=config.ai_max_dias,
                            conta=conta_id, pasta_db=f"{conta_id}:{pasta}",
                            trash_folder=account.trash_folder, chunk_size=account.chunk_size,
                        )
            except Exception as exc:
                logger.exception("sync completo de %s falhou", conta_id)
                on_event("erro", conta=conta_id, pasta="", msg=str(exc))

    if ai_ready:
        ollama.unload()

    on_event("fim")


def _classificar_novo(engine, remetente, assunto, list_unsubscribe):
    decisao = engine.classify(remetente=remetente, assunto=assunto, list_unsubscribe=list_unsubscribe)
    novo_status = STATUS_AGUARDANDO if decisao.precisa_revisao else STATUS_CLASSIFICADO
    return decisao, novo_status


def refresh_favoritos_imap(client, store, pasta_db: str, pasta: str) -> int:
    """Reconfere \\Flagged dos e-mails ainda pendentes nessa pasta.

    O favorito só é capturado no FETCH do insert original (ver
    `BridgeClient._fetch_headers`) — quem favorita um e-mail DEPOIS que o
    Apolo já o sincronizou ficaria com o estado velho pra sempre sem isso.
    Um único UID SEARCH FLAGGED cobre a pasta inteira, então o custo não
    cresce com o tamanho da fila pendente. Devolve quantos mudaram.
    """
    pendentes = store.pending_rows(pasta_db)
    if not pendentes:
        return 0
    flagged = client.flagged_uids(pasta)
    atualizados = 0
    for r in pendentes:
        novo = r["uid"] in flagged
        if bool(r["favorito"]) != novo:
            store.update_favorito(pasta=pasta_db, uidvalidity=r["uidvalidity"], uid=r["uid"], favorito=novo)
            atualizados += 1
    return atualizados


def refresh_favoritos_gmail(client, store, pasta_db: str, pasta: str) -> int:
    """Mesma ideia de `refresh_favoritos_imap`, via label STARRED do Gmail."""
    pendentes = [r for r in store.pending_rows(pasta_db) if r["provider_id"]]
    if not pendentes:
        return 0
    starred = client.starred_ids(pasta)
    atualizados = 0
    for r in pendentes:
        novo = r["provider_id"] in starred
        if bool(r["favorito"]) != novo:
            store.update_favorito(pasta=pasta_db, uidvalidity=r["uidvalidity"], uid=r["uid"], favorito=novo)
            atualizados += 1
    return atualizados


def _sync_imap_pasta(
    store, engine, ollama, verify_config, ai_ready, client, pasta, limit, on_event, *,
    ai_max_dias: int = 90, conta: str = "proton", pasta_db: str | None = None,
    trash_folder: str = "Trash", chunk_size: int = 300,
) -> None:
    """Sync completo de uma pasta IMAP. `conta`/`pasta_db` default pro caso do
    Proton (sem namespace); contas IMAP adicionais passam `conta="imap:<nome>"`
    e `pasta_db="imap:<nome>:<pasta>"` pra não colidir no banco (ver
    `_sync_gmail_pasta`, mesma ideia)."""
    pasta_db = pasta_db or pasta
    uids, uidvalidity = client.list_uids(pasta, limit)
    novos_uids = [u for u in uids if not store.email_exists(pasta_db, uidvalidity, u)]
    logger.info("[%s/%s] %d UID(s) novo(s) a buscar de %d candidato(s).", conta, pasta, len(novos_uids), len(uids))
    on_event("found", conta=conta, pasta=pasta_db, total=len(novos_uids))

    residuo = []
    auto_lixeira_itens: list[DispatchItem] = []
    falhas_seguidas = 0
    for uid in novos_uids:
        try:
            m = client.fetch_header(uid)
        except Exception as e:
            falhas_seguidas += 1
            logger.warning("[%s/%s] falha ao buscar header do UID %d (%d seguida(s)): %s",
                           conta, pasta, uid, falhas_seguidas, e)
            if falhas_seguidas >= MAX_FALHAS_SEGUIDAS:
                logger.warning("[%s/%s] parando cedo após falhas seguidas — retomando no próximo ciclo.", conta, pasta)
                on_event("erro", conta=conta, pasta=pasta_db, msg=f"parou após falhas seguidas: {e}")
                break
            continue
        falhas_seguidas = 0
        if m is None:
            logger.debug("[%s/%s] UID %d sem resposta OK do FETCH — ignorado.", conta, pasta, uid)
            continue
        store.insert_email(
            conta=conta, pasta=pasta_db, uidvalidity=uidvalidity, uid=m.uid,
            message_id=m.message_id, remetente=m.remetente, assunto=m.assunto, data=m.data,
            favorito=m.favorito,
        )
        decisao, novo_status = _classificar_novo(engine, m.remetente, m.assunto, m.list_unsubscribe)
        recente = eh_recente(m.data, ai_max_dias)
        efetiva = acao_efetiva(decisao, ai_ready, recente)
        efetiva = descartar_codigo_lido(decisao, efetiva, m.lido)
        if m.favorito and efetiva == ACAO_LIXEIRA:
            # Favoritado no Proton/Gmail: nunca some sozinho pelo auto-envio —
            # cai pra revisão manual, onde o dono vê o aviso antes de excluir.
            efetiva = ACAO_REVISAR
        store.classify_email(
            pasta=pasta_db, uidvalidity=uidvalidity, uid=m.uid,
            status=novo_status, categoria=decisao.categoria,
            acao_sugerida=efetiva, regra_casada=decisao.regra_casada,
        )
        if efetiva == ACAO_LIXEIRA:
            # Cascata determinística já decidiu sozinha (o ramo 'default' nunca
            # devolve lixeira — só 'revisar') — despacha direto, sem passar
            # pela fila. A IA nunca entra nesse bypass: só decide dentro do
            # resíduo, abaixo, e aquele caminho continua indo pra fila.
            auto_lixeira_itens.append(DispatchItem(
                pasta=pasta_db, uidvalidity=uidvalidity, uid=m.uid,
                message_id=m.message_id, acao=ACAO_LIXEIRA, conta=conta,
            ))
            continue
        item = SyncItem(
            conta=conta, pasta=pasta_db, uidvalidity=uidvalidity, uid=m.uid,
            message_id=m.message_id,
            remetente=m.remetente, assunto=m.assunto, data=m.data,
            status=efetiva, categoria=decisao.categoria,
            sera_analisado=(decisao.regra_casada == "default" and ai_ready and recente),
            favorito=m.favorito,
        )
        on_event("item", item)
        if item.sera_analisado:
            residuo.append((m, item))

    if ai_ready:
        for m, item in residuo:
            item.status = "analisando"
            on_event("analisando", item)
            try:
                msg = client.fetch_message(m.uid)
                trecho = clean_for_classification(message_to_text(msg)) if msg else ""
                decisao_ia = apply_ia_decision(
                    ollama, verify_config, assunto=m.assunto, remetente=m.remetente, trecho=trecho
                )
            except Exception as e:
                logger.warning("[%s/%s] IA falhou pro UID %d: %s", conta, pasta, m.uid, e)
                decisao_ia = None
            if decisao_ia is not None:
                store.classify_email(
                    pasta=pasta_db, uidvalidity=uidvalidity, uid=m.uid,
                    status=STATUS_AGUARDANDO, categoria=decisao_ia.categoria,
                    acao_sugerida=decisao_ia.acao, regra_casada=f"ia:{decisao_ia.categoria}",
                )
                item.status = decisao_ia.acao
                item.categoria = decisao_ia.categoria
            on_event("classificado", item)

    if auto_lixeira_itens:
        resultado = dispatch_lixeira_imap(
            client, store, auto_lixeira_itens,
            pasta_real=pasta, trash_folder=trash_folder, chunk_size=chunk_size, origem="auto",
        )
        if resultado.lixeira:
            on_event("auto_lixeira", conta=conta, pasta=pasta_db, quantidade=resultado.lixeira)

    favoritos_atualizados = refresh_favoritos_imap(client, store, pasta_db, pasta)
    if favoritos_atualizados:
        on_event("favoritos", conta=conta, pasta=pasta_db, quantidade=favoritos_atualizados)

    meta = store.get_folder_meta(pasta_db)
    prev_last_uid = meta[1] if meta else 0
    max_uid = max(uids, default=prev_last_uid)
    store.set_folder_meta(pasta_db, uidvalidity, max(max_uid, prev_last_uid))


def _sync_gmail_pasta(
    config, store, engine, ollama, verify_config, ai_ready, account, pasta, limit, on_event, *, ai_max_dias: int = 90
) -> None:
    from apolo.fetch.gmail import GmailClient

    conta_id = f"gmail:{account.name}"
    pasta_db = f"{conta_id}:{pasta}"
    client = GmailClient(
        account.name, account.client_id, account.client_secret,
        config.tokens_dir / f"{account.name}.json", folders=account.folders,
    )
    if not client.is_authorized():
        on_event("erro", conta=conta_id, pasta=pasta, msg="conta não autorizada")
        return

    gmail_ids, history_id = client.list_ids(pasta, limit)
    novos_ids = [gid for gid in gmail_ids if not store.email_exists(pasta_db, 1, client.uid_for(gid))]
    logger.info("[%s/%s] %d UID(s) novo(s) a buscar de %d candidato(s).", conta_id, pasta, len(novos_ids), len(gmail_ids))
    on_event("found", conta=conta_id, pasta=pasta, total=len(novos_ids))

    residuo = []
    auto_lixeira_itens: list[DispatchItem] = []
    falhas_seguidas = 0
    for gid in novos_ids:
        try:
            m = client.fetch_header(gid)
        except Exception as e:
            falhas_seguidas += 1
            logger.warning("[%s/%s] falha ao buscar header de %s (%d seguida(s)): %s",
                           conta_id, pasta, gid, falhas_seguidas, e)
            if falhas_seguidas >= MAX_FALHAS_SEGUIDAS:
                logger.warning("[%s/%s] parando cedo após falhas seguidas — retomando no próximo ciclo.",
                               conta_id, pasta)
                on_event("erro", conta=conta_id, pasta=pasta, msg=f"parou após falhas seguidas: {e}")
                break
            continue
        falhas_seguidas = 0
        if m is None:
            logger.debug("[%s/%s] %s sem resposta OK do FETCH — ignorado.", conta_id, pasta, gid)
            continue
        store.insert_email(
            conta=conta_id, pasta=pasta_db, uidvalidity=1, uid=m.uid,
            message_id=m.message_id, remetente=m.remetente, assunto=m.assunto, data=m.data,
            provider_id=m.provider_id, favorito=m.favorito,
        )
        decisao, novo_status = _classificar_novo(engine, m.remetente, m.assunto, m.list_unsubscribe)
        recente = eh_recente(m.data, ai_max_dias)
        efetiva = acao_efetiva(decisao, ai_ready, recente)
        efetiva = descartar_codigo_lido(decisao, efetiva, m.lido)
        if m.favorito and efetiva == ACAO_LIXEIRA:
            # Favoritado no Gmail: nunca some sozinho pelo auto-envio — cai
            # pra revisão manual, onde o dono vê o aviso antes de excluir.
            efetiva = ACAO_REVISAR
        store.classify_email(
            pasta=pasta_db, uidvalidity=1, uid=m.uid,
            status=novo_status, categoria=decisao.categoria,
            acao_sugerida=efetiva, regra_casada=decisao.regra_casada,
        )
        if efetiva == ACAO_LIXEIRA:
            # Mesmo bypass do IMAP (ver _sync_imap_pasta): cascata determinística
            # decidiu sozinha — despacha direto, sem passar pela fila.
            auto_lixeira_itens.append(DispatchItem(
                pasta=pasta_db, uidvalidity=1, uid=m.uid, message_id=m.message_id,
                acao=ACAO_LIXEIRA, conta=conta_id, provider_id=m.provider_id,
            ))
            continue
        item = SyncItem(
            conta=conta_id, pasta=pasta_db, uidvalidity=1, uid=m.uid,
            message_id=m.message_id, provider_id=m.provider_id,
            remetente=m.remetente, assunto=m.assunto, data=m.data,
            status=efetiva, categoria=decisao.categoria,
            sera_analisado=(decisao.regra_casada == "default" and ai_ready and recente),
            favorito=m.favorito,
        )
        on_event("item", item)
        if item.sera_analisado:
            residuo.append((m, item))

    if ai_ready:
        for m, item in residuo:
            item.status = "analisando"
            on_event("analisando", item)
            try:
                trecho = clean_for_classification(client.fetch_message(m.provider_id)) if m.provider_id else ""
                decisao_ia = apply_ia_decision(
                    ollama, verify_config, assunto=m.assunto, remetente=m.remetente, trecho=trecho
                )
            except Exception as e:
                logger.warning("[%s/%s] IA falhou pro item %s: %s", conta_id, pasta, m.uid, e)
                decisao_ia = None
            if decisao_ia is not None:
                store.classify_email(
                    pasta=pasta_db, uidvalidity=1, uid=m.uid,
                    status=STATUS_AGUARDANDO, categoria=decisao_ia.categoria,
                    acao_sugerida=decisao_ia.acao, regra_casada=f"ia:{decisao_ia.categoria}",
                )
                item.status = decisao_ia.acao
                item.categoria = decisao_ia.categoria
            on_event("classificado", item)

    if auto_lixeira_itens:
        resultado = dispatch_lixeira_gmail(client, store, auto_lixeira_itens, origem="auto")
        if resultado.lixeira:
            on_event("auto_lixeira", conta=conta_id, pasta=pasta_db, quantidade=resultado.lixeira)

    favoritos_atualizados = refresh_favoritos_gmail(client, store, pasta_db, pasta)
    if favoritos_atualizados:
        on_event("favoritos", conta=conta_id, pasta=pasta_db, quantidade=favoritos_atualizados)

    meta = store.get_folder_meta(pasta_db)
    prev_last_uid = meta[1] if meta else 0
    store.set_folder_meta(pasta_db, 1, max(history_id, prev_last_uid))
