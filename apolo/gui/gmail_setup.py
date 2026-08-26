"""Configurar Gmail: autoriza uma conta nova ou existente via OAuth2.

Credenciais OAuth2 (client_id/client_secret) vêm do `.env`
(APOLO_GMAIL_CLIENT_ID/APOLO_GMAIL_CLIENT_SECRET), compartilhadas entre
todas as contas Gmail; a tela só pede o nome da conta. O resto (subir um
servidor local em loopback, trocar o código por token) é sempre
`apolo.fetch.gmail.GmailClient.authorize()`, só a apresentação mudou
(Textual -> Flet, ver `apolo/ui/gmail_setup.py` no histórico do git pra
comparar). Terceira exceção consciente ao "a UI não toca a rede" (as outras
duas: checar token Gmail ao abrir, buscar corpo do email).
"""

from __future__ import annotations

import flet as ft

from apolo.gui.theme import COR_LIXEIRA, COR_MANTER, INK, INK_DIM
from apolo.gui.widgets import ESCAPE, flash, header, key, keybar, rodape, scaffold

_NOVA_CONTA = ""


class GmailSetupScreen:
    def __init__(self) -> None:
        self.on_close = None
        self._msg_ref: ft.Ref[ft.Text] = ft.Ref()
        self._url_ref: ft.Ref[ft.Text] = ft.Ref()
        self._status_ref: ft.Ref[ft.Text] = ft.Ref()
        self._mounted = False

    # ----- montagem -----
    def build(self) -> ft.Control:
        cfg = self._cfg()
        self.existing_field = ft.Dropdown(
            label="Conta existente", value=_NOVA_CONTA, options=self._opcoes_contas(cfg),
            on_select=self._selecionou_existente,
        )
        self.name_field = ft.TextField(label="Nome da conta", hint_text="gmail", autofocus=True, color=INK)

        sem_credenciais = not cfg.gmail_client_id or not cfg.gmail_client_secret
        aviso = (
            "Defina APOLO_GMAIL_CLIENT_ID e APOLO_GMAIL_CLIENT_SECRET no .env antes de autorizar."
            if sem_credenciais else
            "Credenciais OAuth2 lidas do .env (compartilhadas entre todas as contas Gmail)."
        )

        self._fase_form = ft.Container(
            content=ft.Column(
                [
                    ft.Text(aviso, size=12, color=COR_LIXEIRA if sem_credenciais else INK_DIM),
                    self.existing_field,
                    self.name_field,
                    ft.Row(
                        [
                            ft.FilledButton("Autorizar (Ctrl+S)", on_click=lambda e: self._autorizar()),
                            ft.OutlinedButton("Remover conta", on_click=lambda e: self._remover()),
                        ],
                        spacing=10,
                    ),
                ],
                spacing=14,
            ),
        )
        self._fase_auth = ft.Container(
            content=ft.Column(
                [
                    ft.Text("Autorizar Gmail", size=15, weight=ft.FontWeight.W_600, color=INK),
                    ft.Text("", ref=self._url_ref, size=12, color=INK, selectable=True),
                    ft.Text("aguardando autorização…", ref=self._status_ref, size=13, color=INK_DIM),
                ],
                spacing=12,
            ),
            visible=False,
        )

        body = ft.Column([self._fase_form, self._fase_auth], spacing=24, expand=True, scroll=ft.ScrollMode.AUTO)
        self._mounted = True
        return scaffold(
            header("Configurar Gmail", "OAuth2 pelo navegador, credenciais no .env"),
            body,
            rodape(flash(self._msg_ref), keybar([("Ctrl+S", "Autorizar"), ("Esc", "Voltar")])),
        )

    # ----- teclado -----
    def on_key(self, e: ft.KeyboardEvent) -> None:
        k = key(e)
        if e.ctrl and k == "s":
            self._autorizar()
        elif k in ESCAPE:
            self._fechar()

    def _fechar(self) -> None:
        (self.on_close or self.app.pop_screen)()

    # ----- dados -----
    def _cfg(self):
        from apolo.config import Config

        return self.app.config or Config.load()

    def _contas_existentes(self, cfg) -> list:
        from apolo.config import load_accounts

        return [a for a in load_accounts(cfg.accounts_path) if a.provider == "gmail"]

    def _opcoes_contas(self, cfg) -> list[ft.DropdownOption]:
        opcoes = [ft.DropdownOption(key=_NOVA_CONTA, text="(nova conta)")]
        opcoes += [ft.DropdownOption(key=c.name, text=c.name) for c in self._contas_existentes(cfg)]
        return opcoes

    def _selecionou_existente(self, e: ft.ControlEvent) -> None:
        self.name_field.value = self.existing_field.value or ""
        if self._mounted:
            self.name_field.update()

    def _msg(self, texto: str) -> None:
        if self._msg_ref.current:
            self._msg_ref.current.value = texto
            self._msg_ref.current.update()

    # ----- ações -----
    def _remover(self) -> None:
        atual = self.existing_field.value or ""
        if not atual:
            self._msg("selecione uma conta existente pra remover")
            return
        cfg = self._cfg()
        try:
            self._remover_conta(cfg, atual)
        except Exception as e:
            self._msg(f"erro ao remover: {e}")
            return
        token_path = cfg.tokens_dir / f"{atual}.json"
        if token_path.is_file():
            token_path.unlink()
        self.name_field.value = ""
        self.name_field.update()
        self.existing_field.options = self._opcoes_contas(cfg)
        self.existing_field.value = _NOVA_CONTA
        self.existing_field.update()
        self._msg(f"✓ conta '{atual}' removida")

    def _autorizar(self) -> None:
        cfg = self._cfg()
        if not cfg.gmail_client_id or not cfg.gmail_client_secret:
            self._msg("defina APOLO_GMAIL_CLIENT_ID e APOLO_GMAIL_CLIENT_SECRET no .env antes de autorizar")
            return
        name = (self.name_field.value or "").strip() or "gmail"
        try:
            self._salvar_conta(cfg, name, cfg.gmail_client_id, cfg.gmail_client_secret)
        except Exception as e:
            self._msg(f"erro ao salvar: {e}")
            return

        self._fase_form.visible = False
        self._fase_auth.visible = True
        if self._url_ref.current:
            self._url_ref.current.value = "abrindo o navegador…"
        if self._status_ref.current:
            self._status_ref.current.value = "aguardando autorização…"
            self._status_ref.current.color = INK_DIM
        self._fase_form.update()
        self._fase_auth.update()
        self.app.page.run_thread(self._device_flow, name, cfg.gmail_client_id, cfg.gmail_client_secret)

    def _salvar_conta(self, cfg, name: str, cid: str, csec: str) -> None:
        import tomllib

        from apolo.cli import _write_accounts_toml

        path = cfg.accounts_path
        path.parent.mkdir(parents=True, exist_ok=True)
        existing: dict = {}
        if path.is_file():
            path.chmod(0o600)
            with path.open("rb") as f:
                existing = tomllib.load(f)
        accounts = existing.get("accounts", [])
        entry = {"name": name, "provider": "gmail", "client_id": cid, "client_secret": csec, "folders": ["INBOX"]}
        found = next((a for a in accounts if a.get("name") == name), None)
        if found is None:
            accounts.append(entry)
        else:
            found.update(entry)
        _write_accounts_toml(path, accounts)
        path.chmod(0o600)

    def _remover_conta(self, cfg, name: str) -> None:
        import tomllib

        from apolo.cli import _write_accounts_toml

        path = cfg.accounts_path
        if not path.is_file():
            return
        with path.open("rb") as f:
            existing = tomllib.load(f)
        accounts = [a for a in existing.get("accounts", []) if a.get("name") != name]
        _write_accounts_toml(path, accounts)
        path.chmod(0o600)

    # ----- autorização (thread) -----
    def _device_flow(self, name: str, cid: str, csec: str) -> None:
        from apolo.fetch.gmail import GmailClient

        cfg = self._cfg()
        token_path = cfg.tokens_dir / f"{name}.json"
        client = GmailClient(name, cid, csec, token_path)

        try:
            client.authorize(on_url=self._mostrar_url)
        except Exception as e:
            self._mostrar_erro(str(e))
            return
        self._mostrar_sucesso(name)

    def _mostrar_url(self, url: str) -> None:
        # Tenta abrir sozinho; se não der (sem navegador padrão configurado,
        # ambiente headless etc.), a URL abaixo já serve de reserva pra
        # copiar na mão; client.authorize() espera na porta local do mesmo
        # jeito, não depende do webbrowser.open() ter funcionado.
        try:
            import webbrowser

            webbrowser.open(url)
        except Exception:
            pass
        if self._url_ref.current:
            self._url_ref.current.value = (
                f"Se o navegador não abriu sozinho, copie e abra:\n\n{url}\n\n"
                "Após autorizar, a página redireciona pra localhost e esta tela\n"
                "atualiza sozinha."
            )
            self._url_ref.current.update()

    def _mostrar_erro(self, msg: str) -> None:
        if self._status_ref.current:
            self._status_ref.current.value = f"✗ erro: {msg}"
            self._status_ref.current.color = COR_LIXEIRA
            self._status_ref.current.update()

    def _mostrar_sucesso(self, name: str) -> None:
        invalidas = getattr(self.app, "contas_invalidas", None)
        if invalidas:
            invalidas.pop(f"gmail:{name}", None)
        if self._status_ref.current:
            self._status_ref.current.value = f"✓ conta '{name}' autorizada! Sincronize (S) na fila pra buscar os emails."
            self._status_ref.current.color = COR_MANTER
            self._status_ref.current.update()
