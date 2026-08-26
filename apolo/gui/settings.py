"""Configurações: a única tela que muda estado fora da fila.

Três destinos: timer (systemd, via `apolo.scheduler`), IA/Ollama (`.env`) e
ação do List-Unsubscribe (`rules/config.toml`). Nada aplica até "Salvar".
"""

from __future__ import annotations

import flet as ft

from apolo.gui.theme import INK, INK_FAINT
from apolo.gui.widgets import ESCAPE, flash, header, key, keybar, rodape, scaffold


def _bool_env(v: bool) -> str:
    return "true" if v else "false"


def _sec(titulo: str, sub: str = "") -> ft.Text:
    return ft.Text(f"{titulo}" + (f"   ·   {sub}" if sub else ""), size=13, weight=ft.FontWeight.W_600, color=INK)


class SettingsScreen:
    def __init__(self) -> None:
        self.on_close = None

    def build(self) -> ft.Control:
        from apolo import scheduler
        from apolo.config import Config
        from apolo.rules.writer import get_unsubscribe_acao

        cfg = self.app.config or Config.load()
        self._cfg = cfg
        ativo = scheduler.timer_ativo()
        intervalo = scheduler.intervalo_atual() or scheduler.INTERVALO_PADRAO
        unsub = get_unsubscribe_acao(cfg.rules_path)
        self._inicial = {
            "timer": ativo, "interval": intervalo, "ai": cfg.ai_enabled,
            "model": cfg.ollama_model, "keep": cfg.ollama_keep_alive, "unsub": unsub,
            "user": cfg.username,
        }
        intervalos = list(dict.fromkeys([*scheduler.INTERVALOS, intervalo]))

        self.f_user = ft.TextField(label="Usuário", value=cfg.username, color=INK)
        self.f_senha = ft.TextField(label="Senha", password=True, can_reveal_password=True,
                                     hint_text="vazio mantém a atual", color=INK)
        self.f_interval = ft.Dropdown(
            label="Intervalo", value=intervalo,
            options=[ft.DropdownOption(key=i, text=i) for i in intervalos],
        )
        self.f_timer = ft.Switch(label="Timer ligado", value=ativo)
        self.f_ai = ft.Switch(label="Classificar resíduo", value=cfg.ai_enabled)
        self.f_model = ft.TextField(label="Modelo", value=cfg.ollama_model, color=INK)
        self.f_keep = ft.TextField(label="keep_alive", value=cfg.ollama_keep_alive, color=INK)
        self.f_unsub = ft.Dropdown(
            label="Ação List-Unsubscribe",
            value=unsub if unsub in ("lixeira", "revisar") else "revisar",
            options=[ft.DropdownOption(key="lixeira", text="lixeira"), ft.DropdownOption(key="revisar", text="revisar")],
        )

        self._msg_ref: ft.Ref[ft.Text] = ft.Ref()

        geral = ft.Text(
            "Geral (somente leitura)\n"
            f"Pastas: {', '.join(cfg.folders)}\n"
            f"Lixeira: {cfg.trash_folder}     IMAP: {cfg.imap_host}:{cfg.imap_port}\n"
            f"Banco: {cfg.db_path}\n"
            f"Regras: {cfg.rules_path}",
            size=11, color=INK_FAINT,
        )

        body = ft.Column(
            [
                _sec("Bridge · credenciais", "senha no keyring · usuário no .env"),
                ft.Row([self.f_user], spacing=10),
                ft.Row([self.f_senha, ft.FilledButton("Colar (F2)", on_click=lambda e: self._colar_senha())], spacing=10),
                ft.Divider(color=INK_FAINT, opacity=0.2),
                _sec("Agendamento", "systemd timer"),
                ft.Row([self.f_interval, self.f_timer], spacing=20),
                ft.Divider(color=INK_FAINT, opacity=0.2),
                _sec("IA · Ollama", "grava no .env"),
                ft.Row([self.f_ai], spacing=10),
                ft.Row([self.f_model, self.f_keep], spacing=10),
                ft.Divider(color=INK_FAINT, opacity=0.2),
                _sec("Newsletters", "List-Unsubscribe · TOML"),
                ft.Row([self.f_unsub], spacing=10),
                ft.Divider(color=INK_FAINT, opacity=0.2),
                geral,
                ft.Row([ft.FilledButton("Salvar (Ctrl+S)", on_click=lambda e: self._salvar())], spacing=10),
            ],
            spacing=14, scroll=ft.ScrollMode.AUTO, expand=True,
        )
        return scaffold(
            header("Configurações", "ajustes locais, nada é aplicado até salvar"),
            body,
            rodape(flash(self._msg_ref), keybar([("Ctrl+S", "Salvar"), ("F2", "Colar senha"), ("Esc", "Voltar")])),
        )

    def on_key(self, e: ft.KeyboardEvent) -> None:
        k = key(e)
        if e.ctrl and k == "s":
            self._salvar()
        elif k == "f2":
            self._colar_senha()
        elif k in ESCAPE:
            (self.on_close or self.app.pop_screen)()

    def _msg(self, texto: str) -> None:
        if self._msg_ref.current:
            self._msg_ref.current.value = texto
            self._msg_ref.current.update()

    def _colar_senha(self) -> None:
        from apolo.platform import get_clipboard

        texto = get_clipboard().paste()
        if texto is None:
            self._msg("clipboard indisponível (instale wl-clipboard/xclip)")
            return
        texto = texto.strip()
        if not texto:
            self._msg("clipboard vazio")
            return
        self.f_senha.value = texto
        self.f_senha.update()
        self._msg("✓ senha colada do clipboard (Ctrl+S para salvar)")

    def _salvar(self) -> None:
        from apolo import scheduler, secrets
        from apolo.config import Config
        from apolo.config_writer import env_path, set_env_values
        from apolo.rules.writer import set_unsubscribe_acao

        ini = self._inicial
        novo = {
            "timer": self.f_timer.value, "interval": self.f_interval.value,
            "ai": self.f_ai.value, "model": (self.f_model.value or "").strip(),
            "keep": (self.f_keep.value or "").strip(), "unsub": self.f_unsub.value,
            "user": (self.f_user.value or "").strip(), "senha": (self.f_senha.value or "").strip(),
        }
        feitos: list[str] = []
        erros: list[str] = []

        if novo["timer"] != ini["timer"] or novo["interval"] != ini["interval"]:
            try:
                msg = scheduler.ativar(novo["interval"]) if novo["timer"] else scheduler.desativar()
                feitos.append(msg)
            except Exception as e:
                erros.append(f"timer: {e}")

        if novo["senha"]:
            if secrets.store_password(novo["senha"]):
                feitos.append("senha → keyring")
            else:
                erros.append(f"senha: {secrets.motivo_indisponivel() or 'cofre de senha indisponível'}")

        env_updates: dict[str, str] = {}
        if novo["user"] and novo["user"] != ini["user"]:
            env_updates["APOLO_USERNAME"] = novo["user"]
        if novo["ai"] != ini["ai"]:
            env_updates["APOLO_AI_ENABLED"] = _bool_env(novo["ai"])
        if novo["model"] and novo["model"] != ini["model"]:
            env_updates["APOLO_OLLAMA_MODEL"] = novo["model"]
        if novo["keep"] and novo["keep"] != ini["keep"]:
            env_updates["APOLO_OLLAMA_KEEP_ALIVE"] = novo["keep"]
        if env_updates:
            try:
                set_env_values(env_path(), env_updates)
                feitos.append(f".env: {', '.join(env_updates)} (vale na próxima passada)")
            except Exception as e:
                erros.append(f".env: {e}")

        if novo["unsub"] != ini["unsub"]:
            try:
                set_unsubscribe_acao(self._cfg.rules_path, novo["unsub"])
                feitos.append(f"unsubscribe → {novo['unsub']}")
            except Exception as e:
                erros.append(f"unsubscribe: {e}")

        if self.app.config is not None:
            self.app.config = Config.load()

        self._inicial = novo
        if erros:
            self._msg("⚠ " + " · ".join(erros))
            self.app.notify(" · ".join(erros), severity="error")
        elif feitos:
            self._msg("✓ " + "  ·  ".join(feitos))
            self.app.notify("Configurações salvas.")
        else:
            self._msg("nada mudou")
