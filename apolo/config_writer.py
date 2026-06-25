"""Escrita parcial e atômica do .env — usada pela tela de Configurações da UI.

Atualiza só as chaves pedidas, preservando todo o resto: comentários, ordem e —
crucialmente — as credenciais do Bridge. Nunca reescreve o arquivo do zero.
Tudo stdlib.
"""

from __future__ import annotations

import os
from pathlib import Path


def env_path() -> Path:
    """O .env mora na raiz do projeto (apolo/config_writer.py -> ../.env)."""
    return Path(__file__).resolve().parent.parent / ".env"


def set_env_values(path: Path, updates: dict[str, str]) -> None:
    """Grava KEY=VALUE pra cada chave de `updates`, mantendo o restante do arquivo.

    Chave existente é substituída no lugar; chave nova é anexada ao fim.
    """
    path = Path(path)
    linhas = path.read_text(encoding="utf-8").splitlines() if path.is_file() else []
    pendentes = dict(updates)
    saida: list[str] = []
    for raw in linhas:
        stripped = raw.strip()
        if stripped and not stripped.startswith("#") and "=" in stripped:
            chave = stripped.split("=", 1)[0].strip()
            if chave in pendentes:
                saida.append(f"{chave}={pendentes.pop(chave)}")
                continue
        saida.append(raw)
    for chave, valor in pendentes.items():
        saida.append(f"{chave}={valor}")

    conteudo = "\n".join(saida) + "\n"
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(conteudo, encoding="utf-8")
    os.replace(tmp, path)
