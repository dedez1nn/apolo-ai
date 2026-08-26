"""Fronteira entre o núcleo do Apolo e o sistema operacional.

Quatro preocupações dependem do SO — agendamento, notificação, cofre de
senha e clipboard — cada uma como um `Protocol` (`scheduler.py`,
`notifier.py`, `secret_store.py`, `clipboard.py`) implementado por um backend
concreto por sistema operacional (`linux/`).

O resto do código nunca importa daqui direto: `apolo/notify.py`,
`apolo/scheduler.py`, `apolo/secrets.py` e o clipboard em `apolo/extract.py`/
`apolo/gui/settings.py` continuam sendo a API pública estável, e por baixo
delegam pra `get_notifier()`/`get_scheduler()`/`get_secret_store()`/
`get_clipboard()` daqui. Isso é o único jeito de trocar o backend (adicionar
um SO novo) sem tocar em nada fora deste pacote.

Estado dos backends por preocupação:
  - `Notifier`/`Clipboard`/`SecretStore`: Linux, Windows e macOS —
    `familia_do_sistema()` escolhe. `SecretStore` em Windows/macOS usa `keyring` (PyPI,
    dependência opcional — só importada se um dos dois backends for de fato
    escolhido; ver `apolo/platform/_keyring.py`); no Linux continua sendo
    `pass` + chave GPG dedicada, de propósito (ver docs/secrets.md).
  - `Scheduler`: só Linux por enquanto (agendar via systemd/Task Scheduler/
    launchd é decisão de produto separada — `apolo run --loop` já cobre o
    caso de uso em qualquer SO nesse meio-tempo). `get_scheduler()` sempre
    devolve o backend Linux; como cada método já checa `shutil.which(...)`
    antes de qualquer chamada, isso não muda o comportamento observável num
    Windows/macOS de hoje.
"""

from __future__ import annotations

import sys

from apolo.platform.clipboard import Clipboard
from apolo.platform.notifier import Notifier
from apolo.platform.scheduler import Scheduler
from apolo.platform.secret_store import SecretStore


def familia_do_sistema() -> str:
    """'linux', 'darwin' ou 'win32' — as três famílias que `sys.platform` cobre
    na prática (variantes tipo 'linux2' já não existem no Python 3 atual).
    Pública porque `cli.py` também usa pra decidir se `apolo setup` faz
    sentido nesse SO (ver `cmd_setup`)."""
    if sys.platform.startswith("linux"):
        return "linux"
    if sys.platform == "darwin":
        return "darwin"
    if sys.platform == "win32":
        return "win32"
    return "desconhecida"


def get_notifier() -> Notifier:
    familia = familia_do_sistema()
    if familia == "win32":
        from apolo.platform.win32.notifier import Win32Notifier

        return Win32Notifier()
    if familia == "darwin":
        from apolo.platform.darwin.notifier import DarwinNotifier

        return DarwinNotifier()
    from apolo.platform.linux.notifier import LinuxNotifier

    return LinuxNotifier()


def get_scheduler() -> Scheduler:
    from apolo.platform.linux.scheduler import LinuxScheduler

    return LinuxScheduler()  # único backend por enquanto — ver docstring do módulo


def get_secret_store() -> SecretStore:
    familia = familia_do_sistema()
    if familia == "win32":
        from apolo.platform.win32.secret_store import Win32SecretStore

        return Win32SecretStore()
    if familia == "darwin":
        from apolo.platform.darwin.secret_store import DarwinSecretStore

        return DarwinSecretStore()
    from apolo.platform.linux.secret_store import LinuxSecretStore

    return LinuxSecretStore()


def get_clipboard() -> Clipboard:
    familia = familia_do_sistema()
    if familia == "win32":
        from apolo.platform.win32.clipboard import Win32Clipboard

        return Win32Clipboard()
    if familia == "darwin":
        from apolo.platform.darwin.clipboard import DarwinClipboard

        return DarwinClipboard()
    from apolo.platform.linux.clipboard import LinuxClipboard

    return LinuxClipboard()
