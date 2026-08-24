"""Contrato de agendamento em segundo plano — um `Scheduler` por sistema
operacional.

`apolo/scheduler.py` é a API pública estável (usada por `cli.py` e pela tela
de Configurações); este módulo só define o contrato. Ver `apolo/platform/
linux/scheduler.py` pro único backend implementado até agora (systemd --user).
"""

from __future__ import annotations

from pathlib import Path
from typing import Protocol


class Scheduler(Protocol):
    def timer_ativo(self) -> bool:
        """True se o agendamento periódico está ligado agora."""
        ...

    def intervalo_atual(self) -> str | None:
        """Intervalo configurado (ex.: '15min'), ou None se nunca foi instalado."""
        ...

    def escrever_units(self, interval: str) -> list[Path]:
        """Grava a configuração do agendamento pro intervalo dado, sem ativá-la.

        Devolve os caminhos escritos.
        """
        ...

    def ativar(self, interval: str) -> str:
        """Escreve a configuração e liga o agendamento. Devolve uma mensagem
        de status legível pro dono."""
        ...

    def desativar(self) -> str:
        """Desliga o agendamento. Devolve uma mensagem de status legível."""
        ...
