"""Fila de revisão: onde o dono despacha o resíduo.

Lista navegável, decide com d/m/b/a, a decisão tira o email da lista na hora
(vai pra uma pilha de histórico); `u` desfaz. `b`/`a` gravam a regra na hora
(loop de aprendizado). Enter aplica AGORA (despacha via IMAP/Gmail num
diálogo com thread). `s` sincroniza ao vivo (`apolo.sync.run_sync`) numa
thread, sem travar a tela; itens novos entram direto na lista.
"""

from __future__ import annotations

import asyncio

import flet as ft

from apolo.actions import DispatchItem
from apolo.gui.confirm import ConfirmModal
from apolo.gui.model import ACAO_COR, ACAO_ICONE, ACAO_ROTULO, Item, fmt_conta, fmt_data, fmt_remetente
from apolo.gui.theme import AMBAR, COR_LIXEIRA, COR_MANTER, FONTE_STATUS, INK, INK_DIM, LOURO, SOL, SOL_INK, SURFACE, TERRACOTA
from apolo.gui.widgets import ARROW_DOWN, ARROW_UP, ENTER, ESCAPE, TAB, flash, header, key, keybar, rodape, scaffold
from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER, parse_sender
from apolo.rules.writer import add_rule_entry, remove_rule_entry

_STATUS_ICONE = {**ACAO_ICONE, "analisando": "…"}
_STATUS_COR = {**ACAO_COR, "analisando": AMBAR}
_STATUS_ROTULO = {**ACAO_ROTULO, "analisando": "analisando"}


class QueueScreen:
    def __init__(self) -> None:
        self.idx: int | None = None
        self.hist: list[tuple] = []
        self._rows: list[ft.GestureDetector] = []
        self._exibidos: list[Item] = []
        self._sync_ativo = False
        self._sync_conta: str | None = None
        self._sync_encontrados = 0
        self._sync_analisando = 0
        self._sync_auto_lixeira = 0
        self._sync_favoritos = 0
        self._sync_boxes: dict[tuple, ft.GestureDetector] = {}
        self._filtro_idx = 0
        self._contas: list[str | None] = [None]
        self._header_ref: ft.Ref[ft.Text] = ft.Ref()
        self._msg_ref: ft.Ref[ft.Text] = ft.Ref()
        self._banner_ref: ft.Ref[ft.Text] = ft.Ref()
        self._banner_container = ft.Container(
            content=ft.Text(
                "", ref=self._banner_ref, size=12, weight=ft.FontWeight.BOLD,
                style=ft.TextStyle(letter_spacing=1.1, font_family=FONTE_STATUS),
            ),
            padding=ft.Padding(left=14, right=14, top=9, bottom=9),
            border_radius=6,
            visible=False,
        )
        self._list_col = ft.Column(
            spacing=2, scroll=ft.ScrollMode.AUTO, expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        # True só depois que a árvore construída em build() está de fato na
        # page: controla se _render_lista()/_render_header() podem chamar
        # .update() (senão dá erro em controle ainda não montado). Ações
        # próprias da tela (mover cursor, decidir, desfazer, sincronizar)
        # NUNCA chamam app.refresh_top(), pois isso chamaria build() de novo,
        # e build() chama _aplicar_filtro(), que reseta self.idx pra 0. Todo
        # update depois da montagem é direto nos controles já existentes.
        self._mounted = False
        # Chamado em vez de app.pop_screen() quando embutido dentro de outra
        # tela (o Hub embute a fila no próprio painel, não empilha mais uma
        # tela cheia); None usa o pop_screen padrão (uso avulso).
        self.on_close = None

    # ----- montagem -----
    def build(self) -> ft.Control:
        contas_ativas = getattr(self.app, "_contas_ativas", set())
        self._contas = [None] + sorted(contas_ativas)
        self._aplicar_filtro()
        self._mounted = True
        return scaffold(
            header("Revisar fila"),
            ft.Column(
                [self._banner_container, ft.Text("", ref=self._header_ref, size=12, color=INK_DIM), self._list_col],
                spacing=8, expand=True,
            ),
            rodape(
                flash(self._msg_ref),
                keybar(
                    [
                        ("D", "Lixeira"), ("M", "Manter"), ("B", "Bloquear"), ("A", "Permitir"),
                        ("V", "Ver corpo"), ("C", "Código"), ("S", "Sincronizar"), ("U", "Desfazer"),
                        ("Tab", "Conta"), ("Enter", "Aplicar"), ("Esc", "Voltar"),
                    ]
                ),
            ),
        )

    @property
    def _conta_atual(self) -> str | None:
        return self._contas[self._filtro_idx]

    def _mostrar_badge(self) -> bool:
        return self._conta_atual is None and len(self._contas) > 2

    def _aplicar_filtro(self) -> None:
        conta = self._conta_atual
        self._exibidos = list(self.app.queue) if conta is None else [it for it in self.app.queue if it.conta == conta]
        self.idx = 0 if self._exibidos else None
        self._render_lista()

    def _render_lista(self) -> None:
        badge = self._mostrar_badge()
        self._rows = []
        controls = []
        for i, it in enumerate(self._exibidos):
            row = self._row(i, it, badge)
            self._rows.append(row)
            self._sync_boxes[(it.conta, it.pasta, it.uid)] = row
            controls.append(row)
        self._list_col.controls = controls
        if self._mounted:
            self._list_col.update()
        self._render_header()

    def _row(self, i: int, it: Item, mostrar_badge: bool) -> ft.GestureDetector:
        selecionado = i == self.idx
        status = "analisando" if it.analisando else it.acao
        cor = "#FFFFFF" if selecionado else _STATUS_COR.get(status, SOL)
        cor_fraca = "#FFFFFF" if selecionado else INK_DIM
        icone = _STATUS_ICONE.get(status, "·")
        tag = _STATUS_ROTULO.get(status, status).upper()
        # no_wrap + ellipsis: sem isso, um assunto/remetente comprido quebra
        # em duas linhas e essa linha da lista fica mais alta que as outras
        # (cada linha tem que ter a MESMA altura, senão o cursor pula de
        # tamanho ao navegar).
        titulo = ft.Text(
            f"{icone} {tag:<9}  {fmt_remetente(it.remetente)}",
            size=13, color=cor, weight=ft.FontWeight.W_600,
            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS,
        )
        # Segunda linha em 3 zonas: dado (conta/favorito) à esquerda, prévia
        # do assunto centralizada, horário à direita, com larguras fixas nas
        # pontas pra sobrar o mesmo espaço dos dois lados do centro.
        dado = " ".join(filter(None, [f"[{fmt_conta(it.conta)}]" if mostrar_badge else "", "★" if it.favorito else ""]))
        esquerda = ft.Text(dado, size=11, color=cor_fraca, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, width=150)
        centro = ft.Text(
            it.assunto or "(sem assunto)", size=11, color=cor_fraca, text_align=ft.TextAlign.CENTER,
            no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True,
        )
        direita = ft.Text(
            fmt_data(it.data), size=11, color=cor_fraca, text_align=ft.TextAlign.RIGHT,
            no_wrap=True, width=110,
        )
        linha2 = ft.Row([esquerda, centro, direita], spacing=8)
        caixa = ft.Container(
            content=ft.Column([titulo, linha2], spacing=2),
            bgcolor=_STATUS_COR.get(status, SOL) if selecionado else SURFACE,
            padding=ft.Padding(left=10, right=10, top=6, bottom=6),
            border_radius=6,
        )
        # GestureDetector em vez de Container(ink=True, on_click=...): um
        # Container clicável entra na travessia nativa de foco por teclado
        # do Flutter, e a navegação por seta (tratada manualmente em on_key)
        # brigava com esse foco nativo, "arrastando" a seleção doutras áreas
        # da tela (ex.: o menu do Hub) junto. GestureDetector não disputa foco.
        return ft.GestureDetector(content=caixa, on_tap=lambda e, idx=i: self._selecionar(idx))

    def _selecionar(self, idx: int) -> None:
        self.idx = idx
        self._render_lista()

    def _render_header(self) -> None:
        n = len(self._exibidos)
        n_lix = sum(1 for h in self.hist if h[0].acao == ACAO_LIXEIRA)
        n_man = sum(1 for h in self.hist if h[0].acao == ACAO_MANTER)
        partes = [f"{n} restante(s)"]
        if self.hist:
            partes.append(f"{len(self.hist)} decidido(s) · U desfaz · Enter aplica")
        if len(self._contas) > 2:
            partes.append(f"conta: {self._conta_atual or 'todas as contas'}")
        partes.append(f"● {n_lix} lixeira   ✓ {n_man} manter")
        if self._header_ref.current:
            self._header_ref.current.value = "   ·   ".join(partes)
            if self._mounted:
                self._header_ref.current.update()

    def _msg(self, texto: str) -> None:
        if self._msg_ref.current:
            self._msg_ref.current.value = texto
            self._msg_ref.current.update()

    def _banner(self, texto: str, bg: str, fg: str = "#FFFFFF") -> None:
        """Status da sincronização (sincronizando/concluída/erro): único
        lugar pra isso, no topo, em vez de espalhado entre a linha de
        contadores e a mensagem flutuante do rodapé."""
        self._banner_container.bgcolor = bg
        self._banner_container.visible = True
        if self._banner_ref.current:
            self._banner_ref.current.value = texto.upper()
            self._banner_ref.current.color = fg
        if self._mounted:
            self._banner_container.update()

    @staticmethod
    def _chave_item(it: Item) -> tuple:
        return (it.conta, it.pasta, it.uidvalidity, it.uid)

    # ----- teclado -----
    def on_key(self, e: ft.KeyboardEvent) -> None:
        k = key(e)
        if k in ARROW_UP:
            self._mover(-1)
        elif k in ARROW_DOWN:
            self._mover(1)
        elif k == "d":
            self._decidir_pedindo_confirmacao(ACAO_LIXEIRA)
        elif k == "m":
            self._decidir_pedindo_confirmacao(ACAO_MANTER)
        elif k == "b":
            self._aprender_pedindo_confirmacao("blocklist")
        elif k == "a":
            self._aprender_pedindo_confirmacao("allowlist")
        elif k == "v":
            self._visualizar()
        elif k == "c":
            self._pegar_codigo()
        elif k == "u":
            self._desfazer()
        elif k == "s":
            self._sincronizar()
        elif k in TAB:
            self._alternar_conta()
        elif k in ENTER:
            self._aplicar()
        elif k in ESCAPE or k == "q":
            self._voltar()

    def _mover(self, delta: int) -> None:
        if not self._exibidos:
            return
        self.idx = (self.idx if self.idx is not None else 0)
        self.idx = (self.idx + delta) % len(self._exibidos)
        self._render_lista()

    # ----- decisões -----
    def _decidir(self, acao: str, rule_undo=None, favorito_confirmado: bool = False) -> None:
        if self.idx is None or self.idx >= len(self._exibidos):
            return
        it = self._exibidos[self.idx]
        self.hist.append((it, self.idx, it.acao, rule_undo, favorito_confirmado))
        it.acao = acao
        self.app.queue.remove(it)
        del self._exibidos[self.idx]
        if self.idx >= len(self._exibidos):
            self.idx = len(self._exibidos) - 1 if self._exibidos else None
        self._render_lista()

    def _decidir_pedindo_confirmacao(self, acao: str) -> None:
        if self.idx is None or self.idx >= len(self._exibidos):
            return
        it = self._exibidos[self.idx]

        async def _run() -> None:
            favorito_confirmado = False
            if acao == ACAO_LIXEIRA and it.favorito:
                if not await self._confirmar_exclusao(it):
                    return
                # Confirmar aqui vale como permissão explícita de sobrepor o
                # favorito no despacho (ver DispatchItem.favorito_confirmado
                # e apolo.actions) — senão a checagem de proteção na hora H
                # barra a exclusão de qualquer jeito e o email "volta".
                favorito_confirmado = True
            self._decidir(acao, favorito_confirmado=favorito_confirmado)
            self._msg(f"→ {ACAO_ROTULO[acao]}: {it.remetente}")

        self.app.page.run_task(_run)

    async def _confirmar_exclusao(self, it: Item) -> bool:
        rem = fmt_remetente(it.remetente)
        return await ConfirmModal(
            self.app, f"★ Este email de {rem} está favoritado.\nTem certeza que quer excluí-lo?",
            titulo="Email favoritado",
        ).ask()

    def _aprender_pedindo_confirmacao(self, lista: str) -> None:
        if self.idx is None or self.idx >= len(self._exibidos):
            return
        it = self._exibidos[self.idx]
        acao = ACAO_LIXEIRA if lista == "blocklist" else ACAO_MANTER

        async def _run() -> None:
            favorito_confirmado = False
            if acao == ACAO_LIXEIRA and it.favorito:
                if not await self._confirmar_exclusao(it):
                    return
                favorito_confirmado = True
            _, dominio = parse_sender(it.remetente)
            if not dominio:
                self._msg("sem domínio no remetente, regra não criada")
                return
            try:
                status = add_rule_entry(self.app.rules_path, lista=lista, tipo="dominio", valor=dominio)
            except Exception as e:
                self._msg(f"erro ao gravar regra: {e}")
                return
            rule_undo = (lista, "dominio", dominio) if status == "added" else None
            self._decidir(acao, rule_undo, favorito_confirmado=favorito_confirmado)
            verbo = "já existia" if status == "exists" else "criada"
            self._msg(f"{lista}: {dominio} {verbo} → {ACAO_ROTULO[acao]}")

        self.app.page.run_task(_run)

    def _desfazer(self) -> None:
        if not self.hist:
            self._msg("nada a desfazer")
            return
        it, idx, anterior, rule_undo, _favorito_confirmado = self.hist.pop()
        pre = ""
        if rule_undo:
            try:
                remove_rule_entry(self.app.rules_path, lista=rule_undo[0], tipo=rule_undo[1], valor=rule_undo[2])
            except Exception as e:
                pre = f"(regra não removida: {e}) "
        it.acao = anterior
        idx_fila = min(idx, len(self.app.queue))
        self.app.queue.insert(idx_fila, it)
        if self._conta_atual is None or it.conta == self._conta_atual:
            idx_exib = min(idx, len(self._exibidos))
            self._exibidos.insert(idx_exib, it)
            self.idx = idx_exib
        self._render_lista()
        self._msg(pre + f"↩ desfeito: {it.remetente}")

    def _aplicar(self) -> None:
        itens = [
            DispatchItem(
                pasta=it.pasta, uidvalidity=it.uidvalidity, uid=it.uid, message_id=it.message_id,
                acao=it.acao, conta=it.conta, provider_id=it.provider_id,
                favorito_confirmado=favorito_confirmado,
            )
            for it, _idx, _anterior, _rule_undo, favorito_confirmado in self.hist
            if it.acao in (ACAO_LIXEIRA, ACAO_MANTER)
        ]
        self.hist = []
        if not itens:
            self._fechar()
            return
        DispatchProgress(self.app, itens, self._apos_aplicar).show()

    def _apos_aplicar(self) -> None:
        # O próprio diálogo já mostrou o resultado antes de fechar (ver
        # DispatchProgress), então nada de notify() aqui: um toast separado
        # é não-modal, ficava "por cima" enquanto o dono já conseguia navegar
        # o menu por baixo, parecendo travado.
        self._fechar()

    def _fechar(self) -> None:
        (self.on_close or self.app.pop_screen)()

    def ao_perder_foco(self) -> None:
        """Chamado quando a tela sai de foco sem passar por `_voltar()` (ex.:
        o dono clicou noutro item do menu do Hub); desfaz decisões
        pendentes do mesmo jeito que `_voltar()` faria, senão elas somem da
        fila (removidas de `app.queue` em `_decidir`) sem nunca ter sido
        aplicadas nem devolvidas."""
        while self.hist:
            self._desfazer()

    def _pegar_codigo(self) -> None:
        if self.idx is None or self.idx >= len(self._exibidos):
            return
        if not self.app.config:
            self._msg("configuração não carregada")
            return
        it = self._exibidos[self.idx]
        # run_task exige uma coroutine function de verdade. Um
        # `lambda: obj.ask()` é uma função comum (só devolve a coroutine ao
        # ser chamada), não passa no `iscoroutinefunction` do Flet. Passar o
        # método assíncrono já ligado ao objeto (sem chamar) resolve.
        self.app.page.run_task(CodeModal(self.app, it).ask)

    def _visualizar(self) -> None:
        if self.idx is None or self.idx >= len(self._exibidos):
            return
        if not self.app.config:
            self._msg("configuração não carregada")
            return
        from apolo.gui.body_view import BodyViewModal

        it = self._exibidos[self.idx]
        self.app.page.run_task(BodyViewModal(self.app, it).show)

    # ----- sincronizar -----
    def _alternar_conta(self) -> None:
        if len(self._contas) <= 2:
            self._msg("só uma conta vinculada")
            return
        self._filtro_idx = (self._filtro_idx + 1) % len(self._contas)
        self.app.last_queue_key = None
        self._aplicar_filtro()
        self._msg(f"conta: {self._conta_atual or 'todas as contas'}")

    def _sincronizar(self) -> None:
        if self._sync_ativo:
            self._banner("sincronização já em andamento…", AMBAR, SOL_INK)
            return
        if not self.app.config:
            self._banner("configuração não carregada, sincronização não iniciada", TERRACOTA)
            return
        conta = self._conta_atual
        self._sync_ativo = True
        self._sync_conta = conta
        self._sync_encontrados = self._sync_analisando = self._sync_auto_lixeira = self._sync_favoritos = 0
        self._atualizar_banner_sync()
        self._render_header()
        self.app.page.run_thread(self._thread_sync, conta)

    def _atualizar_banner_sync(self) -> None:
        rotulo = self._sync_conta or "todas as contas"
        self._banner(f"⇄ sincronizando {rotulo}… {self._sync_encontrados} encontrado(s)", AMBAR, SOL_INK)

    def _thread_sync(self, conta: str | None) -> None:
        from apolo.sync import run_sync

        def on_event(kind, *args, **kwargs) -> None:
            try:
                self._processar_evento_sync(kind, args, kwargs)
            except Exception:
                pass

        try:
            run_sync(
                self.app.config, limit=self.app.config.sync_limit, on_event=on_event,
                skip_contas=set(getattr(self.app, "contas_invalidas", {})), only_conta=conta,
            )
        except Exception as exc:
            self._processar_evento_sync("erro_fatal", (str(exc),), {})

    def _processar_evento_sync(self, kind: str, args: tuple, kwargs: dict) -> None:
        if kind == "item":
            item = args[0]
            self._sync_encontrados += 1
            if item.sera_analisado:
                self._sync_analisando += 1
            novo = Item.from_sync(item, acao=item.status)
            novo.analisando = item.sera_analisado
            self._inserir_item_sync(novo)
            self._atualizar_banner_sync()
            self._render_header()
        elif kind == "analisando":
            item = args[0]
            self._marcar_analisando(item, True)
            self._render_lista()
        elif kind == "classificado":
            item = args[0]
            self._sync_analisando = max(0, self._sync_analisando - 1)
            self._marcar_analisando(item, False, novo_status=item.status)
            self._render_lista()
        elif kind == "auto_lixeira":
            self._sync_auto_lixeira += kwargs.get("quantidade", 0)
            self._render_header()
        elif kind == "favoritos":
            self._sync_favoritos += kwargs.get("quantidade", 0)
            self._render_header()
        elif kind == "erro":
            self.app.notify(f"[{kwargs.get('conta')}] {kwargs.get('msg')}", severity="warning")
        elif kind == "erro_fatal":
            self._sync_ativo = False
            self._banner(f"✗ erro na sincronização: {args[0]}", TERRACOTA)
            self._render_header()
        elif kind == "fim":
            self._sync_ativo = False
            extras = []
            if self._sync_auto_lixeira:
                extras.append(f"{self._sync_auto_lixeira} auto→lixeira")
            if self._sync_favoritos:
                extras.append(f"{self._sync_favoritos} ★ atualizado(s)")
            extra_txt = f" ({', '.join(extras)})" if extras else ""
            self._banner(f"✓ sincronização concluída, {self._sync_encontrados} novo(s){extra_txt}", LOURO)
            if self._sync_auto_lixeira:
                self.app.notify(f"{self._sync_auto_lixeira} email(s) movido(s) automaticamente pra lixeira.")
            self._render_header()

    def _marcar_analisando(self, sync_item, analisando: bool, novo_status: str | None = None) -> None:
        for it in self._exibidos:
            if (it.conta, it.pasta, it.uid) == (sync_item.conta, sync_item.pasta, sync_item.uid):
                it.analisando = analisando
                if novo_status:
                    it.acao = novo_status
                break

    def _inserir_item_sync(self, it: Item) -> None:
        from apolo.storage.db import _data_ordenavel

        chave = _data_ordenavel(it.data)

        def _pos(itens: list[Item]) -> int:
            return next((i for i, o in enumerate(itens) if _data_ordenavel(o.data) < chave), len(itens))

        self.app.queue.insert(_pos(self.app.queue), it)
        if self._conta_atual is not None and it.conta != self._conta_atual:
            return
        pos = _pos(self._exibidos)
        self._exibidos.insert(pos, it)
        if self.idx is not None and pos <= self.idx:
            self.idx += 1
        self._render_lista()

    def _voltar(self) -> None:
        self.ao_perder_foco()
        self._fechar()

    def on_show(self) -> None:
        pass


class DispatchProgress:
    """Aplica as decisões numa thread. Mostra "Aplicando…", troca pelo
    resultado no MESMO diálogo (sem virar um toast solto e não-modal por
    cima, que deixava dar pra navegar o menu por baixo enquanto ele ainda
    estava sumindo) e só então fecha e chama `on_done()`."""

    def __init__(self, app, itens: list[DispatchItem], on_done):
        self.app = app
        self._itens = itens
        self._on_done = on_done
        self._msg_ref: ft.Ref[ft.Text] = ft.Ref()
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Aplicando…", color=INK, weight=ft.FontWeight.BOLD),
            content=ft.Text("Movendo pra lixeira e marcando…", ref=self._msg_ref, color=INK_DIM),
        )

    def show(self) -> None:
        self.app.open_dialog(self.dialog, key_handler=lambda e: None)
        self.app.page.run_thread(self._executar)

    def _executar(self) -> None:
        import time

        from apolo.actions import apply_decisions

        try:
            res = apply_decisions(self.app.config, self._itens)
            partes = [f"{res.lixeira} lixeira", f"{res.mantidos} mantido(s)"]
            if res.falhas:
                partes.append(f"{res.falhas} falha(s)")
            if res.protegidos:
                partes.append(f"{res.protegidos} ★ protegido(s) (favoritado, não enviado)")
            msg = "✓ " + ", ".join(partes)
            cor = LOURO
        except Exception as exc:
            msg = f"✗ erro: {exc}"
            cor = TERRACOTA
        if self._msg_ref.current:
            self._msg_ref.current.value = msg
            self._msg_ref.current.color = cor
            self._msg_ref.current.update()
        time.sleep(1.1)  # deixa o resultado visível um instante antes de sumir
        # Fechar o diálogo (e só depois voltar pro Hub) precisa rodar na
        # thread do LOOP, não nesta worker thread: fechar um AlertDialog
        # depende de um round-trip assíncrono com o cliente (animação de
        # saída), e reconstruir a tela inteira (volta pro menu) em cima
        # disso, vindo de outra thread, deixava as duas mensagens chegarem
        # fora de ordem: o app já tinha voltado pro menu do Hub por baixo
        # (e as setas passavam a navegar ele) enquanto o "Aplicando…" ficava
        # preso na tela.
        self.app.page.run_task(self._fechar_e_concluir)

    async def _fechar_e_concluir(self) -> None:
        self.app.close_dialog()
        # Fechar o AlertDialog e trocar a árvore de telas inteira (volta pro
        # Hub) são duas rotas empilhadas no mesmo Navigator do Flutter; sem
        # esperar aqui, a segunda saía colada na primeira e o cliente ficava
        # com o "Aplicando…" preso na tela mesmo com o Python já de volta no
        # menu por baixo (confirmado: as duas threads do processo ficam
        # ociosas nesse estado, não é travamento do lado Python).
        await asyncio.sleep(0.35)
        self._on_done()


class CodeModal:
    """Pega o código/link de confirmação do email selecionado (`await CodeModal(app, item).ask()`)."""

    def __init__(self, app, item: Item):
        self.app = app
        self.item = item
        self._future: asyncio.Future | None = None
        self._cands: list = []
        self._shown = False

        self.msg_text = ft.Text("Buscando o email…", size=12, color=INK_DIM)
        self.list_col = ft.Column(spacing=2, scroll=ft.ScrollMode.AUTO, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text("Pegar código", color=INK, weight=ft.FontWeight.BOLD),
            content=ft.Container(content=ft.Column([self.msg_text, self.list_col], spacing=8), width=480, height=280),
            actions=[ft.TextButton("Fechar (Esc)", on_click=lambda e: self._resolve())],
        )

    def on_key(self, e: ft.KeyboardEvent) -> None:
        k = key(e)
        if k in ARROW_UP:
            self._mover(-1)
        elif k in ARROW_DOWN:
            self._mover(1)
        elif k in ENTER:
            self._copiar()
        elif k in ESCAPE or k == "q":
            self._resolve()

    def _mover(self, delta: int) -> None:
        if not self._cands:
            return
        self._idx = (getattr(self, "_idx", 0) + delta) % len(self._cands)
        self._render_cands()

    def _resolve(self) -> None:
        if self._future is not None and not self._future.done():
            self._future.set_result(None)
        self.app.close_dialog()

    async def ask(self) -> None:
        self._future = asyncio.get_running_loop().create_future()
        self.app.open_dialog(self.dialog, key_handler=self.on_key)
        self._shown = True
        self.app.page.run_thread(self._buscar)
        await self._future

    def _buscar(self) -> None:
        from apolo.actions import fetch_body
        from apolo.extract import extract_candidates

        try:
            texto = fetch_body(self.app.config, self.item)
            cands, err = extract_candidates(texto), None
        except Exception as exc:
            cands, err = [], f"{type(exc).__name__}: {exc}"
        self._mostrar(cands, err)

    def _mostrar(self, cands: list, err: str | None) -> None:
        if err:
            self.msg_text.value = f"erro: {err}"
            self.msg_text.color = COR_LIXEIRA
            if self._shown:
                self.msg_text.update()
            return
        if not cands:
            self.msg_text.value = "nenhum código ou link de confirmação encontrado."
            self.msg_text.color = AMBAR
            if self._shown:
                self.msg_text.update()
            return
        self._cands = cands
        self._idx = 0
        self.msg_text.value = "↑↓ escolher · Enter copia · Esc fecha"
        self._render_cands()

    def _render_cands(self) -> None:
        controls = []
        for i, c in enumerate(self._cands):
            selecionado = i == getattr(self, "_idx", 0)
            cor = COR_MANTER if c.kind == "código" else SOL
            icone = "◆" if c.kind == "código" else "↗"
            valor = c.value if len(c.value) <= 64 else c.value[:63] + "…"
            controls.append(
                ft.Container(
                    content=ft.Text(f"{icone} {c.kind:<7}  {valor}", color="#FFFFFF" if selecionado else cor, size=12),
                    bgcolor=cor if selecionado else None,
                    padding=ft.Padding(left=8, right=8, top=4, bottom=4),
                    border_radius=4,
                    on_click=lambda e, idx=i: self._clicar(idx),
                )
            )
        self.list_col.controls = controls
        if self._shown:
            self.msg_text.update()
            self.list_col.update()

    def _clicar(self, idx: int) -> None:
        self._idx = idx
        self._copiar()

    def _copiar(self) -> None:
        from apolo.extract import copy_to_clipboard

        idx = getattr(self, "_idx", None)
        if idx is None or idx >= len(self._cands):
            return
        c = self._cands[idx]
        if copy_to_clipboard(c.value):
            self.app.notify(f"Copiado: {c.value}")
        else:
            self.app.notify("Sem backend de clipboard disponível.", severity="warning")
        self._resolve()
