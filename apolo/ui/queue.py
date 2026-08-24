"""Fila de revisão repaginada — onde o dono despacha o resíduo.

Mesma semântica do curses antigo, agora em Textual: lista navegável, decide com
d/m/b/a, a decisão tira o email da lista na hora (vai pra uma pilha de história);
`u` desfaz a última. `b`/`a` gravam a regra na hora (loop de aprendizado).

Enter aplica AGORA: despacha as decisões na hora (move pra lixeira via IMAP/
Gmail) num modal com worker, e mostra o resultado. Sair sem aplicar (q/Esc)
cancela as decisões da sessão (devolve à fila).

`S` sincroniza: busca contas/pastas por completo (apolo.sync.run_sync) num
worker em thread — não abre tela nem trava a navegação; os emails novos entram
direto na lista, e os que dependem do Ollama aparecem como "analisando" até a
resposta chegar, tudo ao vivo enquanto o dono continua decidindo/copiando
código normalmente.
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
from apolo.ui.model import ACAO_COR, ACAO_ICONE, ACAO_ROTULO, Item, fmt_data, fmt_remetente
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

# Status "analisando" (sincronização ao vivo) somado às ações normais — usado
# só na exibição, nunca é gravado em `it.acao` (isso continua sendo lixeira/
# manter/revisar pra fins de despacho).
_STATUS_ICONE = {**ACAO_ICONE, "analisando": "…"}
_STATUS_COR = {**ACAO_COR, "analisando": AMBER}
_STATUS_ROTULO = {**ACAO_ROTULO, "analisando": "analisando"}


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
        status = "analisando" if it.analisando else it.acao
        # Selecionada: tudo branco (INK) — o destaque de fundo já marca a
        # linha, cor por ação só atrapalharia a leitura em cima dele.
        cor = INK if self.highlighted else _STATUS_COR.get(status, AZURE_BRT)
        fraca = INK if self.highlighted else INK_FAINT
        icone = _STATUS_ICONE.get(status, "·")
        tag = _STATUS_ROTULO.get(status, status).upper()
        rem = mesc(fmt_remetente(it.remetente))
        badge = f"[{fraca}]\\[{it.conta}][/] " if self._mostrar_badge else ""
        return f"[b {cor}]{icone} {tag:<8}[/]  {badge}[{INK}]{rem}[/]"

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


class QueueScreen(Screen):
    BINDINGS = [
        Binding("d", "decidir('lixeira')", "lixeira"),
        Binding("m", "decidir('manter')", "manter"),
        Binding("b", "aprender('blocklist')", "block"),
        Binding("a", "aprender('allowlist')", "allow"),
        Binding("v", "visualizar", "preview"),
        Binding("u", "desfazer", "desfazer"),
        Binding("c", "pegar_codigo", "código"),
        Binding("s", "sincronizar", "sincronizar"),
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
                    ("V", "Preview"),
                    ("C", "Código"),
                    ("S", "Sincronizar"),
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
        # Sincronização ao vivo (bind S) — roda num worker em thread; não
        # bloqueia a navegação nem as outras ações.
        self._sync_ativo = False
        self._sync_conta: str | None = None
        self._sync_encontrados = 0
        self._sync_analisando = 0
        self._sync_auto_lixeira = 0
        self._sync_rows: dict[tuple, EmailRow] = {}
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
            if manter_idx is not None:
                lv.index = min(manter_idx, len(self._exibidos) - 1)
            else:
                lv.index = self._idx_lembrado()
        self._render_header()

    def _idx_lembrado(self) -> int:
        """Onde estava o cursor da última vez que essa fila foi vista (por
        identidade do email, não pelo índice bruto — que muda conforme a fila
        encolhe/cresce entre uma visita e outra)."""
        chave = getattr(self.app, "last_queue_key", None)
        if chave is not None:
            for i, it in enumerate(self._exibidos):
                if self._chave_item(it) == chave:
                    return i
        return 0

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
        sync_alvo = f" {self._sync_conta}" if self._sync_conta else ""
        sync_txt = (
            f"    [{AMBER}](⇄ sincronizando{sync_alvo}… {self._sync_encontrados} encontrado(s)"
            f"{f', {self._sync_analisando} analisando' if self._sync_analisando else ''}"
            f"{f', {self._sync_auto_lixeira} auto→lixeira' if self._sync_auto_lixeira else ''})[/]"
            if self._sync_ativo
            else ""
        )
        self.query_one("#q-header", Static).update(
            f"[b $accent]Revisar fila[/]   [{INK_DIM}]{n} restantes[/]{alterna}{sync_txt}\n"
            f"[{COR_LIXEIRA}]● {n_lix} lixeira[/]   [{COR_MANTER}]✓ {n_man} manter[/]{extra}"
        )

    def _idx(self) -> int | None:
        return self._list.index

    @staticmethod
    def _chave_item(it: Item) -> tuple:
        return (it.conta, it.pasta, it.uidvalidity, it.uid)

    def on_list_view_highlighted(self, event: ListView.Highlighted) -> None:
        # Lembra qual email está selecionado (por identidade, não por índice
        # bruto) pra reentrar na mesma posição da próxima vez que a fila abrir.
        if event.list_view.id != "q-list":
            return
        if isinstance(event.item, EmailRow):
            self.app.last_queue_key = self._chave_item(event.item.item)

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
        rem = mesc(self._exibidos[idx].remetente)
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
            self._msg(f"[{COR_LIXEIRA}]erro ao gravar regra: {mesc(str(e))}[/]")
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
                pre = f"[{COR_LIXEIRA}](regra não removida: {mesc(str(e))})[/] "
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
        self._msg(pre + f"↩ desfeito: {mesc(it.remetente)}")

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

    def action_visualizar(self) -> None:
        idx = self._idx()
        if idx is None or idx >= len(self._exibidos):
            return
        if not self.app.config:
            self._msg(f"[{COR_LIXEIRA}]configuração não carregada[/]")
            return
        from apolo.ui.email_preview import EmailPreviewModal

        self.app.push_screen(EmailPreviewModal(self._exibidos[idx]))

    # ----- sincronizar (ao vivo, sem travar a tela) -----
    def action_sincronizar(self) -> None:
        if self._sync_ativo:
            self._msg(f"[{AMBER}]sincronização já em andamento…[/]")
            return
        if not self.app.config:
            self._msg(f"[{COR_LIXEIRA}]configuração não carregada[/]")
            return
        # Sincroniza só a conta do filtro atual (⇥); "todas" varre tudo.
        conta = self._conta_atual
        self._sync_ativo = True
        self._sync_conta = conta
        self._sync_encontrados = 0
        self._sync_analisando = 0
        self._sync_auto_lixeira = 0
        self._render_header()
        rotulo = conta or "todas as contas"
        self._msg(f"[{AMBER}]sincronizando {rotulo} em segundo plano…[/]")
        self._sincronizar(conta)

    @work(thread=True, exclusive=True, group="sync")
    def _sincronizar(self, conta: str | None) -> None:
        from apolo.sync import run_sync

        def on_event(kind, *args, **kwargs) -> None:
            self.app.call_from_thread(self._evento_sync, kind, args, kwargs)

        try:
            run_sync(
                self.app.config, limit=self.app.config.sync_limit, on_event=on_event,
                skip_contas=set(getattr(self.app, "contas_invalidas", {})),
                only_conta=conta,
            )
        except Exception as exc:
            self.app.call_from_thread(self._evento_sync, "erro_fatal", (str(exc),), {})

    def _evento_sync(self, kind: str, args: tuple, kwargs: dict) -> None:
        try:
            self._processar_evento_sync(kind, args, kwargs)
        except Exception:
            pass  # tela pode já ter sido fechada — o sync continua gravando no banco

    def _processar_evento_sync(self, kind: str, args: tuple, kwargs: dict) -> None:
        if kind == "item":
            item = args[0]
            self._sync_encontrados += 1
            if item.sera_analisado:
                self._sync_analisando += 1
            novo = Item.from_sync(item, acao=item.status)
            novo.analisando = item.sera_analisado
            self._inserir_item_sync(novo)
            self._render_header()
        elif kind == "analisando":
            item = args[0]
            row = self._sync_rows.get((item.conta, item.pasta, item.uid))
            if row is not None:
                row.item.analisando = True
                row.refresh_text()
        elif kind == "classificado":
            item = args[0]
            self._sync_analisando = max(0, self._sync_analisando - 1)
            row = self._sync_rows.get((item.conta, item.pasta, item.uid))
            if row is not None:
                row.item.analisando = False
                row.item.acao = item.status
                row.set_classes(f"email-row v-{item.status}")
                row.refresh_text()
            self._render_header()
        elif kind == "auto_lixeira":
            self._sync_auto_lixeira += kwargs.get("quantidade", 0)
            self._render_header()
        elif kind == "erro":
            self.app.notify(mesc(f"[{kwargs.get('conta')}] {kwargs.get('msg')}"), severity="warning", title="sincronizar")
        elif kind == "erro_fatal":
            self._sync_ativo = False
            self._msg(f"[{COR_LIXEIRA}]sincronização: {mesc(args[0])}[/]")
            self._render_header()
        elif kind == "fim":
            self._sync_ativo = False
            auto_txt = f", {self._sync_auto_lixeira} auto→lixeira" if self._sync_auto_lixeira else ""
            self._msg(f"[{AMBER}]sincronização concluída — {self._sync_encontrados} novo(s){auto_txt}[/]")
            if self._sync_auto_lixeira:
                self.app.notify(
                    f"{self._sync_auto_lixeira} email(s) movido(s) automaticamente pra lixeira.",
                    title="apolo",
                )
            self._render_header()

    def _inserir_item_sync(self, it: Item) -> None:
        # Mantém a ordem da fila (fetch_queue: mais recentes primeiro pela data
        # real do header) — o item entra na posição da data dele, não no fim.
        from apolo.storage.db import _data_ordenavel

        chave = _data_ordenavel(it.data)

        def _pos(itens: list[Item]) -> int:
            return next(
                (i for i, o in enumerate(itens) if _data_ordenavel(o.data) < chave),
                len(itens),
            )

        self.app.queue.insert(_pos(self.app.queue), it)
        if self._conta_atual is not None and it.conta != self._conta_atual:
            return
        pos = _pos(self._exibidos)
        self._exibidos.insert(pos, it)
        row = EmailRow(it, mostrar_badge=self._mostrar_badge())
        self._sync_rows[(it.conta, it.pasta, it.uid)] = row
        idx = self._list.index
        self._list.insert(pos, [row])
        # Entrou acima do cursor: compensa pra seleção não pular de email.
        if idx is not None and pos <= idx:
            self._list.index = idx + 1

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
            msg = f"erro: {mesc(str(exc))}"
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
            msg.update(f"[{COR_LIXEIRA}]erro: {mesc(err)}[/]")
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
            lv.append(ListItem(Label(f"[{cor}]{icone} {c.kind:<7}[/]  {mesc(valor)}", markup=True)))
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
            self.app.notify(f"Copiado: {mesc(c.value)}", title="apolo")
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
