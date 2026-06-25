"""Controle do systemd timer (user) — usado pelo `apolo setup` e pela UI.

Render das units a partir dos templates em `systemd/`, detectando interpretador e
raiz do projeto na hora (não chumba caminho nem venv). Tudo stdlib.
"""

from __future__ import annotations

import shutil
import subprocess
import sys
from pathlib import Path

_TEMPLATE_DIR = Path(__file__).resolve().parent / "systemd"
_USER_UNIT_DIR = Path("~/.config/systemd/user").expanduser()

# Intervalos oferecidos na UI (o setup aceita qualquer string do systemd).
INTERVALOS = ["5min", "10min", "15min", "30min", "1h", "2h"]
INTERVALO_PADRAO = "15min"


def _systemctl(*args: str) -> tuple[int, str]:
    """Roda `systemctl --user ...`; devolve (rc, saída). rc 127 se ausente."""
    if shutil.which("systemctl") is None:
        return 127, ""
    p = subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, check=False
    )
    return p.returncode, (p.stdout or p.stderr).strip()


def timer_ativo() -> bool:
    _, out = _systemctl("is-active", "apolo.timer")
    return out == "active"


def intervalo_atual() -> str | None:
    """Lê OnUnitActiveSec da unit instalada; None se ainda não instalada."""
    unit = _USER_UNIT_DIR / "apolo.timer"
    if not unit.is_file():
        return None
    for raw in unit.read_text(encoding="utf-8").splitlines():
        linha = raw.strip()
        if linha.startswith("OnUnitActiveSec="):
            return linha.split("=", 1)[1].strip()
    return None


def escrever_units(interval: str) -> list[Path]:
    """Renderiza apolo.service + apolo.timer em ~/.config/systemd/user."""
    workdir = Path(__file__).resolve().parent.parent
    campos = {"python": sys.executable, "workdir": str(workdir), "interval": interval}
    _USER_UNIT_DIR.mkdir(parents=True, exist_ok=True)
    escritos = []
    for nome in ("apolo.service", "apolo.timer"):
        template = (_TEMPLATE_DIR / nome).read_text(encoding="utf-8")
        destino = _USER_UNIT_DIR / nome
        destino.write_text(template.format(**campos), encoding="utf-8")
        escritos.append(destino)
    return escritos


def ativar(interval: str) -> str:
    """Escreve as units e liga o timer. Reentrante (regrava pra trocar intervalo)."""
    escrever_units(interval)
    if shutil.which("systemctl") is None:
        return "systemctl ausente — units escritas, não ativadas."
    _systemctl("daemon-reload")
    _systemctl("enable", "--now", "apolo.timer")
    return f"timer ativo — a cada {interval}."


def desativar() -> str:
    if shutil.which("systemctl") is None:
        return "systemctl ausente."
    _systemctl("disable", "--now", "apolo.timer")
    return "timer desativado."
