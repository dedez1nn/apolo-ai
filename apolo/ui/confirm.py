"""Modal de confirmação genérico (sim/não) — usado antes de ações irreversíveis,
como excluir um email favoritado (ver `queue.py` e `swipe_screen.py`).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen
from textual.widgets import Static

from apolo.ui.theme import AMBER, keybar


class ConfirmModal(ModalScreen[bool]):
    """Mostra `mensagem` e devolve True (confirma) ou False (cancela) ao fechar."""

    BINDINGS = [
        Binding("y,enter", "confirmar", "sim", priority=True),
        Binding("n,escape,q", "cancelar", "não", priority=True),
    ]

    def __init__(self, mensagem: str, titulo: str = "Confirmar"):
        super().__init__()
        self._mensagem = mensagem
        self._titulo = titulo

    def compose(self) -> ComposeResult:
        with Vertical(id="confirm-box"):
            yield Static(f"[b {AMBER}]{self._titulo}[/]", classes="cfg-title")
            yield Static(self._mensagem, id="confirm-msg")
            yield Static(keybar([("Y/↵", "Sim"), ("N/Esc", "Não")]), classes="keybar")

    def action_confirmar(self) -> None:
        self.dismiss(True)

    def action_cancelar(self) -> None:
        self.dismiss(False)
