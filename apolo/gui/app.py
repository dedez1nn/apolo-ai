"""App Flet do Apolo — janela nativa que abre no clique da bandeja/Waybar.

Fase 1: substitui a TUI Textual (`apolo/ui/`, apagada) por um app desktop de
verdade — sem console, sem terminal no meio. Mantém o mesmo contrato que o
`cli.py` já chamava: `run_ui(rows, rules_path, stats, config, contas_ativas)
-> list[DispatchItem]`, bloqueante, devolvendo os itens a despachar (fallback;
o despacho normal já acontece dentro da própria tela de fila, ao aplicar).

Navegação: pilha manual de "telas" (cada uma monta um `ft.View`) — Hub no
fundo, o resto empilha por cima; `Esc` volta. Modais (diálogos) empilham só um
handler de teclado, sem entrar em `page.views`. Um único `page.on_keyboard_event`
sempre despacha pro topo da pilha — é o equivalente ao foco exclusivo do
`ModalScreen` do Textual.
"""

from __future__ import annotations

import logging
from dataclasses import dataclass
from pathlib import Path

import flet as ft

from apolo.actions import DispatchItem
from apolo.gui.model import Item
from apolo.gui.theme import BG, SOL, SOL_INK, SURFACE_2, TERRACOTA

logger = logging.getLogger("apolo.gui.app")


@dataclass
class UiStats:
    """Números que o Hub e a tela de Status mostram (lidos no cli, antes de abrir)."""

    last_run: str | None = None
    rules_count: int = 0
    status_counts: dict[str, int] | None = None
    acao_counts: dict[str, int] | None = None


class ApoloApp:
    """Estado raiz compartilhado entre telas + pilha de navegação/teclado."""

    def __init__(self, rows, rules_path: Path, stats: UiStats, config=None, contas_ativas: set | None = None):
        self.queue: list[Item] = [Item(r) for r in rows]
        self.rules_path = Path(rules_path)
        self.stats = stats
        self.config = config
        self.dispatch_items: list[DispatchItem] = []
        self._contas_ativas: set[str] = contas_ativas or {"proton"}
        self.contas_invalidas: dict[str, str] = {}
        self.last_queue_key: tuple | None = None

        self.page: ft.Page | None = None
        self.stack: list = []
        self._pop_callbacks: list = []
        self.key_handlers: list = []

    # ----- entrypoint -----
    def main(self, page: ft.Page) -> None:
        self.page = page
        page.title = "Apolo"
        page.window.width = 1000
        page.window.height = 720
        page.window.min_width = 760
        page.window.min_height = 520
        page.theme_mode = ft.ThemeMode.DARK
        page.bgcolor = BG
        page.padding = 0
        page.on_keyboard_event = self._on_key

        from apolo.gui.hub import HubScreen

        self.push_screen(HubScreen())
        page.run_thread(self._checar_contas_gmail)

    def _checar_contas_gmail(self) -> None:
        """Testa o token de cada conta Gmail em segundo plano ao abrir o app.

        Exceção ao princípio "a UI não toca a rede" (como o sync embutido):
        detectar um token revogado aqui evita que o dono só descubra no meio
        de uma sincronização falhando.
        """
        if self.config is None:
            return
        from apolo.config import load_accounts
        from apolo.fetch.gmail import GmailClient

        for account in load_accounts(self.config.accounts_path):
            if account.provider != "gmail":
                continue
            client = GmailClient(
                account.name, account.client_id, account.client_secret,
                self.config.tokens_dir / f"{account.name}.json", folders=account.folders,
            )
            motivo = client.check_token()
            if motivo:
                self.contas_invalidas[f"gmail:{account.name}"] = motivo
                self.notify(
                    f"gmail:{account.name}: {motivo}\n"
                    "Reautorize a conta em Regras/Configurações — até lá, "
                    "sincronizar ignora essa conta.",
                    severity="warning",
                )

    # ----- navegação -----
    def push_screen(self, screen, on_pop=None) -> None:
        screen.app = self
        self.stack.append(screen)
        self._pop_callbacks.append(on_pop)
        self.key_handlers.append(screen.on_key)
        self._rebuild_views()
        if hasattr(screen, "on_show"):
            screen.on_show()

    def pop_screen(self, result=None) -> None:
        if len(self.stack) <= 1:
            return
        self.stack.pop()
        self.key_handlers.pop()
        cb = self._pop_callbacks.pop()
        self._rebuild_views()
        top = self.stack[-1]
        if hasattr(top, "on_show"):
            top.on_show()
        if cb:
            cb(result)

    def refresh_top(self) -> None:
        """Reconstrói só a tela do topo da pilha (dados podem ter mudado)."""
        self._rebuild_views()

    def _rebuild_views(self) -> None:
        views = []
        for i, screen in enumerate(self.stack):
            views.append(
                ft.View(
                    route=f"/{i}-{screen.__class__.__name__}",
                    controls=[screen.build()],
                    bgcolor=BG,
                    padding=0,
                )
            )
        self.page.views = views
        self.page.update()

    def exit(self) -> None:
        # Window.close() é uma coroutine -- chamado direto (sem await) desde um
        # handler de teclado síncrono, a corrotina nunca roda e a janela não
        # fecha. run_task agenda no loop da page, que é seguro daqui.
        self.page.run_task(self.page.window.close)

    # ----- teclado (topo da pilha sempre vence) -----
    def _on_key(self, e: ft.KeyboardEvent) -> None:
        if self.key_handlers:
            self.key_handlers[-1](e)

    # ----- diálogos (modais empilham handler de teclado, sem entrar em views) -----
    def open_dialog(self, dialog: ft.AlertDialog, key_handler=None) -> None:
        self.key_handlers.append(key_handler or (lambda e: None))
        self.page.show_dialog(dialog)

    def close_dialog(self) -> None:
        self.page.pop_dialog()
        if self.key_handlers:
            self.key_handlers.pop()

    def notify(self, text: str, severity: str = "information") -> None:
        cor_fundo = {"error": TERRACOTA, "warning": SOL}[severity] if severity in ("error", "warning") else SURFACE_2
        cor_texto = SOL_INK if severity == "warning" else "#FFFFFF" if severity == "error" else None
        self.page.show_dialog(
            ft.SnackBar(ft.Text(text, color=cor_texto), bgcolor=cor_fundo, open=True)
        )


def run_ui(rows, rules_path: Path, stats: UiStats, config=None, contas_ativas: set | None = None) -> list[DispatchItem]:
    """Abre o app; devolve os itens a despachar (lixeira/manter) ao fechar."""
    app = ApoloApp(rows, rules_path, stats, config, contas_ativas=contas_ativas)
    try:
        ft.run(app.main)
    except Exception:
        logger.exception("app desktop caiu")
        raise
    return app.dispatch_items
