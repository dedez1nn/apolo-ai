"""Prévia da cascata — o que cada regra pegaria, sem agir."""

from __future__ import annotations

from collections import defaultdict

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Footer, Static

from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER, ACAO_REVISAR, RuleEngine
from apolo.ui.model import ACAO_ICONE

_ACAO_COR = {ACAO_LIXEIRA: "tomato", ACAO_MANTER: "springgreen", ACAO_REVISAR: "gold"}


class PreviewScreen(Screen):
    BINDINGS = [Binding("escape,q", "app.pop_screen", "voltar")]

    def compose(self) -> ComposeResult:
        yield Static(id="prev-header")
        with VerticalScroll(id="prev-scroll"):
            yield Static(id="prev-body", markup=True)
        yield Footer()

    def on_mount(self) -> None:
        groups = self._compute()
        self._render(groups)

    def _compute(self) -> dict:
        engine = RuleEngine.from_file(self.app.rules_path)
        groups: dict[tuple[str, str], list] = defaultdict(list)
        for item in self.app.queue:
            dec = engine.classify(
                remetente=item.remetente,
                assunto=item.assunto,
                list_unsubscribe="",
            )
            groups[(dec.regra_casada, dec.acao_sugerida)].append(item)
        return groups

    def _render(self, groups: dict) -> None:
        n = len(self.app.queue)
        self.query_one("#prev-header", Static).update(
            f"[b]  Prévia da cascata[/]\n"
            f"  [dim]{n} email(s) na fila · simulação offline · sem ações[/]"
        )

        if not n:
            self.query_one("#prev-body", Static).update("  [dim](fila vazia)[/]")
            return

        linhas: list[str] = []
        for (regra, acao), itens in sorted(groups.items(), key=lambda kv: kv[0][1]):
            cor = _ACAO_COR.get(acao, "white")
            icone = ACAO_ICONE.get(acao, "")
            linhas.append(
                f"\n  [{cor}]{icone} {acao:<9}[/] [dim]·[/] [b]{regra}[/]  [dim]({len(itens)})[/]"
            )
            for it in itens[:10]:
                assunto = (it.assunto or "")[:50]
                linhas.append(f"   • {it.remetente[:36]:<36} [dim]{assunto}[/]")
            if len(itens) > 10:
                linhas.append(f"   [dim]… e mais {len(itens) - 10}[/]")

        self.query_one("#prev-body", Static).update("\n".join(linhas))
