"""Ícone na bandeja do sistema (Windows): abre `apolo review` num terminal e
liga o Proton Bridge quando ele estiver desligado.

Equivalente Windows do botão da Waybar no Linux (ver docs/waybar.md): não faz
parte do núcleo do Apolo, só é um lançador. A interface já abre sozinha
assim que o `.exe` inicia (clique no atalho), sem precisar caçar o ícone da
bandeja primeiro; o ícone continua disponível depois pra reabrir a revisão
(clique duplo, ou o item padrão do menu) ou ligar o Bridge — "Ligar Proton
Bridge" testa se ele já está escutando e, se não estiver, tenta abri-lo, já
que sem o Bridge rodando só contas Gmail funcionam (Proton depende dele).

Não importa nada de `apolo/` — só chama `python -m apolo.cli review` como
subprocesso e lê o `.env` na unha (host/porta do Bridge), então o `.exe`
gerado por `build.bat` fica pequeno (só empacota pystray + Pillow) e não
precisa ser reconstruído quando o Apolo muda.

O `apolo.ico` vai embutido dentro do `.exe` (`--add-data` no build.bat) —
só o `.exe` precisa ir pra raiz do projeto, nada de arquivo solto do lado.

Log: `--windowed` (sem console) faz qualquer erro de inicialização morrer em
silêncio -- nada de traceback visível em lugar nenhum. Por isso todo passo
arriscado escreve em `apolo_tray.log`, do lado do .exe (ver `_log`).
"""

from __future__ import annotations

import datetime
import os
import socket
import subprocess
import sys
import traceback
from pathlib import Path

ICON_FILENAME = "apolo.ico"


def _exe_dir() -> Path:
    """Pasta onde o .exe está de fato (congelado pelo PyInstaller) ou do
    script .py — usada pra achar o projeto (.venv) e onde escrever o log."""
    if getattr(sys, "frozen", False):
        return Path(sys.executable).resolve().parent
    return Path(__file__).resolve().parent


def _log(msg: str) -> None:
    """Anexa uma linha em windows\\apolo_tray.log. Nunca propaga erro —
    logging não pode ser mais uma causa de crash silencioso."""
    try:
        carimbo = datetime.datetime.now().isoformat(timespec="seconds")
        with (_exe_dir() / "apolo_tray.log").open("a", encoding="utf-8") as f:
            f.write(f"{carimbo} {msg}\n")
    except Exception:
        pass


_log("--- apolo_tray iniciando ---")
_log(f"sys.executable={sys.executable} frozen={getattr(sys, 'frozen', False)}")

try:
    import pystray
    from PIL import Image
except Exception:
    _log("FALHA AO IMPORTAR pystray/Pillow:\n" + traceback.format_exc())
    raise


def _resource_dir() -> Path:
    """Pasta com os recursos embutidos (apolo.ico). No .exe onefile do
    PyInstaller eles são extraídos num diretório temporário (`sys._MEIPASS`)
    em cada execução — não é a mesma pasta do .exe."""
    meipass = getattr(sys, "_MEIPASS", None)
    if meipass:
        return Path(meipass)
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


def _lancar_review() -> None:
    project_root = _project_root(_exe_dir())
    python_exe = _python_exe(project_root)
    _log(f"_lancar_review: project_root={project_root} python_exe={python_exe}")

    # shell=True com uma STRING (não uma lista) -- se fosse ["cmd", "/c",
    # comando], o Popen re-escapa as aspas que já estão dentro de `comando`
    # (list2cmdline duplica aspas internas), o que quebra o caminho do
    # python.exe. Com shell=True a string vai direto pro `cmd /c`, sem
    # reescaping.
    # `|| pause`: se `apolo.cli review` quebrar na hora, o console segura a
    # mensagem de erro em vez de fechar sozinho antes de dar tempo de ler.
    comando = f'"{python_exe}" -m apolo.cli review || pause'
    try:
        subprocess.Popen(
            comando,
            shell=True,
            cwd=str(project_root),
            creationflags=subprocess.CREATE_NEW_CONSOLE,
        )
        _log("_lancar_review: Popen disparado sem exceção")
    except Exception:
        _log("_lancar_review: FALHA no Popen:\n" + traceback.format_exc())


def abrir_review(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    _log("menu: Abrir revisão clicado")
    _lancar_review()


def ligar_bridge(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    _log("menu: Ligar Proton Bridge clicado")
    project_root = _project_root(_exe_dir())
    if _bridge_rodando(project_root):
        icon.notify("Proton Bridge já está rodando.", "Apolo")
        return
    ok, mensagem = _abrir_bridge()
    icon.notify(mensagem, "Apolo")


def _texto_bridge(item: pystray.MenuItem) -> str:
    project_root = _project_root(_exe_dir())
    return "Bridge: rodando ✓" if _bridge_rodando(project_root) else "Ligar Proton Bridge"


def sair(icon: pystray.Icon, item: pystray.MenuItem) -> None:
    _log("menu: Sair clicado")
    icon.stop()


def _setup(icon: pystray.Icon) -> None:
    # Passar `setup` pro `icon.run()` tira do pystray a responsabilidade de
    # mostrar o ícone sozinho -- quem faz isso é o próprio `setup` (ver
    # README do pystray). Esquecer o `visible = True` aqui faz o ícone nunca
    # aparecer na bandeja.
    _log("_setup: chamado")
    icon.visible = True
    _lancar_review()
    _log("_setup: concluído")


def main() -> None:
    _log("main: iniciando")
    icon_path = _resource_dir() / ICON_FILENAME
    _log(f"main: icon_path={icon_path} existe={icon_path.exists()}")
    image = Image.open(icon_path)

    menu = pystray.Menu(
        pystray.MenuItem("Abrir revisão", abrir_review, default=True),
        pystray.MenuItem(_texto_bridge, ligar_bridge),
        pystray.MenuItem("Sair", sair),
    )
    icon = pystray.Icon("apolo", image, "Apolo · triagem de emails", menu)
    _log("main: entrando em icon.run(setup=_setup)")
    # Abre a revisão já na largada -- clicar no atalho leva direto à
    # interface, sem precisar achar e clicar no ícone da bandeja primeiro.
    # O ícone continua rodando depois pra reabrir a revisão ou ligar o Bridge.
    icon.run(setup=_setup)


if __name__ == "__main__":
    try:
        main()
    except Exception:
        _log("FALHA EM main():\n" + traceback.format_exc())
        raise
