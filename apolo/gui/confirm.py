"""Modal de confirmação genérico (sim/não), usado antes de ações irreversíveis,
como excluir um email favoritado (ver `queue.py`).
"""

from __future__ import annotations

import asyncio

import flet as ft

from apolo.gui.theme import AMBAR, INK


class ConfirmModal:
    """`resultado = await ConfirmModal(app, "...").ask()` -> True (confirma) / False (cancela)."""

    def __init__(self, app, mensagem: str, titulo: str = "Confirmar"):
        self.app = app
        self._future: asyncio.Future | None = None
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(titulo, color=AMBAR, weight=ft.FontWeight.BOLD),
            content=ft.Text(mensagem, color=INK, width=380),
            actions=[
                ft.TextButton("Não (N/Esc)", on_click=lambda e: self._resolve(False)),
                ft.FilledButton("Sim (Y/↵)", on_click=lambda e: self._resolve(True)),
            ],
        )

    def _resolve(self, value: bool) -> None:
        if self._future is not None and not self._future.done():
            self._future.set_result(value)
        self.app.close_dialog()

    def on_key(self, e: ft.KeyboardEvent) -> None:
        k = e.key.lower()
        if k in ("y", "enter"):
            self._resolve(True)
        elif k in ("n", "escape"):
            self._resolve(False)

    async def ask(self) -> bool:
        # Future criada aqui dentro (não no __init__): get_running_loop()
        # só é seguro chamado de dentro de uma coroutine já rodando; no
        # __init__ (síncrono) não há garantia de qual thread/loop está ativo.
        self._future = asyncio.get_running_loop().create_future()
        self.app.open_dialog(self.dialog, key_handler=self.on_key)
        return await self._future
