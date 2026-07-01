"""Status — leitura pura dos contadores que o cli juntou antes de abrir o app."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from apolo.ui.model import ACAO_COR, fmt_run
from apolo.ui.theme import AZURE_BRT, GUTTER, INK_DIM, INK_FAINT, keybar


class StatusScreen(Screen):
    BINDINGS = [Binding("escape,q", "app.pop_screen", "voltar")]

    def compose(self) -> ComposeResult:
        st = self.app.stats
        linhas = [
            f"[{INK_DIM}]última passada[/]   [b]{fmt_run(st.last_run)}[/]",
            f"[{INK_DIM}]na fila[/]          [b]{len(self.app.queue)}[/]",
            f"[{INK_DIM}]regras[/]           [b]{st.rules_count}[/]",
        ]
        if st.status_counts:
            linhas.append(f"\n[{INK_FAINT}]por status[/]")
            for status, n in sorted(st.status_counts.items()):
                linhas.append(f"  {status:<14} [b]{n}[/]")
        if st.acao_counts:
            linhas.append(f"\n[{INK_FAINT}]ação sugerida[/]")
            for acao, n in sorted(st.acao_counts.items()):
                cor = ACAO_COR.get(acao, AZURE_BRT)
                linhas.append(f"  [{cor}]{GUTTER}[/] {acao:<12} [b]{n}[/]")

        yield Static("[b $accent]Status & contadores[/]", id="status-header", classes="band")
        with VerticalScroll(id="status-scroll"):
            yield Static("\n".join(linhas), id="status-body", markup=True)
        yield Static(keybar([("Q", "Voltar")]), classes="keybar")
