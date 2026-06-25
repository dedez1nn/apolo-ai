"""App Textual do Apolo — o hub que abre no clique da Waybar.

Fluxo: a Waybar abre `apolo review`, que entra aqui. A tela inicial (Hub) é
navegável só com seta + Enter; dela o dono escolhe o que fazer. Tudo dark, com
ícones e cores por ação, focado em teclado.

Esta camada é **offline**: lê a fila (rows) e escreve regras no TOML. O dispatch
real via IMAP acontece depois que o app fecha, no `cli`, com as decisões que o
app devolve em `dispatch_items`. Mantém o princípio "a TUI não toca a rede".
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

from textual.app import App

from apolo.actions import DispatchItem
from apolo.ui.hub import HubScreen
from apolo.ui.model import Item


@dataclass
class UiStats:
    """Números que o Hub e a tela de Status mostram (lidos no cli, antes de abrir)."""

    last_run: str | None = None
    rules_count: int = 0
    status_counts: dict[str, int] | None = None
    acao_counts: dict[str, int] | None = None


class ApoloApp(App):
    """App raiz. Carrega o tema dark e guarda o estado compartilhado entre telas."""

    CSS_PATH = "app.tcss"
    TITLE = "apolo"
    SUB_TITLE = "triador de emails"

    def __init__(self, rows, rules_path: Path, stats: UiStats, config=None):
        super().__init__()
        # Fila compartilhada: as telas mutam esta lista (decidir/desfazer).
        self.queue: list[Item] = [Item(r) for r in rows]
        self.rules_path = Path(rules_path)
        self.stats = stats
        self.config = config  # Config (paths, IA, pastas) — usado pela tela de ajustes.
        # Decisões confirmadas (Enter na fila) que o cli vai despachar via IMAP.
        self.dispatch_items: list[DispatchItem] = []

    def on_mount(self) -> None:
        self.theme = "textual-dark"
        self.push_screen(HubScreen())


def run_ui(rows, rules_path: Path, stats: UiStats, config=None) -> list[DispatchItem]:
    """Abre o app; devolve os itens a despachar (lixeira/manter) ao fechar."""
    app = ApoloApp(rows, rules_path, stats, config)
    app.run()
    return app.dispatch_items
