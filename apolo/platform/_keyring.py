"""`SecretStore` genérico via o pacote `keyring` (PyPI) — compartilhado pelos
backends Windows e macOS (`win32/secret_store.py`, `darwin/secret_store.py`).

`keyring` já escolhe o backend nativo certo por SO — Credential Manager no
Windows, Keychain no macOS — sem precisar de código específico de SO aqui.
No Linux ele cairia no Secret Service (D-Bus), que este projeto evita de
propósito (ver `apolo/platform/linux/secret_store.py` e docs/secrets.md) —
por isso essa classe nunca é instanciada lá; `apolo/platform/__init__.py`
mantém o Linux só no backend `pass`.

Import de `keyring` é sempre lazy e protegido: se o pacote não estiver
instalado (é uma dependência opcional — ver requirements.txt), `disponivel()`
volta False e o resto degrada pra no-op/None, igual qualquer outro backend.
"""

from __future__ import annotations

_SERVICE = "apolo"
_BRIDGE_USER = "bridge-password"


def _account_user(account_id: str) -> str:
    return f"imap-account:{account_id}"


class KeyringSecretStore:
    def disponivel(self) -> bool:
        try:
            import keyring
            import keyring.backends.fail
        except ImportError:
            return False
        try:
            return not isinstance(keyring.get_keyring(), keyring.backends.fail.Keyring)
        except Exception:
            return False

    def _set(self, username: str, value: str) -> bool:
        if not self.disponivel() or not value:
            return False
        import keyring

        try:
            keyring.set_password(_SERVICE, username, value)
            return True
        except Exception:
            return False

    def _get(self, username: str) -> str | None:
        if not self.disponivel():
            return None
        import keyring

        try:
            return keyring.get_password(_SERVICE, username)
        except Exception:
            return None

    def _clear(self, username: str) -> bool:
        if not self.disponivel():
            return False
        import keyring
        import keyring.errors

        try:
            keyring.delete_password(_SERVICE, username)
            return True
        except keyring.errors.PasswordDeleteError:
            # já não existia — o chamador só quer garantir que sumiu.
            return False
        except Exception:
            return False

    def store_password(self, value: str) -> bool:
        return self._set(_BRIDGE_USER, value)

    def lookup_password(self) -> str | None:
        return self._get(_BRIDGE_USER)

    def clear_password(self) -> bool:
        return self._clear(_BRIDGE_USER)

    def store_account_password(self, account_id: str, value: str) -> bool:
        return self._set(_account_user(account_id), value)

    def lookup_account_password(self, account_id: str) -> str | None:
        return self._get(_account_user(account_id))

    def clear_account_password(self, account_id: str) -> bool:
        return self._clear(_account_user(account_id))
