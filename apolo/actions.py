"""Camada de ação — executa o que a fila decidiu e registra no log.

No passo 3 a execução é sempre manual: o dono despacha a fila pela TUI. Mover
pra lixeira é COPY pra Trash + \\Deleted + EXPUNGE (o Bridge não tem MOVE) — e é
reversível, já que a mensagem fica na Trash. Cada remoção é logada com dado de
reversão pra sustentar o `apolo undo` (passo 6).

A promoção pra execução automática (sem o dono) só chega no passo 6.
"""

import json
import sys
from dataclasses import dataclass
from pathlib import Path

from apolo.fetch.imap import BridgeClient
from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER
from apolo.storage.db import Storage


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

    Agrupa o EXPUNGE por pasta (uma vez por pasta com remoções). Cada item é
    isolado: se o COPY de um UID falha (ex.: UID obsoleto após resync do Bridge),
    o item é pulado e contado em `falhas` — sem abortar o lote nem impedir o
    EXPUNGE dos que deram certo.
    """
    result = DispatchResult()
    pastas_com_remocao: set[str] = set()

    for item in itens:
        if item.acao == ACAO_LIXEIRA:
            try:
                client.copy_to(item.pasta, item.uid, trash_folder)
            except Exception as e:
                # UID já saiu da pasta (resync/movido fora) ou falha pontual de
                # rede: não derruba o resto do lote.
                print(
                    f"aviso: pulei {item.pasta} UID {item.uid} (lixeira): {e}",
                    file=sys.stderr,
                )
                result.falhas += 1
                continue
            pastas_com_remocao.add(item.pasta)
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
        elif item.acao == ACAO_MANTER:
            store.mark_dispatched(
                pasta=item.pasta,
                uidvalidity=item.uidvalidity,
                uid=item.uid,
                acao_aplicada=ACAO_MANTER,
            )
            result.mantidos += 1
        # 'revisar' (indeciso) não é despachado: fica na fila pra próxima.

    # EXPUNGE depois de marcar tudo, pra não reabrir a pasta a cada email.
    for pasta in pastas_com_remocao:
        try:
            client.expunge(pasta)
        except Exception as e:
            print(f"aviso: EXPUNGE em {pasta} falhou: {e}", file=sys.stderr)

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

    for item in itens:
        if item.acao == ACAO_LIXEIRA:
            if item.provider_id:
                client.trash_message(item.provider_id)
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
        elif item.acao == ACAO_MANTER:
            store.mark_dispatched(
                pasta=item.pasta,
                uidvalidity=item.uidvalidity,
                uid=item.uid,
                acao_aplicada=ACAO_MANTER,
            )
            result.mantidos += 1

    return result


def fetch_body(config, item) -> str:
    """Busca o corpo de um email da fila e devolve texto limpo (best-effort).

    `item` é duck-typed (o Item da UI serve): precisa de `.conta`, `.pasta`,
    `.uid` e `.provider_id`. Proton via Bridge (IMAP, BODY.PEEK — não marca lido);
    Gmail via API (format=raw). Usado pela UI pra extrair código/link sem abrir o
    email no cliente. Devolve '' se não der.
    """
    from apolo.clean import message_to_text

    if item.conta == "proton":
        config.require_credentials()
        with BridgeClient(config.imap_host, config.imap_port, config.imap_security) as client:
            client.login(config.username, config.password)
            msg = client.fetch_message_from(item.pasta, item.uid)
        return message_to_text(msg) if msg else ""

    if item.conta.startswith("gmail:"):
        from apolo.config import load_accounts
        from apolo.fetch.gmail import GmailClient

        if not item.provider_id:
            return ""
        name = item.conta.removeprefix("gmail:")
        account = next(
            (a for a in load_accounts(config.accounts_path)
             if a.provider == "gmail" and a.name == name),
            None,
        )
        if account is None:
            return ""
        client = GmailClient(
            name, account.client_id, account.client_secret,
            config.tokens_dir / f"{name}.json",
        )
        msg = client.fetch_raw(item.provider_id)
        return message_to_text(msg) if msg else ""

    return ""


class _NoClient:
    """Sentinela: usado quando nenhum item vai pra lixeira (sem IMAP)."""

    def copy_to(self, *a, **k):
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
    imap_itens = [i for i in itens if i.conta == "proton"]
    gmail_itens = [i for i in itens if i.conta.startswith("gmail:")]
    n_lix = sum(1 for i in itens if i.acao == ACAO_LIXEIRA)
    _log(
        config,
        f"aplicando {len(itens)} item(ns): {len(imap_itens)} proton, "
        f"{len(gmail_itens)} gmail, {n_lix} lixeira.",
    )

    total = DispatchResult()
    try:
        total = _apply_decisions_inner(
            config, imap_itens, gmail_itens, accounts_by_name, BridgeClient
        )
    except Exception as e:
        _log(config, f"FALHA: {type(e).__name__}: {e}")
        raise
    _log(
        config,
        f"FIM: {total.lixeira} lixeira, {total.mantidos} mantido(s), "
        f"{total.falhas} falha(s).",
    )
    return total


def _log(config, msg: str) -> None:
    """Anexa uma linha datada a ~/.local/share/apolo/apolo.log (best-effort).

    A TUI roda num popup que fecha ao terminar, então mensagens no terminal
    somem. Este arquivo persiste o que cada despacho fez/falhou.
    """
    import datetime

    try:
        path = config.db_path.parent / "apolo.log"
        path.parent.mkdir(parents=True, exist_ok=True)
        ts = datetime.datetime.now().isoformat(timespec="seconds")
        with open(path, "a", encoding="utf-8") as fh:
            fh.write(f"{ts} dispatch: {msg}\n")
    except Exception:
        pass


def _apply_decisions_inner(config, imap_itens, gmail_itens, accounts_by_name, BridgeClient) -> DispatchResult:
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
