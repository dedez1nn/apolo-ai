"""`Clipboard` via PowerShell (`Set-Clipboard`/`Get-Clipboard`), com `clip.exe`
como fallback de escrita se PowerShell não existir.

`Set-Clipboard`/`Get-Clipboard` (PowerShell 5.1+, presente por padrão desde o
Windows 10) lidam com Unicode corretamente; `clip.exe` sozinho é mais antigo e
menos confiável com acentos, então só entra se não houver PowerShell.
"""

from __future__ import annotations

import shutil
import subprocess


def _powershell() -> str | None:
    return shutil.which("powershell") or shutil.which("pwsh")


class Win32Clipboard:
    def copy(self, text: str) -> bool:
        exe = _powershell()
        if exe is not None:
            try:
                subprocess.run(
                    [exe, "-NoProfile", "-NonInteractive", "-Command", "Set-Clipboard -Value $input"],
                    input=text, text=True, check=True, timeout=5, capture_output=True,
                )
                return True
            except Exception:
                pass
        if shutil.which("clip") is not None:
            try:
                # clip.exe lê o texto do stdin em UTF-16LE (formato nativo do
                # clipboard de texto do Windows).
                subprocess.run(["clip"], input=text.encode("utf-16-le"), check=True, timeout=5)
                return True
            except Exception:
                pass
        return False

    def paste(self) -> str | None:
        exe = _powershell()
        if exe is None:
            return None
        try:
            p = subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command", "Get-Clipboard -Raw"],
                capture_output=True, text=True, timeout=5, check=False,
            )
        except Exception:
            return None
        if p.returncode != 0:
            return None
        if not p.stdout:
            return None
        return p.stdout.rstrip("\r\n")
