"""Fila de revisão repaginada — onde o dono despacha o resíduo.

Mesma semântica do curses antigo, agora em Textual: lista navegável, decide com
d/m/b/a, a decisão tira o email da lista na hora (vai pra uma pilha de história);
`u` desfaz a última. `b`/`a` gravam a regra na hora (loop de aprendizado).

Enter aplica: as decisões viram `dispatch_items` no app (o cli despacha via IMAP
ao fechar). Sair sem aplicar cancela as decisões da sessão (devolve à fila).
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.screen import Screen
from textual.widgets import Footer, Label, ListItem, ListView, Static

from apolo.actions import DispatchItem
from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER, ACAO_REVISAR, parse_sender
from apolo.rules.writer import add_rule_entry, remove_rule_entry
from apolo.ui.model import ACAO_COR, ACAO_ICONE, ACAO_ROTULO, Item, fmt_data


class EmailRow(ListItem):
    """Duas linhas: [tag colorida] remetente … data / assunto (dim)."""

    def __init__(self, item: Item):
        super().__init__(classes="email-row")
        self.item = item

    def compose(self) -> ComposeResult:
        yield Label(self._linha1(), classes="er-top", markup=True)
        yield Label(self._linha2(), classes="er-sub")

    def _linha1(self) -> str:
        it = self.item
        cor = ACAO_COR.get(it.acao, "white")
        icone = ACAO_ICONE.get(it.acao, "")
        tag = ACAO_ROTULO.get(it.acao, it.acao).upper()
        rem = it.remetente or "(sem remetente)"
        return f"[b {cor}]{icone} {tag:<8}[/]  {rem}"

    def _linha2(self) -> str:
        it = self.item
        data = fmt_data(it.data)
        assunto = it.assunto or "(sem assunto)"
        sufixo = f"   ·  {data}" if data else ""
        return f"      {assunto}{sufixo}"

    def refresh_text(self) -> None:
        self.query_one(".er-top", Label).update(self._linha1())


class QueueScreen(Screen):
    BINDINGS = [
        Binding("d", "decidir('lixeira')", "lixeira"),
        Binding("m", "decidir('manter')", "manter"),
        Binding("b", "aprender('blocklist')", "block"),
        Binding("a", "aprender('allowlist')", "allow"),
        Binding("u", "desfazer", "desfazer"),
        Binding("enter", "aplicar", "aplicar", priority=True),
        Binding("escape,q", "voltar", "voltar"),
        Binding("up,k", "cursor_up", "cima", show=False),
        Binding("down,j", "cursor_down", "baixo", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="q-header")
        yield ListView(*[EmailRow(it) for it in self.app.queue], id="q-list")
        yield Static("", id="q-msg")
        yield Footer()

    def on_mount(self) -> None:
        # Pilha de undo da sessão: (item, idx, acao_anterior, rule_undo).
        self.hist: list[tuple] = []
        self.query_one("#q-list", ListView).focus()
        self._render_header()

    # ----- helpers -----
    @property
    def _list(self) -> ListView:
        return self.query_one("#q-list", ListView)

    def _msg(self, texto: str = "") -> None:
        self.query_one("#q-msg", Static).update(texto)

    def _render_header(self) -> None:
        n = len(self.app.queue)
        n_lix = sum(1 for h in self.hist if h[0].acao == ACAO_LIXEIRA)
        n_man = sum(1 for h in self.hist if h[0].acao == ACAO_MANTER)
        extra = f"    [dim]({len(self.hist)} ação(ões) · u desfaz · ↵ aplica)[/]" if self.hist else ""
        self.query_one("#q-header", Static).update(
            f"[b]  Revisar fila[/]   [dim]{n} restantes[/]\n"
            f"  [tomato] {n_lix} lixeira[/]   [springgreen] {n_man} manter[/]{extra}"
        )

    def _idx(self) -> int | None:
        return self._list.index

    # ----- decisões -----
    async def decidir(self, acao: str, rule_undo=None) -> None:
        idx = self._idx()
        if idx is None or idx >= len(self.app.queue):
            return
        it = self.app.queue[idx]
        self.hist.append((it, idx, it.acao, rule_undo))
        it.acao = acao
        del self.app.queue[idx]
        # pop aguarda o DOM e reposiciona o cursor automaticamente.
        await self._list.pop(idx)
        self._render_header()

    async def action_decidir(self, acao: str) -> None:
        idx = self._idx()
        if idx is None or idx >= len(self.app.queue):
            return
        rem = self.app.queue[idx].remetente
        await self.decidir(acao)
        self._msg(f"[{ACAO_COR[acao]}]→ {ACAO_ROTULO[acao]}:[/] {rem}")

    async def action_aprender(self, lista: str) -> None:
        idx = self._idx()
        if idx is None or idx >= len(self.app.queue):
            return
        it = self.app.queue[idx]
        acao = ACAO_LIXEIRA if lista == "blocklist" else ACAO_MANTER
        _, dominio = parse_sender(it.remetente)
        if not dominio:
            self._msg("[gold]sem domínio no remetente — regra não criada[/]")
            return
        try:
            status = add_rule_entry(self.app.rules_path, lista=lista, tipo="dominio", valor=dominio)
        except Exception as e:  # não derruba a UI por erro de escrita
            self._msg(f"[tomato]erro ao gravar regra: {e}[/]")
            return
        rule_undo = (lista, "dominio", dominio) if status == "added" else None
        await self.decidir(acao, rule_undo)
        verbo = "já existia" if status == "exists" else "criada"
        self._msg(f"[{ACAO_COR[acao]}]{lista}: {dominio} {verbo} → {ACAO_ROTULO[acao]}[/]")

    def action_desfazer(self) -> None:
        if not self.hist:
            self._msg("[dim]nada a desfazer[/]")
            return
        it, idx, anterior, rule_undo = self.hist.pop()
        pre = ""
        if rule_undo:
            try:
                remove_rule_entry(self.app.rules_path, lista=rule_undo[0], tipo=rule_undo[1], valor=rule_undo[2])
            except Exception as e:
                pre = f"[tomato](regra não removida: {e})[/] "
        it.acao = anterior
        idx = min(idx, len(self.app.queue))
        self.app.queue.insert(idx, it)
        self._list.insert(idx, [EmailRow(it)])
        self._list.index = idx
        self._render_header()
        self._msg(pre + f"↩ desfeito: {it.remetente}")

    def action_aplicar(self) -> None:
        for it, *_ in self.hist:
            if it.acao in (ACAO_LIXEIRA, ACAO_MANTER):
                self.app.dispatch_items.append(
                    DispatchItem(
                        pasta=it.pasta,
                        uidvalidity=it.uidvalidity,
                        uid=it.uid,
                        message_id=it.message_id,
                        acao=it.acao,
                    )
                )
        self.hist = []
        self.dismiss()

    def action_voltar(self) -> None:
        # Sair sem aplicar = cancelar as decisões da sessão (devolve à fila).
        while self.hist:
            self.action_desfazer()
        self.dismiss()

    def action_cursor_up(self) -> None:
        self._list.action_cursor_up()

    def action_cursor_down(self) -> None:
        self._list.action_cursor_down()
