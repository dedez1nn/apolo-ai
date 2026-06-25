"""Configurações — a única tela da UI que muda estado fora da fila.

Três destinos, três fontes:
  • Timer (intervalo + lig/des)  -> systemd via apolo.scheduler
  • IA / Ollama                  -> .env (escrita parcial, preserva credenciais)
  • Ação do List-Unsubscribe     -> rules/config.toml

Nada é aplicado enquanto você edita; só no `ctrl+s` (salvar). Mudança no .env
vale a partir da próxima passada (`apolo run`), não no processo já aberto.
"""

from __future__ import annotations

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Footer, Input, Label, Select, Static, Switch

from apolo import scheduler
from apolo.config_writer import env_path, set_env_values
from apolo.rules.writer import get_unsubscribe_acao, set_unsubscribe_acao


def _bool_env(v: bool) -> str:
    return "true" if v else "false"


class SettingsScreen(Screen):
    BINDINGS = [
        Binding("ctrl+s", "salvar", "salvar", priority=True),
        Binding("escape,q", "app.pop_screen", "voltar"),
    ]

    def compose(self) -> ComposeResult:
        cfg = self._cfg()
        # snapshot inicial (pra diffar no salvar)
        ativo = scheduler.timer_ativo()
        intervalo = scheduler.intervalo_atual() or scheduler.INTERVALO_PADRAO
        unsub = get_unsubscribe_acao(cfg.rules_path)
        self._inicial = {
            "timer": ativo, "interval": intervalo, "ai": cfg.ai_enabled,
            "model": cfg.ollama_model, "keep": cfg.ollama_keep_alive, "unsub": unsub,
        }
        intervalos = list(dict.fromkeys([*scheduler.INTERVALOS, intervalo]))

        with VerticalScroll(id="cfg"):
            yield Static("[b]  Configurações[/]", classes="cfg-title")

            yield Static("[b]Agendamento[/]  [dim]systemd timer[/]", classes="cfg-sec")
            with Horizontal(classes="cfg-row"):
                yield Label("Intervalo", classes="cfg-lbl")
                yield Select([(i, i) for i in intervalos], value=intervalo,
                             allow_blank=False, id="f-interval")
            with Horizontal(classes="cfg-row"):
                yield Label("Timer ligado", classes="cfg-lbl")
                yield Switch(value=ativo, id="f-timer")

            yield Static("[b]IA · Ollama[/]  [dim]grava no .env[/]", classes="cfg-sec")
            with Horizontal(classes="cfg-row"):
                yield Label("Classificar resíduo", classes="cfg-lbl")
                yield Switch(value=cfg.ai_enabled, id="f-ai")
            with Horizontal(classes="cfg-row"):
                yield Label("Modelo", classes="cfg-lbl")
                yield Input(value=cfg.ollama_model, id="f-model")
            with Horizontal(classes="cfg-row"):
                yield Label("keep_alive", classes="cfg-lbl")
                yield Input(value=cfg.ollama_keep_alive, id="f-keep")

            yield Static("[b]Newsletters[/]  [dim]List-Unsubscribe + termo de marketing · TOML[/]",
                         classes="cfg-sec")
            with Horizontal(classes="cfg-row"):
                yield Label("Ação", classes="cfg-lbl")
                yield Select([("lixeira", "lixeira"), ("revisar", "revisar")],
                             value=unsub if unsub in ("lixeira", "revisar") else "revisar",
                             allow_blank=False, id="f-unsub")

            yield Static(self._geral(cfg), classes="cfg-sec")
            with Horizontal(id="cfg-actions"):
                yield Button("Salvar  (ctrl+s)", variant="primary", id="save")
                yield Button("Voltar  (esc)", id="back")

        yield Static("", id="cfg-msg")
        yield Footer()

    # ----- helpers -----
    def _cfg(self):
        if self.app.config is not None:
            return self.app.config
        from apolo.config import Config

        return Config.load()

    def _geral(self, cfg) -> str:
        return (
            "[b]Geral[/]  [dim]somente leitura[/]\n"
            f"  Pastas:   {', '.join(cfg.folders)}\n"
            f"  Lixeira:  {cfg.trash_folder}     IMAP: {cfg.imap_host}:{cfg.imap_port}\n"
            f"  Banco:    {cfg.db_path}\n"
            f"  Regras:   {cfg.rules_path}"
        )

    def _msg(self, texto: str) -> None:
        self.query_one("#cfg-msg", Static).update(texto)

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "save":
            self.action_salvar()
        else:
            self.app.pop_screen()

    # ----- salvar -----
    def action_salvar(self) -> None:
        cfg = self._cfg()
        ini = self._inicial
        novo = {
            "timer": self.query_one("#f-timer", Switch).value,
            "interval": self.query_one("#f-interval", Select).value,
            "ai": self.query_one("#f-ai", Switch).value,
            "model": self.query_one("#f-model", Input).value.strip(),
            "keep": self.query_one("#f-keep", Input).value.strip(),
            "unsub": self.query_one("#f-unsub", Select).value,
        }
        feitos: list[str] = []
        erros: list[str] = []

        # 1. Timer (systemd) — só mexe se mudou estado ou intervalo.
        if novo["timer"] != ini["timer"] or novo["interval"] != ini["interval"]:
            try:
                msg = scheduler.ativar(novo["interval"]) if novo["timer"] else scheduler.desativar()
                feitos.append(msg)
            except Exception as e:  # não derruba a tela
                erros.append(f"timer: {e}")

        # 2. IA -> .env (preserva o resto, inclusive credenciais).
        env_updates: dict[str, str] = {}
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

        # 3. Unsubscribe -> TOML.
        if novo["unsub"] != ini["unsub"]:
            try:
                set_unsubscribe_acao(cfg.rules_path, novo["unsub"])
                feitos.append(f"unsubscribe → {novo['unsub']}")
            except Exception as e:
                erros.append(f"unsubscribe: {e}")

        # feedback + atualiza snapshot pro próximo diff
        self._inicial = novo
        if erros:
            self._msg("[tomato]" + " · ".join(erros) + "[/]")
            self.notify(" · ".join(erros), title="erro ao salvar", severity="error")
        elif feitos:
            self._msg("[springgreen]✓ " + "  ·  ".join(feitos) + "[/]")
            self.notify("Configurações salvas.", severity="information")
        else:
            self._msg("[dim]nada mudou[/]")
