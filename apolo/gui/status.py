"""Status: leitura pura dos contadores que o cli juntou antes de abrir o app."""

from __future__ import annotations

import flet as ft

from apolo.gui.model import ACAO_COR, fmt_run
from apolo.gui.theme import GUTTER, INK, INK_DIM, INK_FAINT, SOL
from apolo.gui.widgets import header, keybar, scaffold


class StatusScreen:
    def __init__(self) -> None:
        self.on_close = None

    def build(self) -> ft.Control:
        st = self.app.stats
        linhas = [
            _linha("última passada", fmt_run(st.last_run)),
            _linha("na fila", str(len(self.app.queue))),
            _linha("regras", str(st.rules_count)),
        ]
        if st.status_counts:
            linhas.append(ft.Text("por status", size=11, color=INK_FAINT))
            for status, n in sorted(st.status_counts.items()):
                linhas.append(_linha(status, str(n), indent=True))
        if st.acao_counts:
            linhas.append(ft.Text("ação sugerida", size=11, color=INK_FAINT))
            for acao, n in sorted(st.acao_counts.items()):
                linhas.append(_linha_acao(acao, n))
        body = ft.Column(linhas, spacing=6, scroll=ft.ScrollMode.AUTO)
        return scaffold(
            header("Status & contadores"),
            body,
            keybar([("Esc", "Voltar")]),
        )

    def on_key(self, e: ft.KeyboardEvent) -> None:
        if e.key.lower() in ("escape", "q"):
            (self.on_close or self.app.pop_screen)()


def _linha(rotulo: str, valor: str, indent: bool = False) -> ft.Row:
    return ft.Row(
        [
            ft.Text(rotulo, size=13, color=INK_DIM, width=180 if not indent else 160),
            ft.Text(valor, size=13, color=INK, weight=ft.FontWeight.BOLD),
        ]
    )


def _linha_acao(acao: str, n: int) -> ft.Row:
    cor = ACAO_COR.get(acao, SOL)
    return ft.Row(
        [
            ft.Text(GUTTER, color=cor),
            ft.Text(acao, size=13, color=INK_DIM, width=150),
            ft.Text(str(n), size=13, color=INK, weight=ft.FontWeight.BOLD),
        ]
    )
