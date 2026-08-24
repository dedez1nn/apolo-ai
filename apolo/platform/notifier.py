"""Contrato de notificação de desktop — um `Notifier` por sistema operacional.

`apolo/notify.py` é a API pública estável que o resto do código importa; este
módulo só define o que um backend precisa saber fazer. Ver `apolo/platform/
linux/notifier.py` pro único backend implementado até agora.
"""

from __future__ import annotations

from typing import Protocol


class Notifier(Protocol):
    def notify(
        self,
        summary: str,
        body: str = "",
        *,
        urgency: str = "normal",
        expire_ms: int | None = None,
        replace_id: int | None = None,
    ) -> int | None:
        """Dispara uma notificação de desktop.

        Devolve um id (pra permitir substituir por uma notificação posterior
        via `replace_id`) ou `None` se não deu. Sempre best-effort: uma falha
        aqui nunca pode derrubar quem chamou — notificação é cosmético.
        """
        ...
