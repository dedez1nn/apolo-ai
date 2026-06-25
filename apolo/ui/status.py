"""Status — leitura pura dos contadores que o cli juntou antes de abrir o app."""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import Screen
from textual.widgets import Footer, Static

from apolo.ui.model import fmt_run


class StatusScreen(Screen):
    BINDINGS = [Binding("escape,q", "app.pop_screen", "voltar")]

    def compose(self) -> ComposeResult:
        st = self.app.stats
        linhas = [f"[b]  Status[/]\n", f"  última passada   [b]{fmt_run(st.last_run)}[/]",
                  f"  na fila          [b]{len(self.app.queue)}[/]",
                  f"  regras           [b]{st.rules_count}[/]\n"]
        if st.status_counts:
            linhas.append("  [dim]por status[/]")
            for status, n in sorted(st.status_counts.items()):
                linhas.append(f"    {status:<14} {n}")
        if st.acao_counts:
            linhas.append("\n  [dim]ação sugerida[/]")
            for acao, n in sorted(st.acao_counts.items()):
                linhas.append(f"    {acao:<14} {n}")
        with Vertical(id="status-box"):
            yield Static("\n".join(linhas))
        yield Footer()
