"""App Textual do Apolo — o hub que abre no clique da Waybar.

Fluxo: a Waybar abre `apolo review`, que entra aqui. A tela inicial (Hub) é
navegável só com seta + Enter; dela o dono escolhe o que fazer. Tudo dark, com
ícones e cores por ação, focado em teclado.

Esta camada é **offline**: lê a fila (rows) e escreve regras no TOML. O dispatch
real via IMAP acontece depois que o app fecha, no `cli`, com as decisões que o
app devolve em `dispatch_items`. Mantém o princípio "a TUI não toca a rede".
"""

from __future__ import annotations

from dataclasses import dataclass
import logging
from pathlib import Path

from textual import work
from textual.app import App

# Import cedo, ANTES do app rodar: textual-image sonda o terminal (protocolo
# gráfico + tamanho de célula) na primeira importação, e essa sondagem só
# funciona com o terminal "livre" — depois que o Textual assume o stdin (sua
# própria thread de leitura), a sondagem sempre falha e a lib cai pro pior
# fallback (blocos). O modo swipe importa isso tarde (só quando a tela abre);
# forçando aqui a detecção já roda no import do módulo, antes do app.run().
import textual_image.widget  # noqa: F401

from apolo.actions import DispatchItem
from apolo.logging_setup import current_log_path
from apolo.ui.hub import HubScreen
from apolo.ui.model import Item

logger = logging.getLogger("apolo.ui.app")


@dataclass
class UiStats:
    """Números que o Hub e a tela de Status mostram (lidos no cli, antes de abrir)."""

    last_run: str | None = None
    rules_count: int = 0
    status_counts: dict[str, int] | None = None
    acao_counts: dict[str, int] | None = None


class ApoloApp(App):
    """App raiz. Carrega o tema dark e guarda o estado compartilhado entre telas."""

    CSS_PATH = "app.tcss"
    TITLE = "apolo"
    SUB_TITLE = "triador de emails"

    def __init__(self, rows, rules_path: Path, stats: UiStats, config=None, contas_ativas: set | None = None):
        super().__init__()
        # Registra o tema ANTES do parse do TCSS — o CSS usa variáveis do tema
        # ($edge, $lixeira, $manter, $revisar), que precisam existir já no parse.
        from apolo.ui.theme import APOLO_THEME

        self.register_theme(APOLO_THEME)
        self.theme = "apolo-glass"
        # Fila compartilhada: as telas mutam esta lista (decidir/desfazer).
        self.queue: list[Item] = [Item(r) for r in rows]
        self.rules_path = Path(rules_path)
        self.stats = stats
        self.config = config
        self.dispatch_items: list[DispatchItem] = []
        # Conjunto de contas ativas — usado pelo EmailRow pra mostrar badge de conta.
        self._contas_ativas: set[str] = contas_ativas or {"proton"}
        # conta_id ("gmail:<nome>") -> motivo. Preenchido pela checagem de token
        # na abertura; o sincronizar (S) pula essas contas até reautorizar.
        self.contas_invalidas: dict[str, str] = {}

    def on_mount(self) -> None:
        self.push_screen(HubScreen())
        self._checar_contas_gmail()

    @work(thread=True)
    def _checar_contas_gmail(self) -> None:
        """Testa o token de cada conta Gmail em segundo plano ao abrir o app.

        Exceção ao princípio "a TUI não toca a rede" (como o sync embutido):
        detectar um token revogado aqui evita que o dono só descubra no meio
        de uma sincronização falhando.
        """
        if self.config is None:
            return
        from apolo.config import load_accounts
        from apolo.fetch.gmail import GmailClient

        for account in load_accounts(self.config.accounts_path):
            if account.provider != "gmail":
                continue
            client = GmailClient(
                account.name, account.client_id, account.client_secret,
                self.config.tokens_dir / f"{account.name}.json", folders=account.folders,
            )
            motivo = client.check_token()
            if motivo:
                self.call_from_thread(self._marcar_conta_invalida, f"gmail:{account.name}", motivo)

    def _marcar_conta_invalida(self, conta_id: str, motivo: str) -> None:
        from apolo.ui.theme import mesc

        self.contas_invalidas[conta_id] = motivo
        self.notify(
            mesc(
                f"{conta_id}: {motivo}\n"
                f"Vincule a conta de novo em “Configurar Gmail” — "
                f"até lá, sincronizar ignora essa conta."
            ),
            title="conta Gmail inválida",
            severity="warning",
            timeout=12,
        )


def run_ui(rows, rules_path: Path, stats: UiStats, config=None, contas_ativas: set | None = None) -> list[DispatchItem]:
    """Abre o app; devolve os itens a despachar (lixeira/manter) ao fechar."""
    app = ApoloApp(rows, rules_path, stats, config, contas_ativas=contas_ativas)
    try:
        app.run()
    except Exception:
        logger.exception("TUI caiu; veja o log em %s", current_log_path())
        raise
    return app.dispatch_items
