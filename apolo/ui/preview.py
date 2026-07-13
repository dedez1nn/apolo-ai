"""Prévia da cascata — o que cada regra pegaria, sem agir."""

from __future__ import annotations

from collections import defaultdict

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import VerticalScroll
from textual.screen import Screen
from textual.widgets import Static

from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER, ACAO_REVISAR, RuleEngine
from apolo.ui.model import ACAO_ICONE, fmt_remetente
from apolo.ui.theme import COR_LIXEIRA, COR_MANTER, COR_REVISAR, INK_DIM, INK_FAINT, keybar, mesc

_ACAO_COR = {ACAO_LIXEIRA: COR_LIXEIRA, ACAO_MANTER: COR_MANTER, ACAO_REVISAR: COR_REVISAR}


class PreviewScreen(Screen):
    BINDINGS = [Binding("escape,q", "app.pop_screen", "voltar")]

    def compose(self) -> ComposeResult:
        yield Static(id="prev-header", classes="band")
        with VerticalScroll(id="prev-scroll"):
            yield Static(id="prev-body", markup=True)
        yield Static(keybar([("Q", "Voltar")]), classes="keybar")

    def on_mount(self) -> None:
        groups = self._compute()
        self._render_grupos(groups)

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

    def _render_grupos(self, groups: dict) -> None:
        n = len(self.app.queue)
        self.query_one("#prev-header", Static).update(
            f"[b $accent]Prévia da cascata[/]\n"
            f"[{INK_DIM}]{n} email(s) na fila · simulação offline · sem ações[/]"
        )

        if not n:
            self.query_one("#prev-body", Static).update(f"[{INK_FAINT}](fila vazia)[/]")
            return

        linhas: list[str] = []
        for (regra, acao), itens in sorted(groups.items(), key=lambda kv: kv[0][1]):
            cor = _ACAO_COR.get(acao, "white")
            icone = ACAO_ICONE.get(acao, "·")
            linhas.append(
                f"\n[{cor}]{icone} {acao:<9}[/] [{INK_FAINT}]·[/] [b]{regra}[/]  [{INK_FAINT}]({len(itens)})[/]"
            )
            for it in itens[:10]:
                assunto = mesc((it.assunto or "")[:50])
                rem = mesc(f"{fmt_remetente(it.remetente)[:36]:<36}")
                linhas.append(f"   [{cor}]▌[/] {rem} [{INK_FAINT}]{assunto}[/]")
            if len(itens) > 10:
                linhas.append(f"   [{INK_FAINT}]… e mais {len(itens) - 10}[/]")

        self.query_one("#prev-body", Static).update("\n".join(linhas))
