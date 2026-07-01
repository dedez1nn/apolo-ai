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

from dataclasses import dataclass
from typing import Callable

from apolo.ai.ollama import OllamaClient
from apolo.clean import clean_for_classification, message_to_text
from apolo.config import Config, load_accounts
from apolo.fetch.imap import BridgeClient
from apolo.rules.engine import RuleEngine
from apolo.storage.db import STATUS_AGUARDANDO, STATUS_CLASSIFICADO, Storage
from apolo.verify import VerifyConfig, apply_ia_decision

OnEvent = Callable[..., None]


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


def run_sync(config: Config, *, limit: int, on_event: OnEvent) -> None:
    """Varre todas as contas vinculadas e classifica o que ainda não estava no banco.

    Erros por conta/pasta (Bridge fora, conta Gmail não autorizada, etc.) são
    reportados via `on_event("erro", ...)` e não interrompem as demais contas.
    """
    engine = RuleEngine.from_file(config.rules_path)
    verify_config = VerifyConfig.from_file(config.rules_path)
    ollama = OllamaClient(config.ollama_url, config.ollama_model, keep_alive=config.ollama_keep_alive)
    ai_ready = config.ai_enabled and ollama.available()

    with Storage(config.db_path) as store:
        try:
            config.require_credentials()
            with BridgeClient(config.imap_host, config.imap_port, config.imap_security) as client:
                client.login(config.username, config.password)
                for pasta in config.folders:
                    _sync_imap_pasta(store, engine, ollama, verify_config, ai_ready, client, pasta, limit, on_event)
        except Exception as exc:
            on_event("erro", conta="proton", pasta="", msg=str(exc))

        accounts = [a for a in load_accounts(config.accounts_path) if a.provider == "gmail"]
        for account in accounts:
            for pasta in account.folders:
                try:
                    _sync_gmail_pasta(
                        config, store, engine, ollama, verify_config, ai_ready, account, pasta, limit, on_event
                    )
                except Exception as exc:
                    on_event("erro", conta=f"gmail:{account.name}", pasta=pasta, msg=str(exc))

    on_event("fim")


def _classificar_novo(engine, remetente, assunto, list_unsubscribe):
    decisao = engine.classify(remetente=remetente, assunto=assunto, list_unsubscribe=list_unsubscribe)
    novo_status = STATUS_AGUARDANDO if decisao.precisa_revisao else STATUS_CLASSIFICADO
    return decisao, novo_status


def _sync_imap_pasta(store, engine, ollama, verify_config, ai_ready, client, pasta, limit, on_event) -> None:
    uids, uidvalidity = client.list_uids(pasta, limit)
    novos_uids = [u for u in uids if not store.email_exists(pasta, uidvalidity, u)]
    on_event("found", conta="proton", pasta=pasta, total=len(novos_uids))

    residuo = []
    for uid in novos_uids:
        m = client.fetch_header(uid)
        if m is None:
            continue
        store.insert_email(
            pasta=pasta, uidvalidity=uidvalidity, uid=m.uid,
            message_id=m.message_id, remetente=m.remetente, assunto=m.assunto, data=m.data,
        )
        decisao, novo_status = _classificar_novo(engine, m.remetente, m.assunto, m.list_unsubscribe)
        store.classify_email(
            pasta=pasta, uidvalidity=uidvalidity, uid=m.uid,
            status=novo_status, categoria=decisao.categoria,
            acao_sugerida=decisao.acao_sugerida, regra_casada=decisao.regra_casada,
        )
        item = SyncItem(
            conta="proton", pasta=pasta, uidvalidity=uidvalidity, uid=m.uid,
            message_id=m.message_id,
            remetente=m.remetente, assunto=m.assunto, data=m.data,
            status=decisao.acao_sugerida, categoria=decisao.categoria,
            sera_analisado=(decisao.regra_casada == "default"),
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
            except Exception:
                decisao_ia = None
            if decisao_ia is not None:
                store.classify_email(
                    pasta=pasta, uidvalidity=uidvalidity, uid=m.uid,
                    status=STATUS_AGUARDANDO, categoria=decisao_ia.categoria,
                    acao_sugerida=decisao_ia.acao, regra_casada=f"ia:{decisao_ia.categoria}",
                )
                item.status = decisao_ia.acao
                item.categoria = decisao_ia.categoria
            on_event("classificado", item)

    meta = store.get_folder_meta(pasta)
    prev_last_uid = meta[1] if meta else 0
    max_uid = max(uids, default=prev_last_uid)
    store.set_folder_meta(pasta, uidvalidity, max(max_uid, prev_last_uid))


def _sync_gmail_pasta(config, store, engine, ollama, verify_config, ai_ready, account, pasta, limit, on_event) -> None:
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
    on_event("found", conta=conta_id, pasta=pasta, total=len(novos_ids))

    residuo = []
    for gid in novos_ids:
        m = client.fetch_header(gid)
        if m is None:
            continue
        store.insert_email(
            conta=conta_id, pasta=pasta_db, uidvalidity=1, uid=m.uid,
            message_id=m.message_id, remetente=m.remetente, assunto=m.assunto, data=m.data,
            provider_id=m.provider_id,
        )
        decisao, novo_status = _classificar_novo(engine, m.remetente, m.assunto, m.list_unsubscribe)
        store.classify_email(
            pasta=pasta_db, uidvalidity=1, uid=m.uid,
            status=novo_status, categoria=decisao.categoria,
            acao_sugerida=decisao.acao_sugerida, regra_casada=decisao.regra_casada,
        )
        item = SyncItem(
            conta=conta_id, pasta=pasta_db, uidvalidity=1, uid=m.uid,
            message_id=m.message_id, provider_id=m.provider_id,
            remetente=m.remetente, assunto=m.assunto, data=m.data,
            status=decisao.acao_sugerida, categoria=decisao.categoria,
            sera_analisado=(decisao.regra_casada == "default"),
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
            except Exception:
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

    meta = store.get_folder_meta(pasta_db)
    prev_last_uid = meta[1] if meta else 0
    store.set_folder_meta(pasta_db, 1, max(history_id, prev_last_uid))
