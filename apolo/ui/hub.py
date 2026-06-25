"""Hub — a tela inicial que abre no clique da Waybar.

Menu navegável só com seta + Enter. Cada item leva a uma sub-tela. O que ainda
não foi construído (adicionar regra, prévia, regras, rodar) avisa com um toast,
pra deixar o fluxo honesto sem prometer tela que não existe.
"""

from __future__ import annotations

from datetime import datetime

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from apolo.ui.model import fmt_run

# (id, ícone, rótulo, pronto?) — ordem do menu.
_MENU = [
    ("review", "", "Revisar fila", True),
    ("add_rule", "", "Adicionar regra", False),
    ("preview", "", "Prévia — o que as regras pegariam", False),
    ("rules", "", "Regras configuradas", True),
    ("run", "", "Rodar agora (uma passada)", False),
    ("config", "", "Configurações", True),
    ("status", "", "Status & contadores", True),
]


class MenuItem(ListItem):
    def __init__(self, key: str, icone: str, rotulo: str, badge: str, pronto: bool):
        super().__init__(classes="menu-item" if pronto else "menu-item soon")
        self.key_id = key
        self._icone = icone
        self._rotulo = rotulo
        self._badge = badge

    def compose(self) -> ComposeResult:
        yield Label(self._icone, classes="mi-icone")
        yield Label(self._rotulo, classes="mi-rotulo")
        yield Label(self._badge, classes="mi-badge")


class HubScreen(Screen):
    BINDINGS = [
        Binding("q,escape", "sair", "sair"),
        Binding("enter", "abrir", "abrir", show=True),
        Binding("up,k", "cursor_up", "cima", show=False),
        Binding("down,j", "cursor_down", "baixo", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="hub"):
            with Center():
                yield Static(self._titulo(), id="hub-title")
            with Center():
                yield Static(self._subtitulo(), id="hub-stats")
            yield ListView(*self._itens(), id="hub-menu")
        yield Footer()

    # ----- montagem -----
    def on_mount(self) -> None:
        self.query_one("#hub-menu", ListView).focus()
        self.set_interval(1.0, self._tick_relogio)

    def _itens(self) -> list[MenuItem]:
        n_fila = len(self.app.queue)
        n_regras = self.app.stats.rules_count
        badges = {"review": str(n_fila) if n_fila else "", "rules": str(n_regras) if n_regras else ""}
        return [
            MenuItem(key, icone, rotulo, badges.get(key, ""), pronto)
            for key, icone, rotulo, pronto in _MENU
        ]

    # ----- textos do topo -----
    def _titulo(self) -> str:
        return f"  apolo     ·  triador de emails     {datetime.now().strftime('%H:%M')}"

    def _subtitulo(self) -> str:
        n = len(self.app.queue)
        fila = f"{n} na fila" if n else "fila vazia"
        return f"{fila}   ·   última passada {fmt_run(self.app.stats.last_run)}"

    def _tick_relogio(self) -> None:
        self.query_one("#hub-title", Static).update(self._titulo())

    # ----- ações -----
    def _atualizar(self) -> None:
        """Refaz topo + badges (a fila pode ter encolhido após revisar)."""
        self.query_one("#hub-stats", Static).update(self._subtitulo())
        menu = self.query_one("#hub-menu", ListView)
        idx = menu.index
        menu.clear()
        menu.extend(self._itens())
        if idx is not None:
            menu.index = idx

    def action_abrir(self) -> None:
        menu = self.query_one("#hub-menu", ListView)
        item = menu.highlighted_child
        if isinstance(item, MenuItem):
            self._rotear(item.key_id)

    def on_list_view_selected(self, event: ListView.Selected) -> None:
        if isinstance(event.item, MenuItem):
            self._rotear(event.item.key_id)

    def action_cursor_up(self) -> None:
        self.query_one("#hub-menu", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#hub-menu", ListView).action_cursor_down()

    def action_sair(self) -> None:
        self.app.exit()

    def _rotear(self, key: str) -> None:
        if key == "review":
            if not self.app.queue:
                self.notify("Fila vazia — nada pra revisar.", severity="information")
                return
            from apolo.ui.queue import QueueScreen

            self.app.push_screen(QueueScreen(), lambda _=None: self._atualizar())
        elif key == "rules":
            from apolo.ui.rules_screen import RulesScreen

            self.app.push_screen(RulesScreen(), lambda _=None: self._atualizar())
        elif key == "config":
            from apolo.ui.settings import SettingsScreen

            self.app.push_screen(SettingsScreen())
        elif key == "status":
            from apolo.ui.status import StatusScreen

            self.app.push_screen(StatusScreen())
        else:
            self.notify("Chega no próximo passo da UI 🚧", title=key, severity="warning")
