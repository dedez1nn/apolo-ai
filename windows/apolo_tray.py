"""Ícone na bandeja do sistema (Windows) que abre `apolo review` num terminal.

Equivalente Windows do botão da Waybar no Linux (ver docs/waybar.md): não faz
parte do núcleo do Apolo, só é um lançador. Clique duplo (ou o item padrão do
menu) abre um console novo rodando `python -m apolo.cli review`; o resto do
menu deixa sair.

Não importa nada de `apolo/` — só chama `python -m apolo.cli review` como
subprocesso, então o `.exe` gerado por `build.bat` fica pequeno (só empacota
pystray + Pillow) e não precisa ser reconstruído quando o Apolo muda.
"""

from __future__ import annotations

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


def abrir_review(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    app_dir = _app_dir()
    project_root = _project_root(app_dir)
    python_exe = _python_exe(project_root)

    subprocess.Popen(
        [python_exe, "-m", "apolo.cli", "review"],
        cwd=str(project_root),
        creationflags=subprocess.CREATE_NEW_CONSOLE,
    )


def sair(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    icon.stop()


def main() -> None:
    icon_path = _app_dir() / ICON_FILENAME
    image = Image.open(icon_path)

    menu = pystray.Menu(
        pystray.MenuItem("Abrir revisão", abrir_review, default=True),
        pystray.MenuItem("Sair", sair),
    )
    icon = pystray.Icon("apolo", image, "Apolo · triagem de emails", menu)
    icon.run()


if __name__ == "__main__":
    main()
