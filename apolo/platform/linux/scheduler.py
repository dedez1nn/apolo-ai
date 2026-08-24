"""`Scheduler` via systemd --user (timer + service).

Render das units a partir dos templates em `apolo/systemd/`, detectando
interpretador e raiz do projeto na hora (não chumba caminho nem venv). Tudo
stdlib.
"""

from __future__ import annotations

import re
import shutil
import subprocess
import sys
from pathlib import Path

import apolo

# Ancorado no pacote (não em __file__ deste módulo) pra continuar correto não
# importa a profundidade do backend dentro de apolo/platform/<so>/.
_APOLO_DIR = Path(apolo.__file__).resolve().parent
_TEMPLATE_DIR = _APOLO_DIR / "systemd"
_PROJECT_ROOT = _APOLO_DIR.parent
_USER_UNIT_DIR = Path("~/.config/systemd/user").expanduser()

# Tradução pra sintaxe OnCalendar (relógio de parede) — ver apolo.timer sobre
# por que não é mais OnBootSec/OnUnitActiveSec (monotônico). "1h" tem forma
# canônica (hourly); o resto vira "campo/passo" — sintaxe válida mesmo quando
# o passo não divide a unidade inteiramente (ex.: "*:0/7" pra 7min).
_ON_CALENDAR_ESPECIAIS = {"1h": "hourly"}
_INTERVALO_RE = re.compile(r"^(\d+)(min|h|s)$")


def _on_calendar_for(interval: str) -> str:
    if interval in _ON_CALENDAR_ESPECIAIS:
        return _ON_CALENDAR_ESPECIAIS[interval]
    m = _INTERVALO_RE.match(interval)
    if not m:
        raise ValueError(
            f"intervalo {interval!r} não reconhecido — use algo como '15min', '1h' ou '30s' "
            "(uma unidade só; não combine tipo '1h30min')."
        )
    n, unidade = m.group(1), m.group(2)
    if unidade == "min":
        return f"*:0/{n}"
    if unidade == "h":
        return f"0/{n}:00:00"
    return f"*:*:0/{n}"  # s


def _systemctl(*args: str) -> tuple[int, str]:
    """Roda `systemctl --user ...`; devolve (rc, saída). rc 127 se ausente."""
    if shutil.which("systemctl") is None:
        return 127, ""
    p = subprocess.run(
        ["systemctl", "--user", *args], capture_output=True, text=True, check=False
    )
    return p.returncode, (p.stdout or p.stderr).strip()


class LinuxScheduler:
    def timer_ativo(self) -> bool:
        _, out = _systemctl("is-active", "apolo.timer")
        return out == "active"

    def intervalo_atual(self) -> str | None:
        """Lê o intervalo original (comentário "# Interval=") da unit instalada."""
        unit = _USER_UNIT_DIR / "apolo.timer"
        if not unit.is_file():
            return None
        for raw in unit.read_text(encoding="utf-8").splitlines():
            linha = raw.strip()
            if linha.startswith("# Interval="):
                return linha.split("=", 1)[1].strip()
        return None

    def escrever_units(self, interval: str) -> list[Path]:
        """Renderiza apolo.service + apolo.timer em ~/.config/systemd/user."""
        campos = {
            "python": sys.executable,
            "workdir": str(_PROJECT_ROOT),
            "interval": interval,
            "on_calendar": _on_calendar_for(interval),
        }
        _USER_UNIT_DIR.mkdir(parents=True, exist_ok=True)
        escritos = []
        for nome in ("apolo.service", "apolo.timer"):
            template = (_TEMPLATE_DIR / nome).read_text(encoding="utf-8")
            destino = _USER_UNIT_DIR / nome
            destino.write_text(template.format(**campos), encoding="utf-8")
            escritos.append(destino)
        return escritos

    def ativar(self, interval: str) -> str:
        """Escreve as units e liga o timer. Reentrante (regrava pra trocar intervalo)."""
        self.escrever_units(interval)
        if shutil.which("systemctl") is None:
            return "systemctl ausente — units escritas, não ativadas."
        _systemctl("daemon-reload")
        _systemctl("enable", "--now", "apolo.timer")
        return f"timer ativo — a cada {interval}."

    def desativar(self) -> str:
        if shutil.which("systemctl") is None:
            return "systemctl ausente."
        _systemctl("disable", "--now", "apolo.timer")
        return "timer desativado."
