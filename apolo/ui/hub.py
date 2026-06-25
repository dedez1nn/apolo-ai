"""Hub — a tela inicial que abre no clique da Waybar.

Menu navegável só com seta + Enter. Cada item leva a uma sub-tela.
"""

from __future__ import annotations

import contextlib
import io
from datetime import datetime

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from apolo.ui.model import Item, fmt_run

# (id, ícone, rótulo, pronto?) — ordem do menu.
_MENU = [
    ("review", "", "Revisar fila", True),
    ("add_rule", "", "Adicionar regra", True),
    ("preview", "", "Prévia — o que as regras pegariam", True),
    ("rules", "", "Regras configuradas", True),
    ("run", "", "Rodar agora (uma passada)", True),
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
        elif key == "add_rule":
            from apolo.ui.rules_screen import AddRuleModal

            def _cb_add(resultado) -> None:
                self._atualizar()
                if resultado:
                    lista, tipo, valor, status = resultado
                    verbo = "já existia" if status == "exists" else "adicionada"
                    self.notify(f"{lista}: {tipo} {valor} {verbo}", severity="information")

            self.app.push_screen(AddRuleModal(), _cb_add)
        elif key == "preview":
            from apolo.ui.preview import PreviewScreen

            self.app.push_screen(PreviewScreen())
        elif key == "rules":
            from apolo.ui.rules_screen import RulesScreen

            self.app.push_screen(RulesScreen(), lambda _=None: self._atualizar())
        elif key == "run":
            if not self.app.config:
                self.notify("Configuração não carregada.", severity="error")
                return

            def _cb_run(resultado: str | None) -> None:
                if resultado:
                    sev = "error" if resultado.startswith("erro:") else "information"
                    self.notify(resultado[:120], title="apolo run", severity=sev)
                self._atualizar()

            self.app.push_screen(RunModal(), _cb_run)
        elif key == "config":
            from apolo.ui.settings import SettingsScreen

            self.app.push_screen(SettingsScreen())
        elif key == "status":
            from apolo.ui.status import StatusScreen

            self.app.push_screen(StatusScreen())


class RunModal(ModalScreen):
    """Executa `apolo run` numa thread e fecha ao terminar."""

    def compose(self) -> ComposeResult:
        with Vertical(id="run-box"):
            yield Static("[b]  Rodar agora[/]", classes="cfg-title")
            yield Static("  Buscando emails e classificando…", id="run-msg")
            yield Static("  [dim](pode levar alguns segundos)[/]")

    def on_mount(self) -> None:
        self._executar()

    @work(thread=True)
    def _executar(self) -> None:
        from apolo.cli import cmd_run

        buf = io.StringIO()
        resultado = "concluído."
        try:
            with contextlib.redirect_stdout(buf):
                cmd_run(self.app.config, notify_enabled=False)
            saida = buf.getvalue().strip()
            if saida:
                resultado = saida
        except Exception as exc:
            resultado = f"erro: {exc}"

        self.app.call_from_thread(self._apos_run, resultado)

    def _apos_run(self, resultado: str) -> None:
        from apolo.storage.db import Storage

        try:
            with Storage(self.app.config.db_path) as store:
                rows = store.fetch_queue()
                self.app.queue = [Item(r) for r in rows]
                self.app.stats.last_run = store.last_processed_at()
        except Exception:
            pass
        self.dismiss(resultado)
