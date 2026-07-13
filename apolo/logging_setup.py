"""Logging central do Apolo — arquivo em disco, retenção de 7 dias.

Os `print()` espalhados pelo `cli.py` só aparecem no journalctl da sessão
onde o `apolo run` foi disparado (e, no timer do systemd, somem se aquela
invocação nunca chegou a rodar de verdade). Este módulo complementa isso
com um arquivo persistente — sobrevive a reinícios e registra tentativa por
tentativa de conexão/fetch, inclusive o que os prints não cobrem (retries,
UIDs que falharam, motivo exato de cada erro).

O arquivo gira a cada 7 dias (`TimedRotatingFileHandler`); com
`backupCount=1` guarda no máximo a semana atual + a anterior, descartando
o resto — não cresce sem limite.
"""

from __future__ import annotations

import atexit
import faulthandler
import logging
import logging.handlers
import os
import sys
import tempfile
import threading
from pathlib import Path


def _xdg_data() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def default_log_path() -> Path:
    return Path(_xdg_data()) / "apolo" / "apolo.log"


_configured = False
_log_path: Path | None = None
_fault_file = None


def current_log_path() -> Path:
    """Caminho efetivo do log configurado neste processo."""
    return _log_path or default_log_path()


def _resolve_log_path(path: Path) -> Path:
    try:
        path.parent.mkdir(parents=True, exist_ok=True)
        with open(path, "a", encoding="utf-8"):
            pass
        return path
    except OSError:
        fallback = Path(tempfile.gettempdir()) / "apolo.log"
        fallback.parent.mkdir(parents=True, exist_ok=True)
        with open(fallback, "a", encoding="utf-8"):
            pass
        return fallback


def _install_excepthooks() -> None:
    def _log_critical(msg: str, exc_info) -> None:
        logging.getLogger("apolo.crash").critical(msg, exc_info=exc_info)

    def _sys_hook(exc_type, exc, tb) -> None:
        if issubclass(exc_type, KeyboardInterrupt):
            sys.__excepthook__(exc_type, exc, tb)
            return
        _log_critical("exceção não tratada", (exc_type, exc, tb))

    def _thread_hook(args: threading.ExceptHookArgs) -> None:
        if issubclass(args.exc_type, KeyboardInterrupt):
            threading.__excepthook__(args)
            return
        nome = args.thread.name if args.thread else "thread-desconhecida"
        _log_critical(f"exceção não tratada em thread {nome}", (args.exc_type, args.exc_value, args.exc_traceback))

    def _unraisable_hook(unraisable) -> None:
        obj = getattr(unraisable, "object", None)
        where = f" em {obj!r}" if obj is not None else ""
        _log_critical(
            f"exceção não tratável{where}",
            (unraisable.exc_type, unraisable.exc_value, unraisable.exc_traceback),
        )

    sys.excepthook = _sys_hook
    threading.excepthook = _thread_hook
    sys.unraisablehook = _unraisable_hook


def _enable_faulthandler(path: Path) -> None:
    global _fault_file
    try:
        _fault_file = open(path, "a", encoding="utf-8")
    except OSError:
        return
    try:
        faulthandler.enable(_fault_file, all_threads=True)
    except Exception:
        _fault_file.close()
        _fault_file = None
        return

    @atexit.register
    def _close_fault_file() -> None:
        global _fault_file
        if _fault_file is None:
            return
        try:
            _fault_file.flush()
            _fault_file.close()
        finally:
            _fault_file = None


def setup_logging(level: str | None = None, *, log_path: Path | None = None) -> None:
    """Configura o logger raiz "apolo" uma única vez por processo."""
    global _configured, _log_path
    if _configured:
        return
    _configured = True

    path = _resolve_log_path(log_path or default_log_path())
    _log_path = path

    handler = logging.handlers.TimedRotatingFileHandler(
        path, when="D", interval=7, backupCount=1, encoding="utf-8"
    )
    handler.setFormatter(
        logging.Formatter("%(asctime)s %(levelname)-7s %(name)s: %(message)s")
    )

    logger = logging.getLogger("apolo")
    logger.setLevel((level or os.environ.get("APOLO_LOG_LEVEL", "INFO")).upper())
    logger.addHandler(handler)
    logger.propagate = False

    root = logging.getLogger()
    root.setLevel(logging.WARNING)
    root.addHandler(handler)

    _install_excepthooks()
    _enable_faulthandler(path)
