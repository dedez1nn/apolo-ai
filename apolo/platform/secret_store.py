"""Contrato de cofre de senha — um `SecretStore` por sistema operacional.

`apolo/secrets.py` é a API pública estável (usada por `config.py`, `actions.py`,
`sync.py`, `cli.py` e as telas de conta/configurações); este módulo só define
o contrato. Ver `apolo/platform/linux/secret_store.py` pro único backend
implementado até agora (`pass` + chave GPG dedicada).
"""

from __future__ import annotations

from typing import Protocol


class SecretStore(Protocol):
    def disponivel(self) -> bool:
        """True se este backend consegue guardar/ler segredo agora."""
        ...

    def store_password(self, value: str) -> bool:
        """Grava a senha do Bridge. True no sucesso."""
        ...

    def lookup_password(self) -> str | None:
        """Lê a senha do Bridge; None se ausente ou backend indisponível."""
        ...

    def clear_password(self) -> bool:
        """Remove a senha do Bridge."""
        ...

    def store_account_password(self, account_id: str, value: str) -> bool:
        """Grava a senha de uma conta externa (ex.: IMAP genérico)."""
        ...

    def lookup_account_password(self, account_id: str) -> str | None:
        """Lê a senha de uma conta externa; None se ausente."""
        ...

    def clear_account_password(self, account_id: str) -> bool:
        """Remove a senha de uma conta externa."""
        ...
