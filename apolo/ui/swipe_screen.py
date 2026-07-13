"""Fila de revisão em modo swipe — o mesmo despacho da listagem, só que como
um joguinho de cartas ao estilo Tinder.

Cada email vira uma carta; as setas decidem:
  ↑ manter   ↓ lixeira   ← bloquear (regra + lixeira)   → permitir (regra + manter)

A carta entra com uma animaçãozinha, e ao decidir troca o conteúdo pela
imagem-carimbo da ação (apolo/ui/assets/) e desliza pra fora na direção da
seta. Mesma semântica de sessão da fila normal: as decisões só ficam
marcadas localmente — Enter aplica de verdade (despacha via `DispatchModal`,
reusado daqui), Esc cancela tudo e devolve os itens pra fila.
"""

from __future__ import annotations

import os
from pathlib import Path

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical
from textual.geometry import Offset
from textual.screen import Screen
from textual.widgets import Static
from textual_image.widget import AutoImage, TGPImage

from apolo.actions import DispatchItem
from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER, parse_sender
from apolo.rules.writer import add_rule_entry, remove_rule_entry
from apolo.ui.model import Item, fmt_data, fmt_remetente
from apolo.ui.queue import DispatchModal
from apolo.ui.theme import (
    AMBER,
    COR_LIXEIRA,
    COR_MANTER,
    INK_DIM,
    INK_FAINT,
    keybar,
    mesc,
)

# textual-image sonda o terminal com DA1 e prioriza Sixel se ele responder que
# suporta — mas o kitty inclui esse bit por compatibilidade mesmo preferindo o
# protocolo próprio (TGP), que é nítido (pixel de verdade) contra o Sixel
# quantizado (paleta limitada, fica borrado). Força TGP quando é kitty de fato.
_ImageWidget = TGPImage if ("kitty" in os.environ.get("TERM", "") or os.environ.get("KITTY_WINDOW_ID")) else AutoImage

_ASSETS = Path(__file__).parent / "assets"

# seta -> (nome do carimbo, ação de despacho, lista de regra ou None)
_SETA_ACAO = {
    "up": ("apolo_manter", ACAO_MANTER, None),
    "down": ("apolo_lixeira", ACAO_LIXEIRA, None),
    "left": ("apolo_bloquear", ACAO_LIXEIRA, "blocklist"),
    "right": ("apolo_permitir", ACAO_MANTER, "allowlist"),
}

# direção em que a carta some da tela ao decidir (colunas, linhas)
_SETA_OFFSET = {
    "up": Offset(0, -40),
    "down": Offset(0, 40),
    "left": Offset(-100, 0),
    "right": Offset(100, 0),
}

_DUR_ENTRADA = 0.55
_DUR_SAIDA = 0.60


class SwipeCard(Vertical):
    """Uma carta: frente mostra remetente/assunto; ao decidir vira o carimbo."""

    DEFAULT_CSS = """
    SwipeCard {
        width: 46;
        height: 22;
        background: $panel;
        border: round $primary 40%;
        padding: 1 2;
        align: center middle;
    }
    SwipeCard #sc-stamp {
        width: 1fr;
        height: 1fr;
        align: center middle;
    }
    SwipeCard .sc-img {
        width: auto;
        height: auto;
    }
    """

    def __init__(self, item: Item):
        super().__init__()
        self.item = item

    def compose(self) -> ComposeResult:
        yield Static(self._texto(), id="sc-info", markup=True)

    def _texto(self) -> str:
        it = self.item
        rem = mesc(fmt_remetente(it.remetente))
        assunto = mesc(it.assunto or "(sem assunto)")
        data = fmt_data(it.data)
        linhas = [f"[b $accent]{rem}[/]", "", f"[$text]{assunto}[/]"]
        if data:
            linhas.append(f"\n[{INK_FAINT}]{data}[/]")
        return "\n".join(linhas)

    def mostrar_carimbo(self, nome: str) -> None:
        self.query("#sc-info").remove()
        self.mount(Center(_ImageWidget(str(_ASSETS / f"{nome}.png"), classes="sc-img"), id="sc-stamp"))


class SwipeScreen(Screen):
    BINDINGS = [
        Binding("up", "swipe('up')", "manter"),
        Binding("down", "swipe('down')", "lixeira"),
        Binding("left", "swipe('left')", "bloquear"),
        Binding("right", "swipe('right')", "permitir"),
        Binding("enter", "aplicar", "aplicar", priority=True),
        Binding("escape,q", "cancelar", "cancelar"),
    ]

    def compose(self) -> ComposeResult:
        yield Static(id="sw-header", classes="band")
        yield Vertical(id="sw-stage")
        yield Static("", id="sw-msg", classes="flash")
        yield Static(
            keybar(
                [
                    ("↑", "Manter", COR_MANTER),
                    ("↓", "Lixeira", COR_LIXEIRA),
                    ("←", "Bloquear", COR_LIXEIRA),
                    ("→", "Permitir", COR_MANTER),
                    ("↵", "Aplicar"),
                    ("Q", "Cancelar"),
                ]
            ),
            classes="keybar",
        )

    def on_mount(self) -> None:
        # (item, ação_anterior, rule_undo) por decisão — pra reverter no cancelar.
        self.hist: list[tuple] = []
        self._fila: list[Item] = list(self.app.queue)
        self._carta: SwipeCard | None = None
        self._render_header()
        self._proxima_carta()

    # ----- helpers -----
    @property
    def _stage(self) -> Vertical:
        return self.query_one("#sw-stage", Vertical)

    def _msg(self, texto: str = "") -> None:
        self.query_one("#sw-msg", Static).update(texto)

    def _render_header(self) -> None:
        n = len(self._fila)
        n_lix = sum(1 for h in self.hist if h[0].acao == ACAO_LIXEIRA)
        n_man = sum(1 for h in self.hist if h[0].acao == ACAO_MANTER)
        extra = (
            f"    [{INK_FAINT}]({len(self.hist)} decidido(s) · ↵ aplica · Q cancela)[/]"
            if self.hist
            else ""
        )
        self.query_one("#sw-header", Static).update(
            f"[b $accent]Modo swipe[/]   [{INK_DIM}]{n} restante(s)[/]{extra}\n"
            f"[{COR_LIXEIRA}]● {n_lix} lixeira[/]   [{COR_MANTER}]✓ {n_man} manter[/]"
        )

    def _proxima_carta(self) -> None:
        if not self._fila:
            self._carta = None
            self._msg(f"[{AMBER}]fila terminada — ↵ aplica, Q cancela[/]")
            return
        card = SwipeCard(self._fila[0])
        self._stage.mount(card)
        card.styles.opacity = 0.0
        card.styles.offset = Offset(0, 8)
        card.styles.animate("opacity", 1.0, duration=_DUR_ENTRADA)
        card.animate("offset", Offset(0, 0), duration=_DUR_ENTRADA, easing="out_back")
        self._carta = card

    # ----- decisão -----
    def action_swipe(self, direcao: str) -> None:
        if self._carta is None or not self._fila:
            return
        nome, acao, lista = _SETA_ACAO[direcao]
        it = self._fila[0]

        rule_undo = None
        if lista:
            _, dominio = parse_sender(it.remetente)
            if not dominio:
                self._msg(f"[{AMBER}]sem domínio no remetente — regra não criada[/]")
                return
            try:
                status = add_rule_entry(self.app.rules_path, lista=lista, tipo="dominio", valor=dominio)
            except Exception as e:
                self._msg(f"[{COR_LIXEIRA}]erro ao gravar regra: {mesc(str(e))}[/]")
                return
            rule_undo = (lista, "dominio", dominio) if status == "added" else None

        self._fila.pop(0)
        self.hist.append((it, it.acao, rule_undo))
        it.acao = acao
        self.app.queue.remove(it)

        carta = self._carta
        self._carta = None
        carta.mostrar_carimbo(nome)
        carta.animate("offset", _SETA_OFFSET[direcao], duration=_DUR_SAIDA, easing="in_cubic")
        carta.styles.animate("opacity", 0.0, duration=_DUR_SAIDA, on_complete=lambda: self._apos_saida(carta))

        rotulo = "lixeira" if acao == ACAO_LIXEIRA else "manter"
        cor = COR_LIXEIRA if acao == ACAO_LIXEIRA else COR_MANTER
        extra = f" ({lista})" if lista else ""
        self._msg(f"[{cor}]→ {rotulo}{extra}:[/] {mesc(fmt_remetente(it.remetente))}")
        self._render_header()

    async def _apos_saida(self, carta: SwipeCard) -> None:
        await carta.remove()
        self._proxima_carta()

    # ----- sair -----
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

        def _apos(msg: str | None) -> None:
            self.app.notify(f"Aplicado: {msg}" if msg else "Aplicado.", title="apolo")
            self.dismiss()

        self.app.push_screen(DispatchModal(itens), _apos)

    def action_cancelar(self) -> None:
        for it, acao_anterior, rule_undo in reversed(self.hist):
            if rule_undo:
                try:
                    remove_rule_entry(self.app.rules_path, lista=rule_undo[0], tipo=rule_undo[1], valor=rule_undo[2])
                except Exception:
                    pass
            it.acao = acao_anterior
            self.app.queue.append(it)
        self.hist = []
        self.dismiss()
