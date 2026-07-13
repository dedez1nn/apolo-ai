"""SuggestionsScreen — dicas de regra baseadas no seu próprio histórico de despacho.

Cada linha é um padrão (domínio, ou domínio + assunto recorrente) que o dono já
decidiu de forma consistente no passado (apolo.suggest.gerar_sugestoes). Um
Switch por linha liga/desliga a sugestão; nada é gravado até sair da tela
(Esc/Q) — aí sim as ligadas viram entrada em allowlist/blocklist. "N" dispensa
uma sugestão pra sempre (grava na hora, não espera o Esc/Q).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Label, Static, Switch

from apolo.ui.model import ACAO_COR, ACAO_ICONE, ACAO_ROTULO
from apolo.ui.theme import AZURE_BRT, INK_DIM, INK_FAINT, keybar, mesc


class SuggestionRow(Horizontal):
    """Uma sugestão: texto (ícone/rótulo/contagem/frequência) + Switch."""

    def __init__(self, sugestao):
        super().__init__(classes="sug-row")
        self.sugestao = sugestao

    def compose(self) -> ComposeResult:
        s = self.sugestao
        cor = ACAO_COR.get(s.acao, AZURE_BRT)
        icone = ACAO_ICONE.get(s.acao, "·")
        rotulo = ACAO_ROTULO.get(s.acao, s.acao).upper()
        alvo = s.dominio if s.tipo == "dominio" else f"{s.dominio}  [{INK_FAINT}]“{mesc(s.assunto)}”[/]"
        texto = (
            f"[b {cor}]{icone} {rotulo:<8}[/]  {alvo}\n"
            f"  [{INK_FAINT}]{s.concordantes}/{s.total} · {s.frequencia}[/]"
        )
        yield Label(texto, classes="sug-txt", markup=True)
        sw = Switch(value=False, classes="sug-switch")
        sw.sugestao = s
        yield sw


class SuggestionsScreen(Screen):
    BINDINGS = [
        Binding("up,k", "focar_anterior", "cima", show=False),
        Binding("down,j", "focar_proxima", "baixo", show=False),
        Binding("n", "ignorar", "não mostrar mais"),
        Binding("escape,q", "aplicar_e_sair", "voltar"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="sug-header", classes="band")
        yield VerticalScroll(id="sug-list")
        yield Static(
            keybar([("↑↓", "Navegar"), ("␣", "Ligar/desligar"), ("N", "Não mostrar mais"), ("Q", "Voltar e salvar")]),
            classes="keybar",
        )

    async def on_mount(self) -> None:
        self._sugestoes: list = []
        await self._carregar()

    async def _carregar(self) -> None:
        if not self.app.config:
            return
        from apolo.storage.db import Storage
        from apolo.suggest import gerar_sugestoes

        with Storage(self.app.config.db_path) as store:
            rows = store.dispatched_rows()
            ignoradas = store.sugestoes_ignoradas()
        self._sugestoes = gerar_sugestoes(rows, rules_path=self.app.rules_path, ignoradas=ignoradas)
        await self._renderizar()

    async def _renderizar(self) -> None:
        self.query_one("#sug-header", Static).update(self._titulo())
        lista = self.query_one("#sug-list", VerticalScroll)
        await lista.remove_children()
        await lista.mount_all(SuggestionRow(s) for s in self._sugestoes)
        switches = self.query(Switch)
        if switches:
            switches.first().focus()

    def _titulo(self) -> str:
        n = len(self._sugestoes)
        corpo = f"{n} sugestão(ões) com base no seu histórico" if n else "sem sugestões novas por enquanto"
        return f"[b $accent]Sugestões[/]   [{INK_DIM}]{corpo}[/]"

    def action_focar_anterior(self) -> None:
        self.focus_previous()

    def action_focar_proxima(self) -> None:
        self.focus_next()

    async def action_ignorar(self) -> None:
        sw = self.app.focused
        if not isinstance(sw, Switch) or not hasattr(sw, "sugestao"):
            return
        from apolo.storage.db import Storage

        sug = sw.sugestao
        with Storage(self.app.config.db_path) as store:
            store.ignorar_sugestao(sug.chave)
        self._sugestoes = [s for s in self._sugestoes if s.chave != sug.chave]
        await self._renderizar()

    def action_aplicar_e_sair(self) -> None:
        from apolo.rules.engine import ACAO_LIXEIRA
        from apolo.rules.writer import add_rule_entry, list_entries

        aceitas = [sw.sugestao for sw in self.query(Switch) if sw.value]
        criadas = 0
        falhas = 0
        for s in aceitas:
            lista = "blocklist" if s.acao == ACAO_LIXEIRA else "allowlist"
            try:
                add_rule_entry(self.app.rules_path, lista=lista, tipo="dominio", valor=s.dominio)
                criadas += 1
            except Exception:
                falhas += 1
        if aceitas:
            self.app.stats.rules_count = len(list_entries(self.app.rules_path))
        partes = []
        if criadas:
            partes.append(f"{criadas} regra(s) criada(s)")
        if falhas:
            partes.append(f"{falhas} falha(s)")
        self.dismiss(", ".join(partes) if partes else None)
