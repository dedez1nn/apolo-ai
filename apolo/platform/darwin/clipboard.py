"""`Clipboard` via `pbcopy`/`pbpaste` — vêm de fábrica em todo macOS, sem
instalar nada. UTF-8 nativo, sem as pegadinhas de codificação do Windows.
"""

from __future__ import annotations

import shutil
import subprocess


class DarwinClipboard:
    def copy(self, text: str) -> bool:
        if shutil.which("pbcopy") is None:
            return False
        try:
            subprocess.run(["pbcopy"], input=text.encode("utf-8"), check=True, timeout=5)
            return True
        except Exception:
            return False

    def paste(self) -> str | None:
        if shutil.which("pbpaste") is None:
            return None
        try:
            p = subprocess.run(["pbpaste"], capture_output=True, timeout=5, check=False)
        except Exception:
            return None
        if p.returncode != 0:
            return None
        try:
            return p.stdout.decode("utf-8")
        except UnicodeDecodeError:
            return p.stdout.decode("utf-8", errors="replace")
