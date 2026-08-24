"""Contrato de clipboard — um `Clipboard` por sistema operacional.

Hoje usado por `apolo/extract.py` (copiar código/link extraído da fila) e
`apolo/ui/settings.py` (colar a senha do Bridge sem depender do
bracketed-paste do terminal). Ver `apolo/platform/linux/clipboard.py` pro
único backend implementado até agora (`wl-copy`/`wl-paste`, `xclip`, `xsel`).
"""

from __future__ import annotations

from typing import Protocol


class Clipboard(Protocol):
    def copy(self, text: str) -> bool:
        """Copia `text` pro clipboard do sistema. True se algum backend aceitou."""
        ...

    def paste(self) -> str | None:
        """Lê o clipboard do sistema. None se nenhum backend serviu."""
        ...
