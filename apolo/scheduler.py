"""Agendamento em segundo plano — API pública estável do Apolo.

Delega pro backend de `apolo.platform` conforme o sistema operacional (hoje só
Linux, via systemd --user — ver `apolo/platform/linux/scheduler.py`).
`INTERVALOS`/`INTERVALO_PADRAO` não são específicos de SO nenhum (são só as
opções mostradas na tela de Configurações), por isso ficam aqui e não no
backend.
"""

from __future__ import annotations

from pathlib import Path

from apolo.platform import get_scheduler
from apolo.platform.linux.scheduler import _systemctl  # usado direto por cli.py (daemon-reload)

# Intervalos oferecidos na UI. O setup aceita qualquer valor de uma unidade só
# (Nmin/Nh/Ns — ver o backend), não qualquer time-span combinado do systemd
# (tipo "1h30min"); a UI só mostra estes seis.
INTERVALOS = ["5min", "10min", "15min", "30min", "1h", "2h"]
INTERVALO_PADRAO = "15min"


def timer_ativo() -> bool:
    return get_scheduler().timer_ativo()


def intervalo_atual() -> str | None:
    return get_scheduler().intervalo_atual()


def escrever_units(interval: str) -> list[Path]:
    return get_scheduler().escrever_units(interval)


def ativar(interval: str) -> str:
    return get_scheduler().ativar(interval)


def desativar() -> str:
    return get_scheduler().desativar()
