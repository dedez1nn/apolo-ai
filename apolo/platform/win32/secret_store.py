"""`SecretStore` via `keyring` — Credential Manager do Windows.

Implementação real em `apolo/platform/_keyring.py` (compartilhada com o
backend macOS — `keyring` já sabe escolher o Credential Manager sozinho, não
precisa de nada específico de Windows aqui). Dependência opcional — só é
importada se este backend for de fato usado (SO Windows).
"""

from __future__ import annotations

from apolo.platform._keyring import KeyringSecretStore as Win32SecretStore

__all__ = ["Win32SecretStore"]
