"""`SecretStore` via `pass` (gerenciador baseado em GPG), com chave dedicada.

Por que não o Secret Service (`secret-tool`/gnome-keyring): é um D-Bus que
alguns ambientes desligam ou têm quebrado (ver docs/secrets.md) — `pass` não
depende de sessão D-Bus nenhuma, só do GPG local.

Por que `pass` com uma chave GPG **dedicada e sem senha** (não a pessoal):
o `apolo run` roda via timer do systemd --user, sem TTY e sem humano pra
responder pinentry algum — precisa ler o segredo de forma totalmente não
interativa. A chave pessoal do usuário tem senha (protege o keyring de GPG de
verdade); uma chave separada, só para essa automação, sem passphrase, decripta
na hora sem chamar pinentry. Só ela é usada para a subpasta `apolo/` do
password-store — isolada da chave pessoal, então comprometê-la não expõe o
resto do cofre. Detalhes completos em docs/secrets.md.

Requer um `.gpg-id` próprio em `~/.password-store/apolo/.gpg-id` apontando pra
essa chave (senão o `pass insert` cairia na chave padrão da store, que tem
senha, e o timer sem TTY quebraria silenciosamente). `disponivel()` confere
isso antes de qualquer operação.

Tudo stdlib + o binário `pass` (pacote `pass`, o "standard unix password
manager"). Se ele faltar ou a subpasta dedicada não existir, os métodos
degradam pra no-op/None e o chamador cai no fallback do .env.
"""

from __future__ import annotations

import os
import shutil
import subprocess

_TIMEOUT = 5.0  # s; um gpg-agent travado não pendura a UI nem o timer.

_BRIDGE_ENTRY = "apolo/bridge-password"


def _store_dir() -> str:
    """Raiz do password-store, respeitando PASSWORD_STORE_DIR como o `pass` faz."""
    return os.environ.get("PASSWORD_STORE_DIR") or os.path.expanduser("~/.password-store")


def _account_entry(account_id: str) -> str:
    """Caminho da entrada no password-store para uma conta IMAP genérica.

    `/` é trocado por `_` porque viraria diretório dentro da store — o
    identificador da conta não deveria ter barra, mas não custa blindar.
    """
    return f"apolo/imap-account/{account_id.replace('/', '_')}"


class LinuxSecretStore:
    def disponivel(self) -> bool:
        """True se `pass` existe no PATH e a subpasta `apolo/` tem sua própria chave.

        A checagem da chave dedicada importa: sem ela, um `pass insert` em
        `apolo/...` herdaria o `.gpg-id` da store pai (a chave pessoal, com
        senha), e o timer sem TTY passaria a travar esperando um pinentry que
        nunca vem.
        """
        if shutil.which("pass") is None:
            return False
        return os.path.isfile(os.path.join(_store_dir(), "apolo", ".gpg-id"))

    def _pass_insert(self, path: str, value: str) -> bool:
        """Grava `value` na entrada `path`, sobrescrevendo se já existir.

        `-m` lê o segredo inteiro do stdin (até EOF) sem eco — a senha nunca
        aparece na linha de comando / lista de processos. `-f` evita o prompt
        de confirmação de sobrescrita, que travaria uma chamada não interativa.
        """
        try:
            p = subprocess.run(
                ["pass", "insert", "-m", "-f", path],
                input=value,
                text=True,
                capture_output=True,
                timeout=_TIMEOUT,
                check=False,
            )
            return p.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def _pass_show(self, path: str) -> str | None:
        """Lê a entrada `path`; None se ausente, `pass`/gpg fora ou erro/timeout."""
        try:
            p = subprocess.run(
                ["pass", "show", path],
                text=True,
                capture_output=True,
                timeout=_TIMEOUT,
                check=False,
            )
        except (OSError, subprocess.SubprocessError):
            return None
        if p.returncode != 0:
            return None
        return p.stdout or None

    def _pass_rm(self, path: str) -> bool:
        try:
            p = subprocess.run(
                ["pass", "rm", "--force", path],
                capture_output=True,
                timeout=_TIMEOUT,
                check=False,
            )
            return p.returncode == 0
        except (OSError, subprocess.SubprocessError):
            return False

    def store_password(self, value: str) -> bool:
        if not self.disponivel() or not value:
            return False
        return self._pass_insert(_BRIDGE_ENTRY, value)

    def lookup_password(self) -> str | None:
        if not self.disponivel():
            return None
        return self._pass_show(_BRIDGE_ENTRY)

    def clear_password(self) -> bool:
        if not self.disponivel():
            return False
        return self._pass_rm(_BRIDGE_ENTRY)

    def store_account_password(self, account_id: str, value: str) -> bool:
        if not self.disponivel() or not value:
            return False
        return self._pass_insert(_account_entry(account_id), value)

    def lookup_account_password(self, account_id: str) -> str | None:
        if not self.disponivel():
            return None
        return self._pass_show(_account_entry(account_id))

    def clear_account_password(self, account_id: str) -> bool:
        if not self.disponivel():
            return False
        return self._pass_rm(_account_entry(account_id))
