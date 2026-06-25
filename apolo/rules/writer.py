"""Escrita das regras no TOML — sustenta `apolo block`/`allow` e o loop da TUI.

tomllib só lê; aqui editamos o arquivo à mão preservando os comentários (o dono
edita esse arquivo). Inserimos uma linha no array certo, validamos o resultado
com tomllib e só então gravamos de forma atômica. Se algo der errado, o arquivo
original fica intacto. Tudo stdlib.
"""

import os
import re
import tomllib
from pathlib import Path

# lista lógica -> chaves do TOML
_KEYS = {
    "allowlist": {"remetente": "remetentes", "dominio": "dominios"},
    "blocklist": {"remetente": "remetentes", "dominio": "dominios"},
}


def normalize_valor(tipo: str, valor: str) -> str:
    valor = valor.strip().lower()
    if tipo == "dominio":
        valor = valor.lstrip("@")
    return valor


def detect_tipo(valor: str) -> str:
    """'x@dominio.com' -> remetente; 'dominio.com' (ou '@dominio') -> dominio."""
    v = valor.strip()
    if "@" in v and not v.startswith("@"):
        return "remetente"
    return "dominio"


def _already_present(rules_path: Path, lista: str, chave: str, valor: str) -> bool:
    if not rules_path.is_file():
        return False
    with rules_path.open("rb") as f:
        data = tomllib.load(f)
    atuais = {str(x).lower() for x in (data.get(lista, {}) or {}).get(chave, []) or []}
    return valor in atuais


def add_rule_entry(rules_path: Path, *, lista: str, tipo: str, valor: str) -> str:
    """Adiciona um remetente/domínio à allow/blocklist.

    Retorna "added" ou "exists". Levanta erro (sem tocar o arquivo) se o
    resultado não for um TOML válido.
    """
    if lista not in _KEYS:
        raise ValueError(f"lista inválida: {lista!r}")
    if tipo not in _KEYS[lista]:
        raise ValueError(f"tipo inválido: {tipo!r}")

    chave = _KEYS[lista][tipo]
    valor = normalize_valor(tipo, valor)
    if not valor:
        raise ValueError("valor vazio")

    rules_path = Path(rules_path)
    if _already_present(rules_path, lista, chave, valor):
        return "exists"

    original = rules_path.read_text(encoding="utf-8") if rules_path.is_file() else ""
    novo = _insert_into_array(original, lista, chave, valor)

    # Rede de segurança: valida antes de gravar; aborta se quebrou o TOML.
    parsed = tomllib.loads(novo)
    if valor not in {str(x).lower() for x in parsed.get(lista, {}).get(chave, [])}:
        raise RuntimeError("falha ao inserir a entrada no TOML (valor não apareceu)")

    _atomic_write(rules_path, novo)
    return "added"


def remove_rule_entry(rules_path: Path, *, lista: str, tipo: str, valor: str) -> str:
    """Remove um remetente/domínio da allow/blocklist (sustenta o undo da TUI).

    Retorna "removed" ou "absent". Valida o TOML resultante antes de gravar.
    """
    if lista not in _KEYS:
        raise ValueError(f"lista inválida: {lista!r}")
    if tipo not in _KEYS[lista]:
        raise ValueError(f"tipo inválido: {tipo!r}")

    chave = _KEYS[lista][tipo]
    valor = normalize_valor(tipo, valor)
    rules_path = Path(rules_path)
    if not _already_present(rules_path, lista, chave, valor):
        return "absent"

    original = rules_path.read_text(encoding="utf-8")
    novo = _remove_from_array(original, lista, chave, valor)

    parsed = tomllib.loads(novo)
    if valor in {str(x).lower() for x in parsed.get(lista, {}).get(chave, [])}:
        raise RuntimeError("falha ao remover a entrada do TOML (valor permaneceu)")

    _atomic_write(rules_path, novo)
    return "removed"


def _remove_from_array(text: str, lista: str, chave: str, valor: str) -> str:
    """Remove `"valor"` do array [lista].chave (inline ou multilinha)."""
    lines = text.splitlines()
    sec_idx = _find_section(lines, lista)
    if sec_idx is None:
        return text
    sec_end = _section_end(lines, sec_idx)

    key_re = re.compile(rf"^\s*{re.escape(chave)}\s*=\s*\[")
    for i in range(sec_idx + 1, sec_end):
        if not key_re.match(lines[i]):
            continue
        after = lines[i][lines[i].index("[") + 1 :]
        if "]" in after:
            # Array numa linha só.
            head = lines[i][: lines[i].index("[") + 1]
            inner, _, tail = lines[i][lines[i].index("[") + 1 :].rpartition("]")
            itens = [s.strip() for s in inner.split(",") if s.strip()]
            itens = [s for s in itens if not (s[:1] == '"' and s[1:-1].lower() == valor)]
            lines[i] = f"{head}{', '.join(itens)}]{tail}"
            return "\n".join(lines) + "\n"
        # Array multilinha: acha a linha do elemento e remove.
        for j in range(i + 1, sec_end):
            stripped = lines[j].strip()
            if stripped.startswith("]"):
                break
            m = re.match(r'^"([^"]*)"\s*,?\s*$', stripped)
            if m and m.group(1).lower() == valor:
                del lines[j]
                return "\n".join(lines) + "\n"
        break
    return "\n".join(lines) + "\n"


def _insert_into_array(text: str, lista: str, chave: str, valor: str) -> str:
    """Insere `  "valor",` no array [lista].chave, criando o que faltar."""
    entry_line = f'  "{valor}",'
    lines = text.splitlines()

    sec_idx = _find_section(lines, lista)
    if sec_idx is None:
        # Cria a seção inteira no fim.
        bloco = f"\n[{lista}]\n{chave} = [\n{entry_line}\n]\n"
        return (text.rstrip("\n") + "\n" + bloco) if text.strip() else bloco.lstrip("\n")

    # Limites da seção: do header até o próximo header de tabela (ou EOF).
    sec_end = _section_end(lines, sec_idx)

    key_re = re.compile(rf"^\s*{re.escape(chave)}\s*=\s*\[")
    for i in range(sec_idx + 1, sec_end):
        if key_re.match(lines[i]):
            return _insert_in_array_at(lines, i, sec_end, valor)

    # Seção existe mas a chave não: cria a chave logo após o header.
    lines.insert(sec_idx + 1, f"{chave} = [\n{entry_line}\n]")
    return "\n".join(lines) + "\n"


def _insert_in_array_at(lines: list[str], open_idx: int, sec_end: int, valor: str) -> str:
    """Insere o valor no array que começa em lines[open_idx]."""
    entry = f'  "{valor}",'
    open_line = lines[open_idx]
    after_bracket = open_line[open_line.index("[") + 1 :]

    if "]" in after_bracket:
        # Array numa linha só: dominios = [] ou dominios = ["a"].
        head, _, tail = open_line.rpartition("]")
        inner = head[head.index("[") + 1 :].strip()
        if inner and not inner.endswith(","):
            inner += ", "
        elif inner:
            inner += " "
        lines[open_idx] = f'{head[: head.index("[") + 1]}{inner}"{valor}"]{tail}'
        return "\n".join(lines) + "\n"

    # Array multilinha: insere logo após a linha de abertura, preservando comentários.
    lines.insert(open_idx + 1, entry)
    return "\n".join(lines) + "\n"


def _find_section(lines: list[str], lista: str) -> int | None:
    header = re.compile(rf"^\s*\[{re.escape(lista)}\]\s*$")
    for i, line in enumerate(lines):
        if header.match(line):
            return i
    return None


def _section_end(lines: list[str], sec_idx: int) -> int:
    table = re.compile(r"^\s*\[")
    for i in range(sec_idx + 1, len(lines)):
        if table.match(lines[i]):
            return i
    return len(lines)


def _atomic_write(path: Path, content: str) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(content, encoding="utf-8")
    os.replace(tmp, path)
