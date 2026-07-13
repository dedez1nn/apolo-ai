"""Camada de ação — executa o que a fila decidiu e registra no log.

No passo 3 a execução é sempre manual: o dono despacha a fila pela TUI. Mover
pra lixeira é COPY pra Trash + \\Deleted + EXPUNGE (o Bridge não tem MOVE) — e é
reversível, já que a mensagem fica na Trash. Cada remoção é logada com dado de
reversão pra sustentar o `apolo undo` (passo 6).

A promoção pra execução automática (sem o dono) só chega no passo 6.
"""

import json
import logging
import sys
from dataclasses import dataclass
from pathlib import Path

from apolo.fetch.imap import BridgeClient
from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER
from apolo.storage.db import Storage

logger = logging.getLogger("apolo.actions")


@dataclass
class DispatchItem:
    """Um email da fila + a ação final escolhida pelo dono."""

    pasta: str
    uidvalidity: int
    uid: int
    message_id: str | None
    acao: str                    # ACAO_LIXEIRA | ACAO_MANTER
    conta: str = "proton"        # "proton" ou "gmail:<name>"
    provider_id: str | None = None  # Gmail message ID; None pra IMAP


@dataclass
class DispatchResult:
    lixeira: int = 0
    mantidos: int = 0
    falhas: int = 0


def dispatch(client: BridgeClient, store: Storage, itens: list[DispatchItem], *, trash_folder: str) -> DispatchResult:
    """Aplica as ações: 'manter' só marca despachado; 'lixeira' move pra Trash.

    Lixeira é despachada em lote por pasta (1 SELECT + COPY/STORE em chunks —
    ver `BridgeClient.copy_to_bulk`), bem mais rápido que um UID por vez. Um
    UID que não deu certo no lote (ex.: obsoleto após resync do Bridge) é
    pulado e contado em `falhas`, sem abortar o resto.
    """
    result = DispatchResult()

    lixeira_por_pasta: dict[str, list[DispatchItem]] = {}
    for item in itens:
        if item.acao == ACAO_LIXEIRA:
            lixeira_por_pasta.setdefault(item.pasta, []).append(item)
        elif item.acao == ACAO_MANTER:
            store.mark_dispatched(
                pasta=item.pasta,
                uidvalidity=item.uidvalidity,
                uid=item.uid,
                acao_aplicada=ACAO_MANTER,
            )
            result.mantidos += 1
        # 'revisar' (indeciso) não é despachado: fica na fila pra próxima.

    for pasta, pasta_itens in lixeira_por_pasta.items():
        by_uid = {item.uid: item for item in pasta_itens}
        uids = list(by_uid.keys())
        try:
            uids_ok = set(client.copy_to_bulk(pasta, uids, trash_folder))
        except Exception as e:
            logger.warning("lote de lixeira em %s falhou por completo: %s", pasta, e)
            print(f"aviso: lote de lixeira em {pasta} falhou: {e}", file=sys.stderr)
            uids_ok = set()

        for uid in uids:
            if uid not in uids_ok:
                logger.warning("pulei %s UID %d (lixeira): não copiado.", pasta, uid)
                print(f"aviso: pulei {pasta} UID {uid} (lixeira): não copiado", file=sys.stderr)
                result.falhas += 1
                continue
            item = by_uid[uid]
            store.log_action(
                pasta=item.pasta,
                uidvalidity=item.uidvalidity,
                uid=item.uid,
                acao=ACAO_LIXEIRA,
                dado_reverter=json.dumps(
                    {
                        "destino": trash_folder,
                        "pasta_origem": item.pasta,
                        "message_id": item.message_id,
                    }
                ),
            )
            store.mark_dispatched(
                pasta=item.pasta,
                uidvalidity=item.uidvalidity,
                uid=item.uid,
                acao_aplicada=ACAO_LIXEIRA,
            )
            result.lixeira += 1

        # EXPUNGE depois de marcar tudo, pra não reabrir a pasta a cada email.
        if uids_ok:
            try:
                client.expunge(pasta)
            except Exception as e:
                logger.warning("EXPUNGE em %s falhou: %s", pasta, e)
                print(f"aviso: EXPUNGE em {pasta} falhou: {e}", file=sys.stderr)

    return result


def dispatch_imap_account(
    client, store: Storage, itens: list[DispatchItem], *, trash_folder: str, chunk_size: int = 50
) -> DispatchResult:
    """Igual a `dispatch`, mas pra contas IMAP adicionais (ex.: Outlook).

    Não reaproveita `dispatch` porque lá `item.pasta` É o nome real da pasta
    IMAP — aqui `item.pasta` é a chave do banco, namespaced como
    "imap:<nome>:<pasta>" (ver `apolo.sync._sync_imap_pasta`), então a pasta
    real usada no COPY/EXPUNGE precisa ser destrinchada do prefixo da conta.

    `chunk_size` é bem menor que o padrão de `copy_to_bulk` (300, pensado pro
    Bridge local): servidores remotos têm throttling não documentado e lotes
    grandes têm mais chance de sofrer timeout no meio do caminho — configurável
    por conta (`AccountConfig.chunk_size`).
    """
    result = DispatchResult()

    lixeira_por_pasta: dict[str, list[DispatchItem]] = {}
    for item in itens:
        if item.acao == ACAO_LIXEIRA:
            pasta_real = item.pasta.removeprefix(f"{item.conta}:")
            lixeira_por_pasta.setdefault(pasta_real, []).append(item)
        elif item.acao == ACAO_MANTER:
            store.mark_dispatched(
                pasta=item.pasta,
                uidvalidity=item.uidvalidity,
                uid=item.uid,
                acao_aplicada=ACAO_MANTER,
            )
            result.mantidos += 1

    for pasta_real, pasta_itens in lixeira_por_pasta.items():
        by_uid = {item.uid: item for item in pasta_itens}
        uids = list(by_uid.keys())
        try:
            uids_ok = set(client.copy_to_bulk(pasta_real, uids, trash_folder, chunk_size=chunk_size))
        except Exception as e:
            logger.warning("lote de lixeira em %s falhou por completo: %s", pasta_real, e)
            print(f"aviso: lote de lixeira em {pasta_real} falhou: {e}", file=sys.stderr)
            uids_ok = set()

        for uid in uids:
            if uid not in uids_ok:
                logger.warning("pulei %s UID %d (lixeira): não copiado.", pasta_real, uid)
                print(f"aviso: pulei {pasta_real} UID {uid} (lixeira): não copiado", file=sys.stderr)
                result.falhas += 1
                continue
            item = by_uid[uid]
            store.log_action(
                pasta=item.pasta,
                uidvalidity=item.uidvalidity,
                uid=item.uid,
                acao=ACAO_LIXEIRA,
                dado_reverter=json.dumps(
                    {
                        "destino": trash_folder,
                        "pasta_origem": item.pasta,
                        "message_id": item.message_id,
                    }
                ),
            )
            store.mark_dispatched(
                pasta=item.pasta,
                uidvalidity=item.uidvalidity,
                uid=item.uid,
                acao_aplicada=ACAO_LIXEIRA,
            )
            result.lixeira += 1

        if uids_ok:
            try:
                client.expunge(pasta_real)
            except Exception as e:
                logger.warning("EXPUNGE em %s falhou: %s", pasta_real, e)
                print(f"aviso: EXPUNGE em {pasta_real} falhou: {e}", file=sys.stderr)

    return result


def dispatch_gmail(
    store: Storage,
    itens: list[DispatchItem],
    *,
    name: str,
    client_id: str,
    client_secret: str,
    token_path: Path,
) -> DispatchResult:
    """Despacha itens Gmail: lixeira via API, manter só marca no DB."""
    from apolo.fetch.gmail import GmailClient

    client = GmailClient(name, client_id, client_secret, token_path)
    result = DispatchResult()

    lixeira_com_id = [i for i in itens if i.acao == ACAO_LIXEIRA and i.provider_id]
    lixeira_sem_id = [i for i in itens if i.acao == ACAO_LIXEIRA and not i.provider_id]
    manter_itens = [i for i in itens if i.acao == ACAO_MANTER]

    ids_ok: set[str] = set()
    if lixeira_com_id:
        provider_ids = [i.provider_id for i in lixeira_com_id]
        try:
            client.trash_messages_batch(provider_ids)
            ids_ok = set(provider_ids)
        except Exception as e:
            logger.warning(
                "lote de lixeira gmail:%s (%d item(ns)) falhou: %s", name, len(provider_ids), e
            )
            print(f"aviso: lote de lixeira gmail:{name} falhou: {e}", file=sys.stderr)

    def _marcar_lixeira(item: DispatchItem) -> None:
        store.log_action(
            pasta=item.pasta,
            uidvalidity=item.uidvalidity,
            uid=item.uid,
            acao=ACAO_LIXEIRA,
            dado_reverter=json.dumps({
                "provider_id": item.provider_id,
                "conta": item.conta,
            }),
        )
        store.mark_dispatched(
            pasta=item.pasta,
            uidvalidity=item.uidvalidity,
            uid=item.uid,
            acao_aplicada=ACAO_LIXEIRA,
        )
        result.lixeira += 1

    for item in lixeira_com_id:
        if item.provider_id in ids_ok:
            _marcar_lixeira(item)
        else:
            logger.warning("pulei gmail:%s %s (lixeira): lote falhou.", name, item.provider_id)
            result.falhas += 1

    # Sem provider_id não há o que chamar na API — só regulariza o estado
    # (igual ao comportamento anterior, item a item).
    for item in lixeira_sem_id:
        _marcar_lixeira(item)

    for item in manter_itens:
        store.mark_dispatched(
            pasta=item.pasta,
            uidvalidity=item.uidvalidity,
            uid=item.uid,
            acao_aplicada=ACAO_MANTER,
        )
        result.mantidos += 1

    return result


def fetch_message(config, item):
    """Busca a mensagem RFC822 completa da fila (best-effort).

    `item` é duck-typed (o Item da UI serve): precisa de `.conta`, `.pasta`,
    `.uid` e `.provider_id`. Proton via Bridge (IMAP, BODY.PEEK — não marca lido);
    Gmail via API (format=raw). Usado pela UI pra extrair código/link e montar
    a prévia sem abrir o email no cliente. Devolve None se não der.
    """
    if item.conta == "proton":
        config.require_credentials()
        with BridgeClient(config.imap_host, config.imap_port, config.imap_security) as client:
            client.login(config.username, config.password)
            return client.fetch_message_from(item.pasta, item.uid)

    if item.conta.startswith("gmail:"):
        from apolo.config import load_accounts
        from apolo.fetch.gmail import GmailClient

        if not item.provider_id:
            return None
        name = item.conta.removeprefix("gmail:")
        account = next(
            (a for a in load_accounts(config.accounts_path)
             if a.provider == "gmail" and a.name == name),
            None,
        )
        if account is None:
            return None
        client = GmailClient(
            name, account.client_id, account.client_secret,
            config.tokens_dir / f"{name}.json",
        )
        return client.fetch_raw(item.provider_id)

    if item.conta.startswith("imap:"):
        from apolo import secrets
        from apolo.config import load_accounts

        name = item.conta.removeprefix("imap:")
        account = next(
            (a for a in load_accounts(config.accounts_path)
             if a.provider == "imap" and a.name == name),
            None,
        )
        if account is None:
            return None
        senha = secrets.lookup_account_password(item.conta)
        if not (account.host and account.username and senha):
            return None
        pasta_real = item.pasta.removeprefix(f"{item.conta}:")
        with BridgeClient(account.host, account.port, account.security) as client:
            client.login(account.username, senha)
            return client.fetch_message_from(pasta_real, item.uid)

    return None


def fetch_body(config, item) -> str:
    """Busca o corpo de um email da fila e devolve texto limpo (best-effort)."""
    from apolo.clean import message_to_text

    msg = fetch_message(config, item)
    return message_to_text(msg) if msg else ""


class _NoClient:
    """Sentinela: usado quando nenhum item vai pra lixeira (sem IMAP)."""

    def copy_to_bulk(self, *a, **k):
        raise AssertionError("dispatch sem IMAP não deveria mover emails")

    def expunge(self, *a, **k):
        raise AssertionError("dispatch sem IMAP não deveria expurgar")


def apply_decisions(config, itens: list[DispatchItem]) -> DispatchResult:
    """Aplica de fato uma leva de decisões (IMAP + Gmail) e devolve o total.

    Centraliza o despacho pra ser chamado tanto pelo `cli` (ao fechar a TUI)
    quanto pela própria TUI (aplicar na hora, no Enter). Abre seu próprio
    Storage; o login IMAP só acontece se houver ao menos um item 'lixeira'.
    """
    from apolo.config import load_accounts
    from apolo.fetch.imap import BridgeClient

    accounts_by_name = {
        a.name: a for a in load_accounts(config.accounts_path) if a.provider == "gmail"
    }
    imap_accounts_by_name = {
        a.name: a for a in load_accounts(config.accounts_path) if a.provider == "imap"
    }
    imap_itens = [i for i in itens if i.conta == "proton"]
    gmail_itens = [i for i in itens if i.conta.startswith("gmail:")]
    imap_account_itens = [i for i in itens if i.conta.startswith("imap:")]
    n_lix = sum(1 for i in itens if i.acao == ACAO_LIXEIRA)
    logger.info(
        "aplicando %d item(ns): %d proton, %d gmail, %d imap, %d lixeira.",
        len(itens), len(imap_itens), len(gmail_itens), len(imap_account_itens), n_lix,
    )

    total = DispatchResult()
    try:
        total = _apply_decisions_inner(
            config, imap_itens, gmail_itens, imap_account_itens,
            accounts_by_name, imap_accounts_by_name, BridgeClient,
        )
    except Exception:
        logger.exception("dispatch falhou")
        raise
    logger.info(
        "fim do dispatch: %d lixeira, %d mantido(s), %d falha(s).",
        total.lixeira, total.mantidos, total.falhas,
    )
    return total


def _apply_decisions_inner(
    config, imap_itens, gmail_itens, imap_account_itens, accounts_by_name, imap_accounts_by_name, BridgeClient
) -> DispatchResult:
    total = DispatchResult()
    with Storage(config.db_path) as store:
        if imap_itens:
            precisa_imap = any(i.acao == ACAO_LIXEIRA for i in imap_itens)
            if precisa_imap:
                config.require_credentials()
                with BridgeClient(
                    config.imap_host, config.imap_port, config.imap_security
                ) as client:
                    client.login(config.username, config.password)
                    res = dispatch(client, store, imap_itens, trash_folder=config.trash_folder)
            else:
                res = dispatch(_NoClient(), store, imap_itens, trash_folder=config.trash_folder)
            total.lixeira += res.lixeira
            total.mantidos += res.mantidos
            total.falhas += res.falhas

        if imap_account_itens:
            from apolo import secrets

            by_conta_imap: dict[str, list] = {}
            for item in imap_account_itens:
                by_conta_imap.setdefault(item.conta, []).append(item)
            for conta_id, citens in by_conta_imap.items():
                name = conta_id.removeprefix("imap:")
                account = imap_accounts_by_name.get(name)
                if account is None:
                    logger.warning("dispatch %s: conta não encontrada em accounts.toml — pulando.", conta_id)
                    continue
                precisa_imap = any(i.acao == ACAO_LIXEIRA for i in citens)
                if precisa_imap:
                    senha = secrets.lookup_account_password(conta_id)
                    if not (account.host and account.username and senha):
                        logger.warning("dispatch %s: credenciais incompletas — pulando lote de lixeira.", conta_id)
                        res = DispatchResult(falhas=sum(1 for i in citens if i.acao == ACAO_LIXEIRA))
                    else:
                        with BridgeClient(account.host, account.port, account.security) as client:
                            client.login(account.username, senha)
                            res = dispatch_imap_account(
                                client, store, citens, trash_folder=account.trash_folder, chunk_size=account.chunk_size
                            )
                else:
                    res = dispatch_imap_account(
                        _NoClient(), store, citens, trash_folder=account.trash_folder, chunk_size=account.chunk_size
                    )
                total.lixeira += res.lixeira
                total.mantidos += res.mantidos
                total.falhas += res.falhas

        if gmail_itens:
            by_conta: dict[str, list] = {}
            for item in gmail_itens:
                by_conta.setdefault(item.conta, []).append(item)
            for conta_id, citens in by_conta.items():
                name = conta_id.removeprefix("gmail:")
                account = accounts_by_name.get(name)
                if account is None:
                    continue
                token_path = config.tokens_dir / f"{name}.json"
                res = dispatch_gmail(
                    store,
                    citens,
                    name=name,
                    client_id=account.client_id,
                    client_secret=account.client_secret,
                    token_path=token_path,
                )
                total.lixeira += res.lixeira
                total.mantidos += res.mantidos
                total.falhas += res.falhas

    return total
