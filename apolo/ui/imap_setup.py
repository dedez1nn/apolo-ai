"""Modal de configuração de conta IMAP genérica (ex.: Outlook/Hotmail).

Ao contrário do Gmail (OAuth2 device flow), aqui é usuário+senha direto no
protocolo IMAP — a senha (ou senha de app) é pedida por prompt mascarado e vai
pro keyring do SO (apolo.secrets), nunca em texto puro no accounts.toml.
"""
from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical
from textual.screen import ModalScreen
from textual.widgets import Button, Input, Label, Select, Static

from apolo.ui.theme import AZURE_BRT, COR_LIXEIRA, COR_MANTER, INK_DIM, keybar, mesc

_NOVA_CONTA = ""  # valor sentinela do Select — "" = formulário em branco


class ImapSetupModal(ModalScreen):
    """Adiciona, edita ou remove uma conta IMAP genérica em accounts.toml."""

    BINDINGS = [
        Binding("escape", "cancelar", "cancelar"),
        Binding("ctrl+s", "salvar", "salvar", priority=True),
        Binding("f2", "colar_senha", "colar senha", priority=True),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="imap-box"):
            yield Static(f"[{AZURE_BRT} b]Configurar conta IMAP (Outlook e afins)[/]", classes="cfg-title")
            yield Static(
                f"[{INK_DIM}]A senha fica só no keyring do SO — nunca no accounts.toml.\n"
                f"Outlook.com/Hotmail pessoal: ative a verificação em duas etapas e\n"
                f"gere uma \"senha de aplicativo\" em account.live.com — a senha normal\n"
                f"costuma ser recusada. Conta Microsoft 365 corporativa não funciona\n"
                f"por aqui (Basic Auth desligado pela Microsoft).[/]",
            )

            with Horizontal(classes="cfg-row"):
                yield Label("Conta existente", classes="cfg-lbl")
                yield Select(self._opcoes_contas(), value=_NOVA_CONTA, allow_blank=False, id="i-existing")

            with Horizontal(classes="cfg-row"):
                yield Label("Nome da conta", classes="cfg-lbl")
                yield Input(placeholder="outlook", id="i-name")
            with Horizontal(classes="cfg-row"):
                yield Label("Host", classes="cfg-lbl")
                yield Input(placeholder="outlook.office365.com", id="i-host")
            with Horizontal(classes="cfg-row"):
                yield Label("Porta", classes="cfg-lbl")
                yield Input(value="993", id="i-port")
            with Horizontal(classes="cfg-row"):
                yield Label("Segurança", classes="cfg-lbl")
                yield Select(
                    [("SSL (porta 993, direto)", "SSL"), ("STARTTLS", "STARTTLS")],
                    value="SSL", allow_blank=False, id="i-security",
                )
            with Horizontal(classes="cfg-row"):
                yield Label("Usuário", classes="cfg-lbl")
                yield Input(placeholder="voce@outlook.com", id="i-user")
            with Horizontal(classes="cfg-row"):
                yield Label("Senha", classes="cfg-lbl")
                yield Input(password=True, placeholder="senha de app (F2 cola)", id="i-senha")
                yield Button("Colar (F2)", id="i-paste")
            with Horizontal(classes="cfg-row"):
                yield Label("Pasta lixeira", classes="cfg-lbl")
                yield Input(value="Deleted Items", id="i-trash")
            with Horizontal(classes="cfg-row"):
                yield Label("Pastas a vigiar", classes="cfg-lbl")
                yield Input(value="INBOX", id="i-folders")
            with Horizontal(classes="cfg-row"):
                yield Label("Lote (chunk_size)", classes="cfg-lbl")
                yield Input(value="50", id="i-chunk")

            yield Static("", id="i-msg", classes="flash")
            with Horizontal(id="cfg-actions"):
                yield Button("Salvar  (ctrl+s)", variant="primary", id="i-save")
                yield Button("Remover conta", variant="error", id="i-delete")
                yield Button("Cancelar  (esc)", id="i-cancel")

        yield Static(
            keybar([("^S", "Salvar"), ("F2", "Colar senha"), ("Esc", "Cancelar")]),
            classes="keybar",
        )

    def on_mount(self) -> None:
        self.query_one("#i-name", Input).focus()

    # ── eventos ────────────────────────────────────────────────────────────
    def on_button_pressed(self, event: Button.Pressed) -> None:
        bid = event.button.id
        if bid == "i-save":
            self.action_salvar()
        elif bid == "i-cancel":
            self.dismiss(None)
        elif bid == "i-paste":
            self.action_colar_senha()
        elif bid == "i-delete":
            self.action_remover()

    def on_select_changed(self, event: Select.Changed) -> None:
        if event.select.id == "i-existing":
            self._carregar_conta(str(event.value) if event.value is not None else "")

    def action_cancelar(self) -> None:
        self.dismiss(None)

    def action_colar_senha(self) -> None:
        from apolo.ui.settings import _clipboard

        valor = _clipboard()
        campo = self.query_one("#i-senha", Input)
        if valor:
            campo.value = valor.rstrip("\n")
            self.query_one("#i-msg", Static).update(f"[{COR_MANTER}]colado do clipboard.[/]")
        else:
            self.query_one("#i-msg", Static).update(
                f"[{COR_LIXEIRA}]clipboard vazio ou sem wl-paste/xclip/xsel instalado.[/]"
            )

    def action_salvar(self) -> None:
        from apolo import secrets

        msg = self.query_one("#i-msg", Static)

        name = self.query_one("#i-name", Input).value.strip()
        host = self.query_one("#i-host", Input).value.strip()
        username = self.query_one("#i-user", Input).value.strip()
        senha = self.query_one("#i-senha", Input).value

        if not (name and host and username):
            msg.update(f"[{COR_LIXEIRA}]nome, host e usuário são obrigatórios.[/]")
            return

        try:
            port = int(self.query_one("#i-port", Input).value.strip() or "993")
        except ValueError:
            msg.update(f"[{COR_LIXEIRA}]porta inválida.[/]")
            return
        try:
            chunk_size = int(self.query_one("#i-chunk", Input).value.strip() or "50")
        except ValueError:
            msg.update(f"[{COR_LIXEIRA}]chunk_size inválido.[/]")
            return

        security = self.query_one("#i-security", Select).value
        trash_folder = self.query_one("#i-trash", Input).value.strip() or "Trash"
        folders = self.query_one("#i-folders", Input).value.strip() or "INBOX"

        if not secrets.disponivel() and senha:
            msg.update(
                f"[{COR_LIXEIRA}]keyring (pass/gpg) indisponível — "
                f"a senha não pode ser guardada.[/]"
            )
            return

        try:
            self._salvar_conta(
                name=name, host=host, port=port, security=security, username=username,
                trash_folder=trash_folder, folders=folders, chunk_size=chunk_size,
            )
        except Exception as e:
            msg.update(f"[{COR_LIXEIRA}]erro ao salvar: {mesc(str(e))}[/]")
            return

        if senha:
            if not secrets.store_account_password(f"imap:{name}", senha):
                msg.update(
                    f"[{COR_LIXEIRA}]conta salva, mas não consegui guardar a senha no keyring.[/]"
                )
                return

        # Conta corrigida deixa de ser ignorada pelo sincronizar.
        getattr(self.app, "contas_invalidas", {}).pop(f"imap:{name}", None)
        self._atualizar_select(selecionar=name)
        msg.update(f"[{COR_MANTER} b]✓ conta '{mesc(name)}' salva.[/]")

    def action_remover(self) -> None:
        msg = self.query_one("#i-msg", Static)
        atual = self.query_one("#i-existing", Select).value
        if not atual:
            msg.update(f"[{COR_LIXEIRA}]selecione uma conta existente pra remover.[/]")
            return

        from apolo import secrets

        try:
            self._remover_conta(atual)
        except Exception as e:
            msg.update(f"[{COR_LIXEIRA}]erro ao remover: {mesc(str(e))}[/]")
            return
        secrets.clear_account_password(f"imap:{atual}")
        nome_removido = atual
        self._limpar_formulario()
        self._atualizar_select(selecionar=_NOVA_CONTA)
        msg.update(f"[{COR_MANTER} b]✓ conta '{mesc(nome_removido)}' removida.[/]")

    # ── helpers ────────────────────────────────────────────────────────────
    def _cfg(self):
        if self.app.config is not None:
            return self.app.config
        from apolo.config import Config

        return Config.load()

    def _contas_existentes(self):
        from apolo.config import load_accounts

        return [a for a in load_accounts(self._cfg().accounts_path) if a.provider == "imap"]

    def _opcoes_contas(self):
        return [("— nova conta —", _NOVA_CONTA)] + [(c.name, c.name) for c in self._contas_existentes()]

    def _atualizar_select(self, *, selecionar: str) -> None:
        sel = self.query_one("#i-existing", Select)
        sel.set_options(self._opcoes_contas())
        sel.value = selecionar

    def _limpar_formulario(self) -> None:
        self.query_one("#i-name", Input).value = ""
        self.query_one("#i-host", Input).value = ""
        self.query_one("#i-port", Input).value = "993"
        self.query_one("#i-security", Select).value = "SSL"
        self.query_one("#i-user", Input).value = ""
        self.query_one("#i-senha", Input).value = ""
        self.query_one("#i-trash", Input).value = "Deleted Items"
        self.query_one("#i-folders", Input).value = "INBOX"
        self.query_one("#i-chunk", Input).value = "50"

    def _carregar_conta(self, name: str) -> None:
        if not name:
            self._limpar_formulario()
            return
        conta = next((c for c in self._contas_existentes() if c.name == name), None)
        if conta is None:
            return
        self.query_one("#i-name", Input).value = conta.name
        self.query_one("#i-host", Input).value = conta.host
        self.query_one("#i-port", Input).value = str(conta.port)
        self.query_one("#i-security", Select).value = conta.security
        self.query_one("#i-user", Input).value = conta.username
        # Senha nunca é reexibida — em branco aqui mantém a atual ao salvar.
        self.query_one("#i-senha", Input).value = ""
        self.query_one("#i-trash", Input).value = conta.trash_folder
        self.query_one("#i-folders", Input).value = ", ".join(conta.folders)
        self.query_one("#i-chunk", Input).value = str(conta.chunk_size)

    def _salvar_conta(
        self, *, name: str, host: str, port: int, security: str, username: str,
        trash_folder: str, folders: str, chunk_size: int,
    ) -> None:
        import tomllib

        from apolo.cli import _write_accounts_toml

        cfg = self._cfg()
        path = cfg.accounts_path
        path.parent.mkdir(parents=True, exist_ok=True)

        existing: dict = {}
        if path.is_file():
            path.chmod(0o600)
            with path.open("rb") as f:
                existing = tomllib.load(f)

        accounts = existing.get("accounts", [])
        folders_list = [f.strip() for f in folders.split(",") if f.strip()] or ["INBOX"]
        entry = {
            "name": name, "provider": "imap", "host": host, "port": port,
            "security": security, "username": username, "trash_folder": trash_folder,
            "chunk_size": chunk_size, "folders": folders_list,
        }

        found = next((a for a in accounts if a.get("name") == name), None)
        if found is None:
            accounts.append(entry)
        else:
            found.update(entry)

        _write_accounts_toml(path, accounts)
        path.chmod(0o600)

    def _remover_conta(self, name: str) -> None:
        import tomllib

        from apolo.cli import _write_accounts_toml

        cfg = self._cfg()
        path = cfg.accounts_path
        if not path.is_file():
            return
        with path.open("rb") as f:
            existing = tomllib.load(f)
        accounts = [a for a in existing.get("accounts", []) if a.get("name") != name]
        _write_accounts_toml(path, accounts)
        path.chmod(0o600)
