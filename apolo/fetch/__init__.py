"""Tipos compartilhados do subsistema de fetch — usados por IMAP e Gmail."""

from __future__ import annotations

from dataclasses import dataclass


@dataclass(frozen=True)
class FetchedEmail:
    """Metadados de cabeçalho de um email novo (sem corpo)."""

    uid: int                    # inteiro estável: UID IMAP ou hash do Gmail ID
    message_id: str | None      # RFC 2822 Message-ID
    remetente: str
    assunto: str
    data: str
    list_unsubscribe: str       # header (vazio se ausente)
    provider_id: str | None = None  # Gmail message ID; None para IMAP


@dataclass(frozen=True)
class FolderResult:
    """Resultado da varredura incremental de uma pasta/label."""

    pasta: str
    uidvalidity: int    # IMAP: valor real; Gmail: 1 fixo
    resynced: bool
    novos: list[FetchedEmail]
    ultimo_uid: int     # IMAP: maior UID; Gmail: historyId atual
