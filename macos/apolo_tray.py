"""Ícone na barra de menu (macOS): abre `apolo review` num Terminal e liga o
Proton Bridge quando ele estiver desligado.

Equivalente macOS do botão da Waybar no Linux (ver docs/waybar.md) e do
ícone de bandeja do Windows (ver windows/apolo_tray.py) — mesma ideia, casca
nativa via rumps. "Abrir revisão" abre um Terminal.app novo rodando
`python -m apolo.cli review`; "Ligar Proton Bridge" testa se o Bridge já
está escutando e, se não, roda `open -a "Proton Mail Bridge"` — sem o Bridge
rodando só contas Gmail funcionam (Proton depende dele).

Não importa nada de `apolo/` — só chama `python -m apolo.cli review` como
subprocesso e lê o `.env` na unha (host/porta do Bridge), então o `.app`
gerado por `build.sh` fica pequeno e não precisa ser reconstruído quando o
Apolo muda.
"""

from __future__ import annotations

import shlex
import socket
import subprocess
import sys
from pathlib import Path

import rumps

ICON_FILENAME = "apolo.icns"


def _app_dir() -> Path:
    """Pasta que contém o `Apolo.app` (congelado pelo py2app) ou o script
    `.py`, subindo a partir do executável até sair do bundle `.app/`."""
    if getattr(sys, "frozen", False):
        exe = Path(sys.executable).resolve()
        for parent in exe.parents:
            if parent.suffix == ".app":
                return parent.parent
        return exe.parent
    return Path(__file__).resolve().parent


def _project_root(app_dir: Path) -> Path:
    """Onde fica o `apolo/` a rodar — o `.app` deve estar na raiz do
    projeto (ao lado de `.venv/`) ou dentro de `macos/`, ambos cobertos aqui."""
    if (app_dir / ".venv").exists():
        return app_dir
    if (app_dir.parent / ".venv").exists():
        return app_dir.parent
    return app_dir


def _python_exe(project_root: Path) -> str:
    venv_python = project_root / ".venv" / "bin" / "python"
    if venv_python.exists():
        return str(venv_python)
    return "python3"  # fallback: python do PATH, se o venv ainda não existir


def _read_env(project_root: Path) -> dict[str, str]:
    """Parser mínimo do `.env` — só o suficiente pra achar host/porta do
    Bridge, sem depender de `apolo.config` (mantém este script standalone)."""
    values: dict[str, str] = {}
    env_path = project_root / ".env"
    if not env_path.exists():
        return values
    for line in env_path.read_text(encoding="utf-8", errors="ignore").splitlines():
        line = line.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, value = line.partition("=")
        values[key.strip()] = value.strip()
    return values


def _bridge_host_port(project_root: Path) -> tuple[str, int]:
    env = _read_env(project_root)
    host = env.get("APOLO_IMAP_HOST") or "127.0.0.1"
    try:
        port = int(env.get("APOLO_IMAP_PORT") or "1143")
    except ValueError:
        port = 1143
    return host, port


def _bridge_rodando(project_root: Path) -> bool:
    host, port = _bridge_host_port(project_root)
    try:
        with socket.create_connection((host, port), timeout=1.5):
            return True
    except OSError:
        return False


class ApoloApp(rumps.App):
    def __init__(self) -> None:
        icon_path = _app_dir() / ICON_FILENAME
        super().__init__(
            "Apolo",
            icon=str(icon_path) if icon_path.exists() else None,
            quit_button="Sair",
        )
        self.menu = ["Abrir revisão", "Ligar Proton Bridge"]

    @rumps.clicked("Abrir revisão")
    def abrir_review(self, _sender: rumps.MenuItem) -> None:
        project_root = _project_root(_app_dir())
        python_exe = _python_exe(project_root)
        comando = f"cd {shlex.quote(str(project_root))} && {shlex.quote(python_exe)} -m apolo.cli review"
        # comando vira um literal de string do AppleScript — escapa aspas/barras
        # antes de embutir (paths normais não têm nenhuma, mas não custa).
        comando_applescript = comando.replace("\\", "\\\\").replace('"', '\\"')
        subprocess.Popen(
            ["osascript", "-e", f'tell application "Terminal" to do script "{comando_applescript}"']
        )

    @rumps.clicked("Ligar Proton Bridge")
    def ligar_bridge(self, _sender: rumps.MenuItem) -> None:
        project_root = _project_root(_app_dir())
        if _bridge_rodando(project_root):
            rumps.notification("Apolo", "", "Proton Bridge já está rodando.")
            return
        resultado = subprocess.run(
            ["open", "-a", "Proton Mail Bridge"], capture_output=True, text=True
        )
        if resultado.returncode == 0:
            rumps.notification("Apolo", "", "Abrindo Proton Mail Bridge...")
        else:
            rumps.notification(
                "Apolo", "", "Proton Mail Bridge não encontrado. Abra manualmente."
            )


def main() -> None:
    ApoloApp().run()


if __name__ == "__main__":
    main()
