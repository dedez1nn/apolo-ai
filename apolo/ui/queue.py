"""Fila de revisão repaginada — onde o dono despacha o resíduo.

Mesma semântica do curses antigo, agora em Textual: lista navegável, decide com
d/m/b/a, a decisão tira o email da lista na hora (vai pra uma pilha de história);
`u` desfaz a última. `b`/`a` gravam a regra na hora (loop de aprendizado).

Enter aplica AGORA: despacha as decisões na hora (move pra lixeira via IMAP/
Gmail) num modal com worker, e mostra o resultado. Sair sem aplicar (q/Esc)
cancela as decisões da sessão (devolve à fila).
"""

from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Vertical
from textual.screen import ModalScreen, Screen
from textual.widgets import Label, ListItem, ListView, Static

from apolo.actions import DispatchItem
from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER, ACAO_REVISAR, parse_sender
from apolo.rules.writer import add_rule_entry, remove_rule_entry
from apolo.ui.model import ACAO_COR, ACAO_ICONE, ACAO_ROTULO, Item, fmt_data
from apolo.ui.theme import (
    AMBER,
    AZURE_BRT,
    COR_LIXEIRA,
    COR_MANTER,
    INK_DIM,
    INK_FAINT,
    keybar,
)


class EmailRow(ListItem):
    """Duas linhas: [tag colorida] remetente … data / assunto (dim)."""

    def __init__(self, item: Item, mostrar_badge: bool = True):
        super().__init__(classes=f"email-row v-{item.acao}")
        self.item = item
        self._mostrar_badge = mostrar_badge

    def compose(self) -> ComposeResult:
        yield Label(self._linha1(), classes="er-top", markup=True)
        yield Label(self._linha2(), classes="er-sub")

    def _linha1(self) -> str:
        it = self.item
        cor = ACAO_COR.get(it.acao, AZURE_BRT)
        icone = ACAO_ICONE.get(it.acao, "·")
        tag = ACAO_ROTULO.get(it.acao, it.acao).upper()
        rem = it.remetente or "(sem remetente)"
        badge = f"[{INK_FAINT}][{it.conta}][/] " if self._mostrar_badge else ""
        return f"[b {cor}]{icone} {tag:<8}[/]  {badge}{rem}"

    def _linha2(self) -> str:
        it = self.item
        data = fmt_data(it.data)
        assunto = it.assunto or "(sem assunto)"
        sufixo = f"   [{INK_FAINT}]·[/]  [{INK_FAINT}]{data}[/]" if data else ""
        return f"      [{INK_DIM}]{assunto}[/]{sufixo}"

    def refresh_text(self) -> None:
        self.query_one(".er-top", Label).update(self._linha1())


class QueueScreen(Screen):
    BINDINGS = [
        Binding("d", "decidir('lixeira')", "lixeira"),
        Binding("m", "decidir('manter')", "manter"),
        Binding("b", "aprender('blocklist')", "block"),
        Binding("a", "aprender('allowlist')", "allow"),
        Binding("u", "desfazer", "desfazer"),
        Binding("c", "pegar_codigo", "código"),
        Binding("tab", "alternar_conta", "conta", priority=True),
        Binding("enter", "aplicar", "aplicar", priority=True),
        Binding("escape,q", "voltar", "voltar"),
        Binding("up,k", "cursor_up", "cima", show=False),
        Binding("down,j", "cursor_down", "baixo", show=False),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="q-header", classes="band")
        yield ListView(id="q-list")
        yield Static("", id="q-msg", classes="flash")
        yield Static(
            keybar(
                [
                    ("D", "Lixeira", COR_LIXEIRA),
                    ("M", "Manter", COR_MANTER),
                    ("B", "Bloquear", COR_LIXEIRA),
                    ("A", "Permitir", COR_MANTER),
                    ("C", "Código"),
                    ("U", "Desfazer"),
                    ("⇥", "Conta"),
                    ("↵", "Aplicar"),
                    ("Q", "Voltar"),
                ]
            ),
            classes="keybar",
        )

    def on_mount(self) -> None:
        # Pilha de undo da sessão: (item, idx, acao_anterior, rule_undo).
        self.hist: list[tuple] = []
        # Alternador de conta: índice 0 = "todas"; os demais ciclam pelas
        # contas vinculadas (proton, gmail:<nome>…), na ordem que o app recebeu.
        contas_ativas = getattr(self.app, "_contas_ativas", set())
        self._contas: list[str | None] = [None] + sorted(contas_ativas)
        self._filtro_idx = 0
        self.query_one("#q-list", ListView).focus()
        self._aplicar_filtro()

    # ----- helpers -----
    @property
    def _list(self) -> ListView:
        return self.query_one("#q-list", ListView)

    @property
    def _conta_atual(self) -> str | None:
        return self._contas[self._filtro_idx]

    def _msg(self, texto: str = "") -> None:
        self.query_one("#q-msg", Static).update(texto)

    def _mostrar_badge(self) -> bool:
        # Badge de conta só ajuda na visão "todas" com mais de uma conta —
        # numa visão filtrada por conta, ela é redundante.
        return self._conta_atual is None and len(self._contas) > 2

    def _aplicar_filtro(self, manter_idx: int | None = None) -> None:
        """Reconstrói a lista exibida a partir do filtro de conta atual.

        `self._exibidos` é o subconjunto (mesmas referências de Item) de
        `self.app.queue` mostrado agora — as ações indexam nele, não no índice
        bruto da fila completa.
        """
        conta = self._conta_atual
        self._exibidos: list[Item] = (
            list(self.app.queue) if conta is None else [it for it in self.app.queue if it.conta == conta]
        )
        badge = self._mostrar_badge()
        lv = self._list
        lv.clear()
        for it in self._exibidos:
            lv.append(EmailRow(it, mostrar_badge=badge))
        if self._exibidos:
            lv.index = min(manter_idx, len(self._exibidos) - 1) if manter_idx is not None else 0
        self._render_header()

    def _render_header(self) -> None:
        n = len(self._exibidos)
        n_lix = sum(1 for h in self.hist if h[0].acao == ACAO_LIXEIRA)
        n_man = sum(1 for h in self.hist if h[0].acao == ACAO_MANTER)
        extra = (
            f"    [{INK_FAINT}]({len(self.hist)} decidido(s) · U desfaz · ↵ aplica)[/]"
            if self.hist
            else ""
        )
        rotulo_conta = self._conta_atual or "todas as contas"
        alterna = (
            f"    [{INK_FAINT}](⇥ conta: {rotulo_conta})[/]" if len(self._contas) > 2 else ""
        )
        self.query_one("#q-header", Static).update(
            f"[b $accent]Revisar fila[/]   [{INK_DIM}]{n} restantes[/]{alterna}\n"
            f"[{COR_LIXEIRA}]● {n_lix} lixeira[/]   [{COR_MANTER}]✓ {n_man} manter[/]{extra}"
        )

    def _idx(self) -> int | None:
        return self._list.index

    # ----- decisões -----
    async def decidir(self, acao: str, rule_undo=None) -> None:
        idx = self._idx()
        if idx is None or idx >= len(self._exibidos):
            return
        it = self._exibidos[idx]
        self.hist.append((it, idx, it.acao, rule_undo))
        it.acao = acao
        self.app.queue.remove(it)
        del self._exibidos[idx]
        # pop aguarda o DOM e reposiciona o cursor automaticamente.
        await self._list.pop(idx)
        self._render_header()

    async def action_decidir(self, acao: str) -> None:
        idx = self._idx()
        if idx is None or idx >= len(self._exibidos):
            return
        rem = self._exibidos[idx].remetente
        await self.decidir(acao)
        self._msg(f"[{ACAO_COR[acao]}]→ {ACAO_ROTULO[acao]}:[/] {rem}")

    async def action_aprender(self, lista: str) -> None:
        idx = self._idx()
        if idx is None or idx >= len(self._exibidos):
            return
        it = self._exibidos[idx]
        acao = ACAO_LIXEIRA if lista == "blocklist" else ACAO_MANTER
        _, dominio = parse_sender(it.remetente)
        if not dominio:
            self._msg(f"[{AMBER}]sem domínio no remetente — regra não criada[/]")
            return
        try:
            status = add_rule_entry(self.app.rules_path, lista=lista, tipo="dominio", valor=dominio)
        except Exception as e:  # não derruba a UI por erro de escrita
            self._msg(f"[{COR_LIXEIRA}]erro ao gravar regra: {e}[/]")
            return
        rule_undo = (lista, "dominio", dominio) if status == "added" else None
        await self.decidir(acao, rule_undo)
        verbo = "já existia" if status == "exists" else "criada"
        self._msg(f"[{ACAO_COR[acao]}]{lista}: {dominio} {verbo} → {ACAO_ROTULO[acao]}[/]")

    def action_desfazer(self) -> None:
        if not self.hist:
            self._msg(f"[{INK_FAINT}]nada a desfazer[/]")
            return
        it, idx, anterior, rule_undo = self.hist.pop()
        pre = ""
        if rule_undo:
            try:
                remove_rule_entry(self.app.rules_path, lista=rule_undo[0], tipo=rule_undo[1], valor=rule_undo[2])
            except Exception as e:
                pre = f"[{COR_LIXEIRA}](regra não removida: {e})[/] "
        it.acao = anterior
        idx_fila = min(idx, len(self.app.queue))
        self.app.queue.insert(idx_fila, it)
        # Só reaparece na visão atual se pertence ao filtro de conta ativo —
        # senão fica de volta na fila completa, fora da tela por enquanto.
        if self._conta_atual is None or it.conta == self._conta_atual:
            idx_exib = min(idx, len(self._exibidos))
            self._exibidos.insert(idx_exib, it)
            self._list.insert(idx_exib, [EmailRow(it, mostrar_badge=self._mostrar_badge())])
            self._list.index = idx_exib
        self._render_header()
        self._msg(pre + f"↩ desfeito: {it.remetente}")

    def action_aplicar(self) -> None:
        itens = [
            DispatchItem(
                pasta=it.pasta,
                uidvalidity=it.uidvalidity,
                uid=it.uid,
                message_id=it.message_id,
                acao=it.acao,
                conta=it.conta,
                provider_id=it.provider_id,
            )
            for it, *_ in self.hist
            if it.acao in (ACAO_LIXEIRA, ACAO_MANTER)
        ]
        self.hist = []
        if not itens:
            self.dismiss()
            return

        # Despacha AGORA (na thread, via modal) — o Enter aplica de fato.
        def _apos(msg: str | None) -> None:
            self.app.notify(f"Aplicado: {msg}" if msg else "Aplicado.", title="apolo")
            self.dismiss()

        self.app.push_screen(DispatchModal(itens), _apos)

    def action_pegar_codigo(self) -> None:
        idx = self._idx()
        if idx is None or idx >= len(self._exibidos):
            return
        if not self.app.config:
            self._msg(f"[{COR_LIXEIRA}]configuração não carregada[/]")
            return
        self.app.push_screen(CodeModal(self._exibidos[idx]))

    def action_alternar_conta(self) -> None:
        """Cicla a visão da fila entre "todas" e cada conta vinculada."""
        if len(self._contas) <= 2:
            self._msg(f"[{INK_FAINT}]só uma conta vinculada[/]")
            return
        self._filtro_idx = (self._filtro_idx + 1) % len(self._contas)
        self._aplicar_filtro()
        rotulo = self._conta_atual or "todas as contas"
        self._msg(f"[{AZURE_BRT}]conta:[/] {rotulo}")

    def action_voltar(self) -> None:
        # Sair sem aplicar = cancelar as decisões da sessão (devolve à fila).
        while self.hist:
            self.action_desfazer()
        self.dismiss()

    def action_cursor_up(self) -> None:
        self._list.action_cursor_up()

    def action_cursor_down(self) -> None:
        self._list.action_cursor_down()


class DispatchModal(ModalScreen):
    """Aplica as decisões numa thread e fecha devolvendo o resumo (string).

    Reusa o estilo do RunModal (#run-box). O despacho pode levar alguns segundos
    — ou até ~60s se o Bridge estiver subindo (o BridgeClient reenta a conexão).
    """

    def __init__(self, itens: list[DispatchItem]):
        super().__init__()
        self._itens = itens

    def compose(self) -> ComposeResult:
        with Vertical(id="run-box"):
            yield Static("[b]  Aplicando…[/]", classes="cfg-title")
            yield Static("  Movendo pra lixeira e marcando…", id="run-msg")
            yield Static("  [dim](pode levar alguns segundos)[/]")

    def on_mount(self) -> None:
        self._executar()

    @work(thread=True)
    def _executar(self) -> None:
        from apolo.actions import apply_decisions

        try:
            res = apply_decisions(self.app.config, self._itens)
            partes = [f"{res.lixeira} lixeira", f"{res.mantidos} mantido(s)"]
            if res.falhas:
                partes.append(f"{res.falhas} falha(s)")
            msg = ", ".join(partes)
        except Exception as exc:
            msg = f"erro: {exc}"
        self.app.call_from_thread(self.dismiss, msg)


class CodeModal(ModalScreen):
    """Pega o código/link de confirmação do email selecionado.

    Numa thread (o fetch toca a rede): puxa o corpo (Proton/Gmail), extrai
    candidatos (apolo.extract) e lista. Enter copia o escolhido pro clipboard
    (wl-copy/xclip/xsel) e fecha. Esc fecha sem copiar.
    """

    BINDINGS = [
        Binding("enter", "copiar", "copiar", priority=True),
        Binding("escape,q", "fechar", "fechar"),
        Binding("up,k", "cursor_up", "cima", show=False),
        Binding("down,j", "cursor_down", "baixo", show=False),
    ]

    def __init__(self, item: Item):
        super().__init__()
        self._item = item
        self._cands: list = []

    def compose(self) -> ComposeResult:
        with Vertical(id="code-box"):
            yield Static("[b]Pegar código[/]", classes="cfg-title")
            yield Static("Buscando o email…", id="code-msg")
            yield ListView(id="code-list")

    def on_mount(self) -> None:
        self.query_one("#code-list", ListView).display = False
        self._buscar()

    @work(thread=True)
    def _buscar(self) -> None:
        from apolo.actions import fetch_body
        from apolo.extract import extract_candidates

        try:
            texto = fetch_body(self.app.config, self._item)
            cands, err = extract_candidates(texto), None
        except Exception as exc:
            cands, err = [], f"{type(exc).__name__}: {exc}"
        self.app.call_from_thread(self._mostrar, cands, err)

    def _mostrar(self, cands: list, err: str | None) -> None:
        msg = self.query_one("#code-msg", Static)
        if err:
            msg.update(f"[{COR_LIXEIRA}]erro: {err}[/]")
            return
        if not cands:
            msg.update(f"[{AMBER}]nenhum código ou link de confirmação encontrado.[/]")
            return
        self._cands = cands
        msg.update(f"[{INK_FAINT}]↑↓ escolher · ↵ copiar · Esc fechar[/]")
        lv = self.query_one("#code-list", ListView)
        for c in cands:
            cor = COR_MANTER if c.kind == "código" else AZURE_BRT
            icone = "◆" if c.kind == "código" else "↗"
            valor = c.value if len(c.value) <= 64 else c.value[:63] + "…"
            lv.append(ListItem(Label(f"[{cor}]{icone} {c.kind:<7}[/]  {valor}", markup=True)))
        lv.display = True
        lv.index = 0
        lv.focus()

    def action_copiar(self) -> None:
        from apolo.extract import copy_to_clipboard

        lv = self.query_one("#code-list", ListView)
        idx = lv.index
        if idx is None or idx >= len(self._cands):
            return
        c = self._cands[idx]
        if copy_to_clipboard(c.value):
            self.app.notify(f"Copiado: {c.value}", title="apolo")
        else:
            self.app.notify(
                "Sem wl-copy/xclip/xsel pra copiar.", title="apolo", severity="warning"
            )
        self.dismiss()

    def action_fechar(self) -> None:
        self.dismiss()

    def action_cursor_up(self) -> None:
        self.query_one("#code-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#code-list", ListView).action_cursor_down()
