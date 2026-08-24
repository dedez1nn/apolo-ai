"""`SecretStore` via `keyring` — Keychain do macOS.

Implementação real em `apolo/platform/_keyring.py` (compartilhada com o
backend Windows — `keyring` já sabe escolher o Keychain sozinho, não precisa
de nada específico de macOS aqui). Dependência opcional — só é importada se
este backend for de fato usado (SO macOS).
"""

from __future__ import annotations

from apolo.platform._keyring import KeyringSecretStore as DarwinSecretStore

__all__ = ["DarwinSecretStore"]
