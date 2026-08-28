"""Hub: a tela inicial que abre no clique da bandeja/Waybar.

Layout mestre-detalhe: menu estreito à esquerda (↑↓ + Enter, clique abre
direto); à direita, ou uma prévia (cursor movendo sem abrir) ou a tela
escolhida DE VERDADE, embutida no próprio painel, nunca navega pra uma tela
cheia à parte. Só um "foco": None (setas navegam o menu) ou a chave da opção
aberta (setas/teclas vão pro painel embutido; Esc no painel volta pro menu).
"""

from __future__ import annotations

import flet as ft

from apolo.gui.model import ACAO_COR, fmt_remetente
from apolo.gui.theme import BORDER, COR_LIXEIRA, COR_MANTER, GUTTER, INK, INK_DIM, INK_FAINT, SOL, SURFACE, SURFACE_2, TERRACOTA
from apolo.gui.widgets import ARROW_DOWN, ARROW_UP, ENTER, ESCAPE, key, keybar

_MENU = [
    ("review", "Revisar fila"),
    ("rules", "Regras configuradas"),
    ("gmail", "Configurar Gmail"),
    ("config", "Configurações"),
    ("status", "Status & contadores"),
]

_NAV_WIDTH = 240


class HubScreen:
    def __init__(self) -> None:
        self.idx = 0
        # None = modo menu (setas navegam a lista à esquerda). Uma chave de
        # _MENU = essa opção está embutida à direita e recebe todo o teclado.
        self.foco: str | None = None
        self._painel_ativo = None
        self._boxes: list[ft.Container] = []
        self._detail_container: ft.Container | None = None

    # ----- montagem -----
    def build(self) -> ft.Control:
        n_fila = len(self.app.queue)
        n_regras = self.app.stats.rules_count
        n_invalidas = len(getattr(self.app, "contas_invalidas", {}))
        badges = {
            "review": str(n_fila) if n_fila else "",
            "rules": str(n_regras) if n_regras else "",
            # Terracota (ver badge_cor abaixo) em vez do dourado padrão: não é
            # uma contagem neutra, é "precisa de atenção" (token expirado/
            # revogado); sem isso o único aviso era um toast que passava
            # rápido demais na abertura do app pra dar tempo de notar.
            "gmail": str(n_invalidas) if n_invalidas else "",
        }
        badge_cores = {"gmail": TERRACOTA}

        self._boxes = []
        rows = [
            self._row(i, chave, rotulo, badges.get(chave, ""), badge_cores.get(chave, SOL))
            for i, (chave, rotulo) in enumerate(_MENU)
        ]

        masthead = ft.Container(
            content=ft.Row(
                [
                    ft.Text("☉ APOLO", size=16, weight=ft.FontWeight.W_600, color=SOL),
                    ft.Text("·", color=INK_FAINT),
                    ft.Text("triador de emails", size=13, color=INK_DIM),
                    ft.Container(expand=True),
                    ft.Text(self._stats_texto(), size=12, color=INK_DIM),
                ],
                spacing=8,
            ),
            bgcolor=SURFACE_2,
            padding=ft.Padding(left=20, right=20, top=14, bottom=14),
            border=ft.Border(bottom=ft.BorderSide(width=1, color=BORDER)),
        )
        nav = ft.Container(
            content=ft.Column(rows, spacing=6),
            width=_NAV_WIDTH,
            padding=ft.Padding(left=12, right=12, top=16, bottom=16),
            bgcolor=SURFACE_2,
        )
        divisor = ft.Container(width=1, bgcolor=BORDER)

        if self.foco is not None and self._painel_ativo is not None:
            # Painel embutido: ele já traz seu próprio cabeçalho/lista/rodapé
            # de atalhos (ver *Screen.build() de cada um), então o Hub não
            # desenha o próprio rodapé "Navegar/Abrir/Sair" enquanto isso acontece.
            self._detail_container = ft.Container(content=self._painel_ativo.build(), expand=True)
            corpo = ft.Row([nav, divisor, self._detail_container], spacing=0, expand=True)
            return ft.Column([masthead, corpo], spacing=0, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

        self._detail_container = ft.Container(
            content=self._detalhe(_MENU[self.idx][0]),
            expand=True,
            padding=ft.Padding(left=24, right=24, top=20, bottom=20),
        )
        corpo = ft.Row([nav, divisor, self._detail_container], spacing=0, expand=True)
        foot = keybar([("↑↓", "Navegar"), ("Enter", "Abrir"), ("Q", "Sair")])
        return ft.Column([masthead, corpo, foot], spacing=0, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)

    def _stats_texto(self) -> str:
        from apolo.gui.model import fmt_run

        n = len(self.app.queue)
        fila = f"{n} na fila" if n else "fila vazia"
        return f"{fila}  ·  última {fmt_run(self.app.stats.last_run)}"

    def _row(self, i: int, chave: str, rotulo: str, badge: str, badge_cor: str = SOL) -> ft.GestureDetector:
        selecionado = i == self.idx
        caixa = ft.Container(
            content=ft.Row(
                [
                    ft.Text(
                        rotulo, size=13, color="#FFFFFF" if selecionado else INK,
                        weight=ft.FontWeight.W_500, expand=True,
                    ),
                    ft.Container(
                        content=ft.Text(badge, size=10, weight=ft.FontWeight.BOLD, color="#221803" if badge_cor == SOL else "#FFFFFF"),
                        bgcolor=badge_cor, padding=ft.Padding(left=7, right=7, top=1, bottom=1), border_radius=100,
                        visible=bool(badge),
                    ),
                ],
                spacing=6,
            ),
            bgcolor=SOL if selecionado else SURFACE,
            padding=ft.Padding(left=12, right=10, top=10, bottom=10),
            border_radius=8,
            data=chave,
        )
        self._boxes.append(caixa)
        # GestureDetector em vez de Container(ink=True, on_click=...): um
        # Container clicável entra na travessia nativa de foco por teclado do
        # Flutter (setas incluídas), então navegar a fila embutida ao lado
        # "arrastava" a seleção deste menu junto, mesmo com o teclado sendo
        # tratado manualmente (ver on_key). GestureDetector não disputa foco.
        return ft.GestureDetector(content=caixa, on_tap=lambda e, k=chave: self._abrir(k))

    # ----- painel de detalhe (direita) -----
    def _titulo(self, texto: str) -> ft.Text:
        return ft.Text(texto, size=15, weight=ft.FontWeight.W_600, color=INK)

    def _detalhe(self, chave: str) -> ft.Control:
        metodo = getattr(self, f"_detalhe_{chave}", None)
        return metodo() if metodo else ft.Container()

    def _linhas_emails(self, itens: list, limite: int = 10) -> list[ft.Control]:
        linhas = []
        for it in itens[:limite]:
            cor = ACAO_COR.get(it.acao, SOL)
            linhas.append(
                ft.Row(
                    [
                        ft.Text(GUTTER, color=cor),
                        ft.Column(
                            [
                                ft.Text(fmt_remetente(it.remetente), size=12, color=INK, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                                ft.Text(it.assunto or "(sem assunto)", size=11, color=INK_FAINT, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS),
                            ],
                            spacing=0, expand=True,
                        ),
                    ],
                    spacing=8,
                )
            )
        if len(itens) > limite:
            linhas.append(ft.Text(f"… e mais {len(itens) - limite}", size=11, color=INK_FAINT))
        return linhas

    def _detalhe_review(self) -> ft.Control:
        fila = self.app.queue
        titulo = self._titulo("Revisar fila")
        if not fila:
            return ft.Column(
                [titulo, ft.Text("Fila vazia, nada para revisar agora.", size=12, color=INK_DIM)],
                spacing=10,
            )
        rodape = ft.Text(f"{len(fila)} email(s) aguardando · Enter para revisar", size=11, color=INK_DIM)
        return ft.Column([titulo, *self._linhas_emails(fila), rodape], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)

    def _detalhe_rules(self) -> ft.Control:
        from apolo.rules.writer import list_entries

        titulo = self._titulo("Regras configuradas")
        try:
            entries = list_entries(self.app.rules_path)
        except Exception as e:
            return ft.Column([titulo, ft.Text(f"Erro ao ler regras: {e}", size=12, color=COR_LIXEIRA)], spacing=10)
        if not entries:
            return ft.Column(
                [titulo, ft.Text("Nenhuma regra ainda. Enter para criar a primeira.", size=12, color=INK_DIM)],
                spacing=10,
            )
        linhas = []
        for lista, tipo, valor in entries[:14]:
            cor = COR_MANTER if lista == "allowlist" else COR_LIXEIRA
            linhas.append(
                ft.Row(
                    [ft.Text(GUTTER, color=cor), ft.Text(tipo, size=11, color=INK_FAINT, width=70), ft.Text(valor, size=12, color=INK, no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True)],
                    spacing=8,
                )
            )
        if len(entries) > 14:
            linhas.append(ft.Text(f"… e mais {len(entries) - 14}", size=11, color=INK_FAINT))
        return ft.Column([titulo, *linhas], spacing=6, scroll=ft.ScrollMode.AUTO, expand=True)

    def _detalhe_gmail(self) -> ft.Control:
        titulo = self._titulo("Configurar Gmail")
        cfg = self.app.config
        if cfg is None:
            return ft.Column([titulo, ft.Text("Configuração não carregada.", size=12, color=INK_DIM)], spacing=10)
        if not cfg.gmail_client_id or not cfg.gmail_client_secret:
            return ft.Column(
                [
                    titulo,
                    ft.Text(
                        "Defina APOLO_GMAIL_CLIENT_ID e APOLO_GMAIL_CLIENT_SECRET\nno .env antes de autorizar uma conta.",
                        size=12, color=INK_DIM,
                    ),
                ],
                spacing=10,
            )
        from apolo.config import load_accounts

        contas = [a for a in load_accounts(cfg.accounts_path) if a.provider == "gmail"]
        invalidas = getattr(self.app, "contas_invalidas", {})
        if not contas:
            return ft.Column(
                [titulo, ft.Text("Nenhuma conta Gmail ainda. Enter pra autorizar a primeira.", size=12, color=INK_DIM)],
                spacing=10,
            )
        linhas = []
        for c in contas:
            motivo = invalidas.get(f"gmail:{c.name}")
            cor = COR_LIXEIRA if motivo else COR_MANTER
            estado = f"reautorizar: {motivo}" if motivo else "token ok"
            linhas.append(
                ft.Row(
                    [ft.Text(GUTTER, color=cor), ft.Text(c.name, size=12, color=INK, width=140), ft.Text(estado, size=11, color=cor)],
                    spacing=8,
                )
            )
        linhas.append(ft.Text("Enter pra adicionar/reautorizar uma conta.", size=11, color=INK_FAINT))
        return ft.Column([titulo, *linhas], spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)

    def _detalhe_config(self) -> ft.Control:
        titulo = self._titulo("Configurações")
        cfg = self.app.config
        if cfg is None:
            return ft.Column(
                [titulo, ft.Text("Ajustes de agendamento, IA, newsletters e credenciais.", size=12, color=INK_DIM)],
                spacing=10,
            )
        ia = "ligada" if cfg.ai_enabled else "desligada"
        cor_ia = COR_MANTER if cfg.ai_enabled else INK_FAINT
        return ft.Column(
            [
                titulo,
                ft.Row([ft.Text("IA / classificação:", size=12, color=INK_DIM), ft.Text(ia, size=12, color=cor_ia, weight=ft.FontWeight.BOLD)], spacing=6),
                ft.Text(f"Modelo: {cfg.ollama_model}", size=12, color=INK_DIM),
                ft.Text(f"Pastas: {', '.join(cfg.folders)}", size=12, color=INK_DIM),
                ft.Text("Enter para agendamento, IA, newsletters e senha do Bridge.", size=11, color=INK_FAINT),
            ],
            spacing=10,
        )

    def _detalhe_status(self) -> ft.Control:
        from apolo.gui.model import fmt_run

        st = self.app.stats
        titulo = self._titulo("Status & contadores")
        linhas: list[ft.Control] = [
            titulo,
            ft.Text(f"Última passada: {fmt_run(st.last_run)}", size=12, color=INK_DIM),
            ft.Text(f"Na fila: {len(self.app.queue)}", size=12, color=INK_DIM),
            ft.Text(f"Regras: {st.rules_count}", size=12, color=INK_DIM),
        ]
        if st.acao_counts:
            linhas.append(ft.Text("ação sugerida", size=11, color=INK_FAINT))
            for acao, n in sorted(st.acao_counts.items()):
                cor = ACAO_COR.get(acao, SOL)
                linhas.append(
                    ft.Row(
                        [ft.Text(GUTTER, color=cor), ft.Text(acao, size=12, color=INK_DIM, width=90), ft.Text(str(n), size=12, color=INK, weight=ft.FontWeight.BOLD)],
                        spacing=8,
                    )
                )
        return ft.Column(linhas, spacing=8, scroll=ft.ScrollMode.AUTO, expand=True)

    # ----- navegação por teclado -----
    def on_key(self, e: ft.KeyboardEvent) -> None:
        if self.foco is not None and self._painel_ativo is not None:
            self._painel_ativo.on_key(e)
            return
        k = key(e)
        if k in ARROW_UP:
            self._mover(-1)
        elif k in ARROW_DOWN:
            self._mover(1)
        elif k in ENTER:
            self._abrir(_MENU[self.idx][0])
        elif k in ({"q"} | ESCAPE):
            self.app.exit()

    def _mover(self, delta: int) -> None:
        if not self._boxes:
            return
        novo = (self.idx + delta) % len(_MENU)
        antigo = self.idx
        self.idx = novo
        self._boxes[antigo].bgcolor = SURFACE
        self._boxes[antigo].content.controls[0].color = INK
        self._boxes[novo].bgcolor = SOL
        self._boxes[novo].content.controls[0].color = "#FFFFFF"
        self._boxes[antigo].update()
        self._boxes[novo].update()
        if self._detail_container is not None:
            self._detail_container.content = self._detalhe(_MENU[novo][0])
            self._detail_container.update()

    # ----- roteamento (embutido, nunca navega pra tela cheia) -----
    def _criar_painel(self, chave: str):
        if chave == "review":
            from apolo.gui.queue import QueueScreen

            return QueueScreen()
        if chave == "rules":
            from apolo.gui.rules_screen import RulesScreen

            return RulesScreen()
        if chave == "gmail":
            from apolo.gui.gmail_setup import GmailSetupScreen

            return GmailSetupScreen()
        if chave == "config":
            from apolo.gui.settings import SettingsScreen

            return SettingsScreen()
        if chave == "status":
            from apolo.gui.status import StatusScreen

            return StatusScreen()
        raise ValueError(chave)

    def _abrir(self, chave: str) -> None:
        if self.foco == chave:
            return  # já aberto, nada a fazer
        if self._painel_ativo is not None:
            # Sai do painel anterior sem passar pelo Esc dele, mas ainda assim
            # precisa da limpeza (ex.: fila com decisão pendente não aplicada
            # não pode simplesmente sumir, ver QueueScreen.ao_perder_foco).
            getattr(self._painel_ativo, "ao_perder_foco", lambda: None)()
        self.idx = next(i for i, (k, _) in enumerate(_MENU) if k == chave)
        if chave == "review":
            self._recarregar_fila()
        painel = self._criar_painel(chave)
        painel.app = self.app
        painel.on_close = self._sair_do_foco
        self._painel_ativo = painel
        self.foco = chave
        self.app.refresh_top()

    def _sair_do_foco(self) -> None:
        self.foco = None
        self._painel_ativo = None
        self._recarregar_fila()
        self.app.refresh_top()

    def _recarregar_fila(self) -> None:
        """Relê a fila do banco: pega o que o timer (`apolo run` em paralelo)
        inseriu enquanto o dono estava fora do Hub."""
        if not self.app.config:
            return
        from apolo.gui.model import Item
        from apolo.storage.db import Storage

        try:
            with Storage(self.app.config.db_path) as store:
                self.app.queue = [Item(r) for r in store.fetch_queue()]
        except Exception:
            pass
