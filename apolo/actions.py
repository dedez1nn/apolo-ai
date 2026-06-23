"""Camada de ação — executa o que a fila decidiu e registra no log.

No passo 3 a execução é sempre manual: o dono despacha a fila pela TUI. Mover
pra lixeira é COPY pra Trash + \\Deleted + EXPUNGE (o Bridge não tem MOVE) — e é
reversível, já que a mensagem fica na Trash. Cada remoção é logada com dado de
reversão pra sustentar o `apolo undo` (passo 6).

A promoção pra execução automática (sem o dono) só chega no passo 6.
"""

import json
from dataclasses import dataclass

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
    acao: str  # ACAO_LIXEIRA | ACAO_MANTER


@dataclass
class DispatchResult:
    lixeira: int = 0
    mantidos: int = 0


def dispatch(client: BridgeClient, store: Storage, itens: list[DispatchItem], *, trash_folder: str) -> DispatchResult:
    """Aplica as ações: 'manter' só marca despachado; 'lixeira' move pra Trash.

    Agrupa o EXPUNGE por pasta (uma vez por pasta com remoções).
    """
    result = DispatchResult()
    pastas_com_remocao: set[str] = set()

    for item in itens:
        if item.acao == ACAO_LIXEIRA:
            client.copy_to(item.pasta, item.uid, trash_folder)
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
        client.expunge(pasta)

    return result
