"""`Clipboard` via `wl-copy`/`wl-paste` (Wayland), `xclip`, `xsel` (X11).

Tenta cada ferramenta na ordem; a primeira que existir e não falhar vence.
Devolve `False`/`None` se nenhuma servir — quem chama decide como avisar o
dono (nunca derruba a UI).
"""

from __future__ import annotations

import shutil
import subprocess


class LinuxClipboard:
    def copy(self, text: str) -> bool:
        candidatos = (
            ["wl-copy"],
            ["xclip", "-selection", "clipboard"],
            ["xsel", "-ib"],
        )
        for cmd in candidatos:
            if not shutil.which(cmd[0]):
                continue
            try:
                subprocess.run(cmd, input=text.encode(), check=True, timeout=5)
                return True
            except Exception:
                continue
        return False

    def paste(self) -> str | None:
        # Colar dentro da TUI (kitty -e) depende do Ctrl+Shift+V do terminal
        # e do bracketed-paste — frágil em campo de senha. Ler o clipboard
        # aqui contorna isso de vez.
        for cmd in (
            ["wl-paste", "-n"],
            ["xclip", "-selection", "clipboard", "-o"],
            ["xsel", "-b"],
        ):
            if shutil.which(cmd[0]) is None:
                continue
            try:
                p = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False)
            except (OSError, subprocess.SubprocessError):
                continue
            if p.returncode == 0:
                return p.stdout
        return None
