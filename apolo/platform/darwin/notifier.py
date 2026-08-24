"""`Notifier` via `osascript` (Notification Center do macOS) — vem de fábrica,
sem instalar nada.
"""

from __future__ import annotations

import shutil
import subprocess


def _as_escape(s: str) -> str:
    """Escapa pra dentro de uma string AppleScript de aspas duplas ("...")."""
    return s.replace("\\", "\\\\").replace('"', '\\"')


class DarwinNotifier:
    def notify(
        self,
        summary: str,
        body: str = "",
        *,
        urgency: str = "normal",
        expire_ms: int | None = None,
        replace_id: int | None = None,
    ) -> int | None:
        if shutil.which("osascript") is None:
            return None

        # Notification Center não tem replace_id/expire_ms endereçável via
        # osascript — ignorados, igual o backend Linux ignora o que
        # notify-send não suporta.
        script = f'display notification "{_as_escape(body or "")}" with title "{_as_escape(summary)}"'
        try:
            subprocess.run(
                ["osascript", "-e", script], check=False, timeout=5, capture_output=True
            )
        except Exception:
            return None
        return None
