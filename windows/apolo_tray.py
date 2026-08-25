"""Ícone na bandeja do sistema (Windows): abre `apolo review` num terminal e
liga o Proton Bridge quando ele estiver desligado.

Equivalente Windows do botão da Waybar no Linux (ver docs/waybar.md): não faz
parte do núcleo do Apolo, só é um lançador. Clique duplo (ou o item padrão do
menu) abre um console novo rodando `python -m apolo.cli review`; "Ligar
Proton Bridge" testa se o Bridge já está escutando e, se não estiver, tenta
abri-lo — sem o Bridge rodando só contas Gmail funcionam (Proton depende
dele).

Não importa nada de `apolo/` — só chama `python -m apolo.cli review` como
subprocesso e lê o `.env` na unha (host/porta do Bridge), então o `.exe`
gerado por `build.bat` fica pequeno (só empacota pystray + Pillow) e não
precisa ser reconstruído quando o Apolo muda.
"""

from __future__ import annotations

import os
import socket
import subprocess
import sys
from pathlib import Path

import pystray
from PIL import Image

ICON_FILENAME = "apolo.ico"


def _app_dir() -> Path:
    """Pasta do .exe (congelado pelo PyInstaller) ou do script .py."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _project_root(app_dir: Path) -> Path:
    """Onde fica o `apolo/` a rodar — o .exe deve estar na raiz do projeto
    (ao lado de `.venv/`) ou dentro de `windows/`, ambos cobertos aqui."""
    if (app_dir / ".venv").exists():
        return app_dir
    if (app_dir.parent / ".venv").exists():
        return app_dir.parent
    return app_dir


def _python_exe(project_root: Path) -> str:
    venv_python = project_root / ".venv" / "Scripts" / "python.exe"
    if venv_python.exists():
        return str(venv_python)
    return "python"  # fallback: python do PATH, se o venv ainda não existir


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


def _candidatos_bridge() -> list[Path]:
    """Caminhos de instalação padrão do Proton Mail Bridge no Windows —
    varia entre versões/instaladores, então tenta vários antes de desistir."""
    program_files = Path(os.environ.get("ProgramFiles", r"C:\Program Files"))
    local_appdata_raw = os.environ.get("LOCALAPPDATA")
    candidatos = [
        program_files / "Proton AG" / "Proton Mail Bridge" / "Proton Mail Bridge.exe",
        program_files / "Proton AG" / "Proton Mail Bridge" / "proton-bridge.exe",
        program_files / "Proton Technologies AG" / "ProtonMail Bridge" / "Desktop-Bridge.exe",
    ]
    if local_appdata_raw:
        local_appdata = Path(local_appdata_raw)
        candidatos.append(
            local_appdata / "Programs" / "Proton AG" / "Proton Mail Bridge" / "Proton Mail Bridge.exe"
        )
    return candidatos


def _abrir_bridge() -> tuple[bool, str]:
    for exe in _candidatos_bridge():
        if exe.exists():
            os.startfile(str(exe))  # noqa: S606 — caminho vem de candidatos fixos, não de input externo
            return True, f"Abrindo {exe.name}..."
    return False, "Bridge não encontrado nos caminhos padrão. Abra manualmente."


def abrir_review(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    project_root = _project_root(_app_dir())
    python_exe = _python_exe(project_root)

    subprocess.Popen(
        [python_exe, "-m", "apolo.cli", "review"],
        cwd=str(project_root),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def ligar_bridge(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    project_root = _project_root(_app_dir())
    if _bridge_rodando(project_root):
        icon.notify("Proton Bridge já está rodando.", "Apolo")
        return
    ok, mensagem = _abrir_bridge()
    icon.notify(mensagem, "Apolo")


def _texto_bridge(item: pystray.MenuItem) -> str:
    project_root = _project_root(_app_dir())
    return "Bridge: rodando ✓" if _bridge_rodando(project_root) else "Ligar Proton Bridge"


def sair(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    icon.stop()


def main() -> None:
    icon_path = _app_dir() / ICON_FILENAME
    image = Image.open(icon_path)

    menu = pystray.Menu(
        pystray.MenuItem("Abrir revisão", abrir_review, default=True),
        pystray.MenuItem(_texto_bridge, ligar_bridge),
        pystray.MenuItem("Sair", sair),
    )
    icon = pystray.Icon("apolo", image, "Apolo · triagem de emails", menu)
    icon.run()


if __name__ == "__main__":
    main()
