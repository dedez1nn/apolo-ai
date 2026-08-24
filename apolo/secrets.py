"""Cofre de senha (Bridge + contas IMAP genéricas) — API pública estável do Apolo.

Delega pro backend de `apolo.platform` conforme o sistema operacional: `pass`
+ chave GPG dedicada no Linux (ver `apolo/platform/linux/secret_store.py` e
`docs/secrets.md` pro porquê dessa escolha), Credential Manager no Windows e
Keychain no macOS (via `keyring`, dependência opcional — ver
`apolo/platform/_keyring.py`).
"""

from __future__ import annotations

from apolo.platform import get_secret_store


def disponivel() -> bool:
    """True se o backend deste SO consegue guardar/ler segredo agora."""
    return get_secret_store().disponivel()


def motivo_indisponivel() -> str | None:
    """Por que `disponivel()` é False agora, em texto curto pro dono ler.
    None se `disponivel()` é True — nada a explicar."""
    return get_secret_store().motivo_indisponivel()


def store_password(value: str) -> bool:
    """Grava a senha do Bridge. Devolve True no sucesso, False se não deu."""
    return get_secret_store().store_password(value)


def lookup_password() -> str | None:
    """Lê a senha do Bridge; None se ausente, backend fora ou erro."""
    return get_secret_store().lookup_password()


def clear_password() -> bool:
    """Remove a senha do Bridge (útil ao trocar de conta)."""
    return get_secret_store().clear_password()


def store_account_password(account_id: str, value: str) -> bool:
    """Grava a senha (ou senha de app) de uma conta IMAP genérica."""
    return get_secret_store().store_account_password(account_id, value)


def lookup_account_password(account_id: str) -> str | None:
    """Lê a senha de uma conta IMAP genérica; None se ausente/backend fora."""
    return get_secret_store().lookup_account_password(account_id)


def clear_account_password(account_id: str) -> bool:
    """Remove a senha de uma conta IMAP genérica."""
    return get_secret_store().clear_account_password(account_id)
