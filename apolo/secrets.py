"""Senha do Bridge no Secret Service do SO (libsecret), via `secret-tool`.

Por que aqui e não no .env: a senha do Proton Bridge troca a cada sessão dele,
e guardá-la em texto puro num arquivo é frágil. O Secret Service (gnome-keyring
/ kwallet) a mantém criptografada pela sessão e exposta por D-Bus — o mesmo
D-Bus que o systemd --user já entrega ao serviço agendado, então o `apolo run`
do timer lê a senha sem TTY e sem env.

Tudo stdlib + o binário `secret-tool` (pacote libsecret). Se ele faltar ou o
keyring estiver fora, as funções degradam pra no-op/None e o chamador cai no
fallback do .env.
"""

from __future__ import annotations

import shutil
import subprocess

# Atributos que identificam o segredo na coleção. Estáveis: store e lookup têm
# que casar exatamente, senão o lookup volta vazio.
_ATTRS = ("service", "apolo", "key", "bridge-password")
_LABEL = "Apolo — senha do Proton Bridge"
_TIMEOUT = 5.0  # s; um keyring travado não pendura a UI nem o timer.


def disponivel() -> bool:
    """True se o `secret-tool` existe no PATH (libsecret instalado)."""
    return shutil.which("secret-tool") is not None


def store_password(value: str) -> bool:
    """Grava a senha no keyring. Devolve True no sucesso, False se não deu.

    O `secret-tool store` lê o segredo do stdin (sem eco) — passamos por aí pra
    a senha nunca aparecer na linha de comando / lista de processos.
    """
    if not disponivel() or not value:
        return False
    try:
        p = subprocess.run(
            ["secret-tool", "store", "--label", _LABEL, *_ATTRS],
            input=value,
            text=True,
            capture_output=True,
            timeout=_TIMEOUT,
            check=False,
        )
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def lookup_password() -> str | None:
    """Lê a senha do keyring; None se ausente, keyring fora ou erro.

    rc 0 = achou (segredo no stdout, sem \\n final). rc 1 = não existe. Qualquer
    outro/timeout também vira None pra o chamador cair no fallback do .env.
    """
    if not disponivel():
        return None
    try:
        p = subprocess.run(
            ["secret-tool", "lookup", *_ATTRS],
            text=True,
            capture_output=True,
            timeout=_TIMEOUT,
            check=False,
        )
    except (OSError, subprocess.SubprocessError):
        return None
    if p.returncode != 0:
        return None
    senha = p.stdout
    return senha or None


def clear_password() -> bool:
    """Remove a senha do keyring (útil ao trocar de conta)."""
    if not disponivel():
        return False
    try:
        p = subprocess.run(
            ["secret-tool", "clear", *_ATTRS],
            capture_output=True,
            timeout=_TIMEOUT,
            check=False,
        )
        return p.returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False
