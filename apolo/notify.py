"""Notificações de desktop — API pública estável do Apolo.

Delega pro backend de `apolo.platform` conforme o sistema operacional (hoje só
Linux, via `notify-send`/libnotify — ver `apolo/platform/linux/notifier.py`).
Best-effort sempre: notificação é cosmético, uma falha aqui nunca pode
derrubar a triagem.
"""

from __future__ import annotations

from apolo.platform import get_notifier


def notify(
    summary: str,
    body: str = "",
    *,
    urgency: str = "normal",
    expire_ms: int | None = None,
    replace_id: int | None = None,
) -> int | None:
    """Dispara uma notificação de desktop. Devolve o id (pra permitir
    substituir depois via `replace_id`), ou `None` se não deu."""
    return get_notifier().notify(
        summary, body, urgency=urgency, expire_ms=expire_ms, replace_id=replace_id
    )
