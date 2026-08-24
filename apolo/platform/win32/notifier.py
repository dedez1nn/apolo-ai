"""`Notifier` via PowerShell + `System.Windows.Forms.NotifyIcon` (balão na
bandeja do sistema).

Sem dependência nova: `System.Windows.Forms` é parte do .NET Framework, que
todo Windows com PowerShell já tem. Alternativa mais "nativa" seria toast via
`Windows.UI.Notifications` (WinRT), mas exige um AppUserModelID registrado
pra aparecer de forma confiável — o balão da bandeja funciona sem esse
cadastro prévio, o que importa mais aqui do que o visual mais moderno.

Best-effort, como o backend Linux: se PowerShell não existir ou falhar,
engole o erro e devolve None — notificação é cosmético.
"""

from __future__ import annotations

import shutil
import subprocess


def _ps_escape(s: str) -> str:
    """Escapa pra dentro de uma string PowerShell de aspas simples ('...')."""
    return s.replace("'", "''")


class Win32Notifier:
    def notify(
        self,
        summary: str,
        body: str = "",
        *,
        urgency: str = "normal",
        expire_ms: int | None = None,
        replace_id: int | None = None,
    ) -> int | None:
        exe = shutil.which("powershell") or shutil.which("pwsh")
        if exe is None:
            return None

        # expire_ms/replace_id/urgency não têm equivalente direto nesse
        # mecanismo (balão da bandeja não é endereçável por id) — ignorados,
        # igual o backend Linux ignora o que notify-send não suporta.
        script = (
            "Add-Type -AssemblyName System.Windows.Forms; "
            "$n = New-Object System.Windows.Forms.NotifyIcon; "
            "$n.Icon = [System.Drawing.SystemIcons]::Information; "
            "$n.Visible = $true; "
            f"$n.BalloonTipTitle = '{_ps_escape(summary)}'; "
            f"$n.BalloonTipText = '{_ps_escape(body or summary)}'; "
            "$n.ShowBalloonTip(8000); "
            "Start-Sleep -Seconds 1; "
            "$n.Dispose();"
        )
        try:
            subprocess.run(
                [exe, "-NoProfile", "-NonInteractive", "-Command", script],
                check=False, timeout=10, capture_output=True,
            )
        except Exception:
            return None
        return None  # balão não expõe id pra substituir depois (replace_id)
