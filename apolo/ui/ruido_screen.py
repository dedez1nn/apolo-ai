"""Emails de ruído — o que o auto-envio já mandou pra lixeira sozinho.

Lista os despachados como lixeira pela cascata determinística (blocklist/
keyword/list-unsubscribe — ver `apolo.sync`/`apolo.cli`), com o prazo
estimado até a lixeira apagar de vez (30 dias fixos, política do Gmail;
adotada como estimativa também pras contas IMAP). `r` restaura antes de
expirar — volta pra fila normal de revisão; `v` mostra o preview.

Sem "aplicar em lote": restaurar é uma correção pontual, já executa na hora.
"""

from __future__ import annotations

from datetime import datetime, timedelta, timezone

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Label, ListItem, ListView, Static

from apolo.ui.model import Item, fmt_data, fmt_remetente
from apolo.ui.theme import (
    AMBER,
    AZURE_BRT,
    COR_LIXEIRA,
    COR_MANTER,
    INK,
    INK_DIM,
    INK_FAINT,
    keybar,
    mesc,
)

DIAS_EXPIRACAO = 30


def dias_restantes(processado_em: str | None) -> int | None:
    """`processado_em` (ISO, gravado por `mark_dispatched`) + 30 dias - agora,
    em dias inteiros. None se não der pra calcular (sem timestamp)."""
    if not processado_em:
        return None
    try:
        dt = datetime.fromisoformat(processado_em)
    except ValueError:
        return None
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    expira = dt + timedelta(days=DIAS_EXPIRACAO)
    return (expira - datetime.now(timezone.utc)).days


def _cor_prazo(dias: int) -> str:
    if dias < 3:
        return COR_LIXEIRA
    if dias < 10:
        return AMBER
    return INK_DIM


class RuidoRow(ListItem):
    """Duas linhas: prazo + remetente / assunto (dim) — mesmo formato do EmailRow da fila."""

    def __init__(self, item: Item, dias: int):
        super().__init__(classes="email-row v-lixeira")
        self.item = item
        self.dias = dias

    def compose(self) -> ComposeResult:
        yield Label(self._linha1(), classes="er-top", markup=True)
        yield Label(self._linha2(), classes="er-sub")

    def _linha1(self) -> str:
        it = self.item
        cor = INK if self.highlighted else _cor_prazo(self.dias)
        rem = mesc(fmt_remetente(it.remetente))
        prazo = f"expira em {self.dias}d" if self.dias >= 0 else "expirado"
        return f"[b {cor}]● {prazo:<14}[/]  [{INK}]{rem}[/]"

    def _linha2(self) -> str:
        it = self.item
        data = fmt_data(it.data)
        assunto = mesc(it.assunto or "(sem assunto)")
        fraca = INK if self.highlighted else INK_FAINT
        cor_assunto = INK if self.highlighted else INK_DIM
        sufixo = f"   [{fraca}]·[/]  [{fraca}]{data}[/]" if data else ""
        return f"      [{cor_assunto}]{assunto}[/]{sufixo}"

    def watch_highlighted(self, value: bool) -> None:
        super().watch_highlighted(value)
        if self.is_mounted:
            self.refresh_text()

    def refresh_text(self) -> None:
        self.query_one(".er-top", Label).update(self._linha1())
        self.query_one(".er-sub", Label).update(self._linha2())


class RuidoScreen(Screen):
    BINDINGS = [
        Binding("r", "restaurar", "restaurar"),
        Binding("v", "visualizar", "preview"),
        Binding("escape,q", "voltar", "voltar"),
        Binding("up,k", "cursor_up", "cima", show=False),
        Binding("down,j", "cursor_down", "baixo", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="q-header", classes="band")
        yield ListView(id="q-list")
        yield Static("", id="q-msg", classes="flash")
        yield Static(
            keybar([("R", "Restaurar"), ("V", "Preview"), ("Q", "Voltar")]),
            classes="keybar",
        )

    def on_mount(self) -> None:
        self._carregar()
        self.query_one("#q-list", ListView).focus()

    @property
    def _list(self) -> ListView:
        return self.query_one("#q-list", ListView)

    def _msg(self, texto: str = "") -> None:
        self.query_one("#q-msg", Static).update(texto)

    def _idx(self) -> int | None:
        return self._list.index

    def _carregar(self) -> None:
        from apolo.storage.db import Storage

        self._itens: list[Item] = []
        if self.app.config:
            try:
                with Storage(self.app.config.db_path) as store:
                    self._itens = [Item(r) for r in store.trashed_rows()]
            except Exception:
                self._itens = []
        # Já deve ter sido apagado de vez pelo provedor — sem ação possível,
        # só polui a lista (filtra no Python, não na consulta ao banco).
        self._itens = [it for it in self._itens if (dias_restantes(it.processado_em) or 0) >= 0]
        self._repovoar()

    def _repovoar(self, manter_idx: int | None = None) -> None:
        lv = self._list
        lv.clear()
        for it in self._itens:
            lv.append(RuidoRow(it, dias_restantes(it.processado_em) or 0))
        if self._itens:
            lv.index = min(manter_idx, len(self._itens) - 1) if manter_idx is not None else 0
        self._render_header()

    def _render_header(self) -> None:
        n = len(self._itens)
        sub = f"{n} auto-enviado(s) pra lixeira" if n else "nada aqui — nenhum auto-envio pendente de expirar"
        self.query_one("#q-header", Static).update(f"[b {AZURE_BRT}]Emails de ruído[/]    [{INK_FAINT}]{sub}[/]")

    # ----- ações -----
    def action_restaurar(self) -> None:
        idx = self._idx()
        if idx is None or idx >= len(self._itens):
            return
        if not self.app.config:
            self._msg(f"[{COR_LIXEIRA}]configuração não carregada[/]")
            return
        item = self._itens[idx]
        self._msg(f"[{AMBER}]restaurando {mesc(fmt_remetente(item.remetente))}…[/]")
        self._restaurar(item, idx)

    @work(thread=True)
    def _restaurar(self, item: Item, idx: int) -> None:
        from apolo.actions import restaurar_email

        erro = None
        try:
            restaurar_email(self.app.config, item)
        except Exception as exc:
            erro = str(exc)
        self.app.call_from_thread(self._apos_restaurar, item, idx, erro)

    def _apos_restaurar(self, item: Item, idx: int, erro: str | None) -> None:
        if erro:
            self._msg(f"[{COR_LIXEIRA}]erro ao restaurar: {mesc(erro)}[/]")
            return
        if idx < len(self._itens) and self._itens[idx] is item:
            del self._itens[idx]
        self._repovoar(manter_idx=idx)
        self._msg(
            f"[{COR_MANTER}]✓ restaurado:[/] {mesc(fmt_remetente(item.remetente))} "
            f"[{INK_FAINT}](volta pra fila normal de revisão)[/]"
        )

    def action_visualizar(self) -> None:
        idx = self._idx()
        if idx is None or idx >= len(self._itens):
            return
        if not self.app.config:
            self._msg(f"[{COR_LIXEIRA}]configuração não carregada[/]")
            return
        from apolo.ui.email_preview import EmailPreviewModal

        self.app.push_screen(EmailPreviewModal(self._itens[idx]))

    def action_voltar(self) -> None:
        self.dismiss()

    def action_cursor_up(self) -> None:
        self._list.action_cursor_up()

    def action_cursor_down(self) -> None:
        self._list.action_cursor_down()
