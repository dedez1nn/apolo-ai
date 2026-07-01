"""SyncScreen — tela do botão "Sincronizar": full-scan ao vivo de todas as contas.

Roda `apolo.sync.run_sync` numa thread; cada email descoberto aparece na lista
assim que a cascata de regras decide, e — se ela não resolver (regra_casada
'default') — a linha muda pra "analisando" enquanto o Ollama processa e depois
pra ação final. Sair (Q/Esc) só fecha a tela; o sync em si já terminou de gravar
no banco antes do evento "fim" atualizar a fila do app.
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Label, ListItem, ListView, Static

from apolo.ui.model import ACAO_COR, ACAO_ICONE, ACAO_ROTULO
from apolo.ui.theme import AMBER, AZURE_BRT, COR_LIXEIRA, INK_DIM, INK_FAINT, keybar

_STATUS_ICONE = {**ACAO_ICONE, "analisando": "…"}
_STATUS_COR = {**ACAO_COR, "analisando": AMBER}
_STATUS_ROTULO = {**ACAO_ROTULO, "analisando": "analisando"}


class SyncRow(ListItem):
    """Duas linhas, igual EmailRow — mas o status muda ao vivo (novo→analisando→final)."""

    def __init__(self, item, mostrar_badge: bool):
        super().__init__(classes="email-row")
        self.item = item
        self._mostrar_badge = mostrar_badge

    def compose(self) -> ComposeResult:
        yield Label(self._linha1(), classes="er-top", markup=True)
        yield Label(self._linha2(), classes="er-sub")

    def _linha1(self) -> str:
        it = self.item
        cor = _STATUS_COR.get(it.status, AZURE_BRT)
        icone = _STATUS_ICONE.get(it.status, "·")
        tag = _STATUS_ROTULO.get(it.status, it.status).upper()
        badge = f"[{INK_FAINT}][{it.conta}][/] " if self._mostrar_badge else ""
        rem = it.remetente or "(sem remetente)"
        return f"[b {cor}]{icone} {tag:<10}[/]  {badge}{rem}"

    def _linha2(self) -> str:
        assunto = self.item.assunto or "(sem assunto)"
        return f"      [{INK_DIM}]{assunto}[/]"

    def refresh_text(self) -> None:
        self.query_one(".er-top", Label).update(self._linha1())


class SyncScreen(Screen):
    BINDINGS = [
        Binding("escape,q", "fechar", "fechar"),
        Binding("up,k", "cursor_up", "cima", show=False),
        Binding("down,j", "cursor_down", "baixo", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="sync-header", classes="band")
        yield ListView(id="sync-list")
        yield Static(keybar([("Q", "Voltar")]), classes="keybar")

    def on_mount(self) -> None:
        self._rows: dict[tuple, SyncRow] = {}
        self._n_encontrados = 0
        self._n_analisando = 0
        self._n_prontos = 0
        self._terminado = False
        self._erro: str | None = None
        self._render_header()
        self.query_one("#sync-list", ListView).focus()
        self._sincronizar()

    def _render_header(self) -> None:
        estado = f"[{COR_LIXEIRA}]erro: {self._erro}[/]" if self._erro else (
            f"[{INK_DIM}]concluído[/]" if self._terminado else f"[{AZURE_BRT}]sincronizando…[/]"
        )
        self.query_one("#sync-header", Static).update(
            f"[b $accent]Sincronizar[/]   {estado}\n"
            f"[{INK_DIM}]{self._n_encontrados} encontrado(s)"
            f" · {self._n_analisando} analisando"
            f" · {self._n_prontos} pronto(s)[/]"
        )

    @work(thread=True)
    def _sincronizar(self) -> None:
        from apolo.sync import run_sync

        def on_event(kind, *args, **kwargs) -> None:
            self.app.call_from_thread(self._handle_event, kind, args, kwargs)

        try:
            run_sync(self.app.config, limit=self.app.config.sync_limit, on_event=on_event)
        except Exception as exc:
            self.app.call_from_thread(self._erro_fatal, str(exc))

    def _erro_fatal(self, msg: str) -> None:
        self._terminado = True
        self._erro = msg
        self._render_header()

    def _handle_event(self, kind: str, args: tuple, kwargs: dict) -> None:
        try:
            self._processar_evento(kind, args, kwargs)
        except Exception:
            pass  # tela pode já ter sido fechada — o sync continua gravando no banco
        self._render_header()

    def _processar_evento(self, kind: str, args: tuple, kwargs: dict) -> None:
        if kind == "item":
            item = args[0]
            self._n_encontrados += 1
            if not item.sera_analisado:
                self._n_prontos += 1
            badge = len(getattr(self.app, "_contas_ativas", set())) > 1
            row = SyncRow(item, mostrar_badge=badge)
            self._rows[(item.conta, item.pasta, item.uid)] = row
            self.query_one("#sync-list", ListView).append(row)
        elif kind == "analisando":
            item = args[0]
            self._n_analisando += 1
            self._atualizar_row(item)
        elif kind == "classificado":
            item = args[0]
            self._n_analisando -= 1
            self._n_prontos += 1
            self._atualizar_row(item)
        elif kind == "erro":
            self.app.notify(f"[{kwargs.get('conta')}] {kwargs.get('msg')}", severity="warning", title="sincronizar")
        elif kind == "fim":
            self._terminado = True
            self._refrescar_fila()

    def _atualizar_row(self, item) -> None:
        row = self._rows.get((item.conta, item.pasta, item.uid))
        if row is not None:
            row.refresh_text()

    def _refrescar_fila(self) -> None:
        from apolo.storage.db import Storage
        from apolo.ui.model import Item

        try:
            with Storage(self.app.config.db_path) as store:
                rows = store.fetch_queue()
                self.app.queue = [Item(r) for r in rows]
        except Exception:
            pass

    def action_fechar(self) -> None:
        self.dismiss()

    def action_cursor_up(self) -> None:
        self.query_one("#sync-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#sync-list", ListView).action_cursor_down()
