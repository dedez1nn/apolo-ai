"""`Notifier` via notify-send (libnotify). Só stdlib.

É **best-effort**: se `notify-send` não existir (headless, sem D-Bus, ou
qualquer SO que não seja Linux) ou falhar, engole o erro e segue — notificação
é cosmético, nunca pode derrubar a triagem.

Quando rodando sob `systemd --user`, o D-Bus da sessão já vem no ambiente do
serviço, então o `notify-send` chega na área de trabalho normalmente.
"""

from __future__ import annotations

import shutil
import subprocess

_APP_NAME = "Apolo"


class LinuxNotifier:
    def notify(
        self,
        summary: str,
        body: str = "",
        *,
        urgency: str = "normal",
        expire_ms: int | None = None,
        replace_id: int | None = None,
    ) -> int | None:
        if shutil.which("notify-send") is None:
            return None

        cmd = ["notify-send", "--app-name", _APP_NAME, "--urgency", urgency, "--print-id"]
        if expire_ms is not None:
            cmd += ["--expire-time", str(expire_ms)]
        if replace_id is not None:
            cmd += ["--replace-id", str(replace_id)]
        cmd.append(summary)
        if body:
            cmd.append(body)

        try:
            out = subprocess.run(cmd, check=False, timeout=5, capture_output=True, text=True)
        except Exception:
            return None
        try:
            return int(out.stdout.strip())
        except (ValueError, AttributeError):
            return None
