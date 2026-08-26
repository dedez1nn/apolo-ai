"""Corpo do email — versão enxuta (Fase 1).

Substitui os 954 linhas do parser HTML→blocos de terminal da TUI antiga: sem
canvas de terminal pra desenhar em cima, um controle de texto nativo já
resolve. `WebView` do Flet não cobre Windows/Linux desktop (só
iOS/Android/macOS/Web), então o corpo vira texto limpo via
`apolo.clean.message_to_text` — mesma extração que a classificação já usa.
Resolução de imagem inline por CID fica pra Fase 2, se fizer falta.
"""

from __future__ import annotations

import asyncio

import flet as ft

from apolo.gui.model import Item, fmt_remetente
from apolo.gui.theme import COR_LIXEIRA, INK, INK_DIM
from apolo.gui.widgets import ESCAPE, key


class BodyViewModal:
    """`await BodyViewModal(app, item).show()` — não devolve nada, só fecha em Esc."""

    def __init__(self, app, item: Item):
        self.app = app
        self.item = item
        self._future: asyncio.Future | None = None
        self._shown = False

        self.body_text = ft.Text("Buscando o email…", size=13, color=INK_DIM, selectable=True)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(fmt_remetente(item.remetente), color=INK, weight=ft.FontWeight.BOLD, size=14),
            content=ft.Container(
                content=ft.Column([self.body_text], scroll=ft.ScrollMode.AUTO),
                width=560, height=420,
            ),
            actions=[ft.TextButton("Fechar (Esc)", on_click=lambda e: self._resolve())],
        )

    def on_key(self, e: ft.KeyboardEvent) -> None:
        if key(e) in ESCAPE:
            self._resolve()

    def _resolve(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.set_result(None)
        self.app.close_dialog()

    async def show(self) -> None:
        self._future = asyncio.get_running_loop().create_future()
        self.app.open_dialog(self.dialog, key_handler=self.on_key)
        self._shown = True
        self.app.page.run_thread(self._buscar)
        await self._future

    def _buscar(self) -> None:
        from apolo.actions import fetch_body

        try:
            texto = fetch_body(self.app.config, self.item) or "(corpo vazio)"
        except Exception as exc:
            texto = f"erro ao buscar: {exc}"
            self.body_text.color = COR_LIXEIRA
        self.body_text.value = texto[:20000]
        if self._shown:
            self.body_text.update()
