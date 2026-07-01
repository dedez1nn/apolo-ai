"""Configurações — a única tela da UI que muda estado fora da fila.

Três destinos, três fontes:
  • Timer (intervalo + lig/des)  -> systemd via apolo.scheduler
  • IA / Ollama                  -> .env (escrita parcial, preserva credenciais)
  • Ação do List-Unsubscribe     -> rules/config.toml

Nada é aplicado enquanto você edita; só no `ctrl+s` (salvar). Mudança no .env
vale a partir da próxima passada (`apolo run`), não no processo já aberto.
"""

from __future__ import annotations

import shutil
import subprocess

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, VerticalScroll
from textual.screen import Screen
from textual.widgets import Button, Input, Label, Select, Static, Switch

from apolo import scheduler, secrets
from apolo.config_writer import env_path, set_env_values
from apolo.rules.writer import get_unsubscribe_acao, set_unsubscribe_acao
from apolo.ui.theme import COR_LIXEIRA, COR_MANTER, INK_DIM, INK_FAINT, keybar


def _bool_env(v: bool) -> str:
    return "true" if v else "false"


def _clipboard() -> str | None:
    """Lê o clipboard do sistema. None se nenhuma ferramenta servir.

    Colar dentro da TUI (kitty -e) depende do Ctrl+Shift+V do terminal e do
    bracketed-paste — frágil em campo de senha. Ler o clipboard aqui (wl-paste
    no Wayland, xclip/xsel no X11) contorna isso de vez.
    """
    for cmd in (
        ["wl-paste", "-n"],
        ["xclip", "-selection", "clipboard", "-o"],
        ["xsel", "-b"],
    ):
        if shutil.which(cmd[0]) is None:
            continue
        try:
            p = subprocess.run(cmd, capture_output=True, text=True, timeout=3, check=False)
        except (OSError, subprocess.SubprocessError):
            continue
        if p.returncode == 0:
            return p.stdout
    return None


class SettingsScreen(Screen):
    BINDINGS = [
        Binding("ctrl+s", "salvar", "salvar", priority=True),
        Binding("f2", "colar_senha", "colar senha", priority=True),
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
            "user": cfg.username,
        }
        intervalos = list(dict.fromkeys([*scheduler.INTERVALOS, intervalo]))

        yield Static(
            f"[b $accent]Configurações[/]  [{INK_DIM}]ajustes locais — nada é aplicado até salvar[/]",
            classes="band",
        )
        with VerticalScroll(id="cfg"):
            # A senha do Bridge troca a cada sessão dele; o dono cola a nova aqui
            # em vez de editar nada na mão. Senha vai pro keyring do SO; usuário
            # pro .env. Vale a partir da próxima passada.
            yield Static("[b]Bridge · credenciais[/]  [dim]senha no keyring · usuário no .env[/]",
                         classes="cfg-sec")
            with Horizontal(classes="cfg-row"):
                yield Label("Usuário", classes="cfg-lbl")
                yield Input(value=cfg.username, id="f-user")
            with Horizontal(classes="cfg-row"):
                yield Label("Senha", classes="cfg-lbl")
                yield Input(password=True, placeholder="•••• (vazio mantém a atual)",
                            id="f-senha")
                yield Button("Colar (F2)", id="paste-senha")

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

        yield Static("", id="cfg-msg", classes="flash")
        yield Static(
            keybar([("^S", "Salvar", COR_MANTER), ("F2", "Colar senha"), ("Q", "Voltar")]),
            classes="keybar",
        )

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
        elif event.button.id == "paste-senha":
            self.action_colar_senha()
        else:
            self.app.pop_screen()

    def action_colar_senha(self) -> None:
        """Preenche o campo de senha com o conteúdo do clipboard do sistema."""
        texto = _clipboard()
        if texto is None:
            self._msg(f"[{COR_LIXEIRA}]clipboard indisponível (instale wl-clipboard/xclip)[/]")
            return
        texto = texto.strip()
        if not texto:
            self._msg(f"[{INK_FAINT}]clipboard vazio[/]")
            return
        self.query_one("#f-senha", Input).value = texto
        self._msg(f"[{COR_MANTER}]✓ senha colada do clipboard (Ctrl+S para salvar)[/]")

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
            "user": self.query_one("#f-user", Input).value.strip(),
            "senha": self.query_one("#f-senha", Input).value.strip(),
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

        # 2a. Senha do Bridge -> keyring do SO (libsecret). Vazia = manter a atual;
        # só grava quando o dono digita uma nova.
        if novo["senha"]:
            if secrets.store_password(novo["senha"]):
                feitos.append("senha → keyring")
            else:
                erros.append("senha: keyring indisponível (secret-tool?)")

        # 2b. Usuário do Bridge + IA -> .env (preserva o resto). A senha NÃO vai
        # pro .env — fica só no keyring.
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

        # 3. Unsubscribe -> TOML.
        if novo["unsub"] != ini["unsub"]:
            try:
                set_unsubscribe_acao(cfg.rules_path, novo["unsub"])
                feitos.append(f"unsubscribe → {novo['unsub']}")
            except Exception as e:
                erros.append(f"unsubscribe: {e}")

        # Recarrega o Config em memória — senão "rodar agora" e o resto da sessão
        # da UI continuam com a senha/usuário antigos até fechar e reabrir.
        if self.app.config is not None:
            from apolo.config import Config

            self.app.config = Config.load()

        # feedback + atualiza snapshot pro próximo diff
        self._inicial = novo
        if erros:
            self._msg(f"[{COR_LIXEIRA}]" + " · ".join(erros) + "[/]")
            self.notify(" · ".join(erros), title="erro ao salvar", severity="error")
        elif feitos:
            self._msg(f"[{COR_MANTER}]✓ " + "  ·  ".join(feitos) + "[/]")
            self.notify("Configurações salvas.", severity="information")
        else:
            self._msg(f"[{INK_FAINT}]nada mudou[/]")
