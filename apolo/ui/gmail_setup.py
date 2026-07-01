"""Modal de configuração do Gmail — OAuth2 device flow interativo.

Credenciais OAuth2 (client_id / client_secret) vêm do .env via Config
(APOLO_GMAIL_CLIENT_ID e APOLO_GMAIL_CLIENT_SECRET). O modal pede apenas
o nome da conta, salva em accounts.toml e guia o device flow na tela.
"""
from __future__ import annotations

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Static
def _esc(s: str) -> str:
    """Escapa colchetes para evitar MarkupError no Textual."""
    return s.replace("[", "\\[").replace("]", "\\]")

from apolo.ui.theme import AZURE_BRT, COR_LIXEIRA, COR_MANTER, INK_DIM, keybar


class GmailSetupModal(ModalScreen):
    """Adiciona uma conta Gmail via OAuth2 device flow."""

    BINDINGS = [
        Binding("escape", "cancelar", "cancelar"),
        Binding("ctrl+s", "autorizar", "autorizar", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="gmail-box"):
            yield Static(f"[{AZURE_BRT} b]Configurar Gmail[/]", classes="cfg-title")

            # ── fase 1: formulário ──────────────────────────────────────────
            with Vertical(id="gmail-form"):
                yield Static(
                    f"[{INK_DIM}]Credenciais OAuth2 lidas do .env\n"
                    f"(APOLO_GMAIL_CLIENT_ID / APOLO_GMAIL_CLIENT_SECRET).[/]",
                )
                with Horizontal(classes="cfg-row"):
                    yield Label("Nome da conta", classes="cfg-lbl")
                    yield Input(placeholder="gmail", id="g-name")
                yield Static("", id="g-form-msg", classes="flash")
                with Horizontal(id="cfg-actions"):
                    yield Button("Autorizar  (ctrl+s)", variant="primary", id="g-auth")
                    yield Button("Cancelar  (esc)", id="g-cancel")

            # ── fase 2: auth flow (oculto até autorizar) ───────────────────
            with Vertical(id="gmail-device"):
                yield Static(f"[{AZURE_BRT} b]Autorizar Gmail[/]")
                yield Static("", id="g-url")
                yield Static("", id="g-code")
                yield Static(f"[{INK_DIM}]aguardando autorização…[/]", id="g-status")
                yield Static("", id="g-result")
                with Horizontal(id="cfg-actions"):
                    yield Button("Fechar  (esc)", id="g-close")

        yield Static(
            keybar([("^S", "Autorizar"), ("Esc", "Cancelar")]),
            classes="keybar",
        )

    def on_mount(self) -> None:
        self.query_one("#gmail-device").display = False
        self.query_one("#g-name", Input).focus()

    # ── eventos ────────────────────────────────────────────────────────────
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "g-auth":
            self.action_autorizar()
        elif bid in ("g-cancel", "g-close"):
            self.dismiss(None)

    def action_autorizar(self) -> None:
        cfg = self._cfg()
        msg = self.query_one("#g-form-msg", Static)

        if not cfg.gmail_client_id or not cfg.gmail_client_secret:
            msg.update(
                f"[{COR_LIXEIRA}]Defina APOLO_GMAIL_CLIENT_ID e "
                f"APOLO_GMAIL_CLIENT_SECRET no .env antes de autorizar.[/]"
            )
            return

        name = self.query_one("#g-name", Input).value.strip() or "gmail"

        try:
            self._salvar_conta(name, cfg.gmail_client_id, cfg.gmail_client_secret)
        except Exception as e:
            msg.update(f"[{COR_LIXEIRA}]Erro ao salvar: {e}[/]")
            return

        self.query_one("#gmail-form").display = False
        self.query_one("#gmail-device").display = True
        self._device_flow(name, cfg.gmail_client_id, cfg.gmail_client_secret)

    def action_cancelar(self) -> None:
        self.dismiss(None)

    # ── helpers ────────────────────────────────────────────────────────────
    def _cfg(self):
        if self.app.config is not None:
            return self.app.config
        from apolo.config import Config
        return Config.load()

    def _salvar_conta(self, name: str, cid: str, csec: str) -> None:
        import tomllib

        cfg = self._cfg()
        path = cfg.accounts_path
        path.parent.mkdir(parents=True, exist_ok=True)

        existing: dict = {}
        if path.is_file():
            path.chmod(0o600)
            with path.open("rb") as f:
                existing = tomllib.load(f)

        accounts = existing.get("accounts", [])
        found = next((a for a in accounts if a.get("name") == name), None)
        if found is None:
            accounts.append({
                "name": name, "provider": "gmail",
                "client_id": cid, "client_secret": csec,
                "folders": ["INBOX"],
            })
        else:
            found["client_id"] = cid
            found["client_secret"] = csec
        lines = []
        for acc in accounts:
            if lines:
                lines.append("")
            lines.append("[[accounts]]")
            lines.append(f'name = "{acc["name"]}"')
            lines.append(f'provider = "{acc["provider"]}"')
            lines.append(f'client_id = "{acc["client_id"]}"')
            lines.append(f'client_secret = "{acc["client_secret"]}"')
            lines.append(f'folders = {acc["folders"]!r}')
        path.write_text("\n".join(lines) + "\n", encoding="utf-8")
        path.chmod(0o600)

    # ── device flow (thread) ───────────────────────────────────────────────
    @work(thread=True)
    def _device_flow(self, name: str, cid: str, csec: str) -> None:
        import traceback
        from pathlib import Path

        log = Path.home() / ".local" / "share" / "apolo" / "gmail_setup.log"
        log.parent.mkdir(parents=True, exist_ok=True)

        try:
            from apolo.fetch.gmail import GmailClient

            cfg = self._cfg()
            token_path = cfg.tokens_dir / f"{name}.json"
            client = GmailClient(name, cid, csec, token_path)

            def on_url(url: str) -> None:
                self.app.call_from_thread(self._show_device_info, url)

            client.authorize(on_url=on_url)
        except Exception as e:
            tb = traceback.format_exc()
            log.write_text(tb, encoding="utf-8")
            self.app.call_from_thread(self._show_error, str(e))
            return

        self.app.call_from_thread(self._show_success, name)

    def _show_device_info(self, url: str) -> None:
        self.query_one("#g-url", Static).update(
            f"[{INK_DIM}]Abra no browser e autorize:[/]\n\n"
            f"  [{AZURE_BRT}]{_esc(url)}[/]\n\n"
            f"[{INK_DIM}]Após autorizar o browser vai redirecionar para localhost\n"
            f"e a tela atualiza automaticamente.[/]"
        )
        self.query_one("#g-code").display = False

    def _show_error(self, msg: str) -> None:
        from pathlib import Path
        import traceback
        log = Path.home() / ".local" / "share" / "apolo" / "gmail_setup.log"
        try:
            self.query_one("#g-status", Static).update(
                f"[{COR_LIXEIRA}]Erro: {_esc(msg)}[/]"
            )
            log.write_text(log.read_text() + "\n_show_error OK\n", encoding="utf-8")
        except Exception:
            log.write_text(log.read_text() + f"\n_show_error FALHOU:\n{traceback.format_exc()}\n", encoding="utf-8")

    def _show_success(self, name: str) -> None:
        try:
            self.query_one("#g-status").display = False
            self.query_one("#g-result", Static).update(
                f"[{COR_MANTER} b]✓ Conta '{_esc(name)}' autorizada![/]\n"
                f"[{INK_DIM}]Feche e use 'Rodar agora' para buscar os emails.[/]"
            )
        except Exception:
            pass
