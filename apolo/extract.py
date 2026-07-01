"""Extração de código/link de confirmação do corpo de um email.

O dono seleciona um email na fila ("pegar código") e a UI puxa o corpo, roda
estas funções e oferece os candidatos pra copiar. Tudo determinístico e stdlib
(`re`, `subprocess`) — nada de IA aqui: é casamento de padrão simples.

Dois tipos de candidato:
  - **código**: 6 dígitos (com ou sem separador, ex. `123-456`), 4–8 dígitos
    seguidos, ou alfanumérico em caixa-alta com ao menos um dígito (ex. `A3F9K2`).
  - **link**: URL que parece ser de confirmação (`confirm`, `verify`, `ativar`…).

A pontuação prioriza o que está perto de uma "pista" (código/verificação/OTP…)
e o formato clássico de 6 dígitos, pra subir o código certo num email cheio de
números (preços, datas, telefones).
"""

from __future__ import annotations

import re
import shutil
import subprocess
from dataclasses import dataclass

# Pistas (pt + en) que costumam aparecer perto de um código de verificação.
_CODE_HINT = re.compile(
    r"c[óo]digo|code|verifica|verify|confirm|otp|\bpin\b|token|autentica|"
    r"one[-\s]?time|acesso|access|seguran[çc]a|security|valida",
    re.I,
)

# 6 dígitos partidos por um separador: 123-456 / 123 456 / 123.456.
_CODE_SPLIT = re.compile(r"\b(\d{3}[\s.\-]\d{3})\b")
# 4–8 dígitos seguidos (não colados a outra letra/dígito/hífen).
_CODE_DIGITS = re.compile(r"(?<![\w-])(\d{4,8})(?![\w-])")
# Alfanumérico em caixa-alta, 6–8 chars (filtra depois exigindo ≥1 dígito).
_CODE_ALNUM = re.compile(r"(?<![\w-])([A-Z0-9]{6,8})(?![\w-])")

_URL = re.compile(r"https?://[^\s<>\"'\)\]}]+", re.I)
_LINK_HINT = re.compile(
    r"confirm|verif|activ|ativ|valid|reset|token|magic|login|sign[-_]?in|"
    r"auth|account|conta|senha|password|secure|seguran|unlock|email",
    re.I,
)

# Abaixo deste score um "código" é só um número solto (preço, ano, telefone).
_CODE_MIN_SCORE = 5


@dataclass
class Candidate:
    """Um candidato a copiar; `kind` é 'código' ou 'link'."""

    kind: str
    value: str
    score: int = 0


def _score_codes(text: str) -> dict[str, int]:
    scores: dict[str, int] = {}

    def consider(raw: str, pos: int, base: int) -> None:
        norm = re.sub(r"[\s.\-]", "", raw)
        if not any(c.isdigit() for c in norm):
            return
        window = text[max(0, pos - 60):pos]
        has_hint = bool(_CODE_HINT.search(window))
        # Alfanumérico é ambíguo (vira pedaço de URL/token); só com pista.
        if base == 2 and not has_hint:
            return
        s = base
        if has_hint:
            s += 12
        if len(norm) == 6:
            s += 5
        if raw != norm:  # tinha separador (123-456) — sinal forte de código
            s += 3
        if s > scores.get(norm, 0):
            scores[norm] = s

    for m in _CODE_SPLIT.finditer(text):
        consider(m.group(1), m.start(), 6)
    for m in _CODE_DIGITS.finditer(text):
        consider(m.group(1), m.start(), 4)
    for m in _CODE_ALNUM.finditer(text):
        consider(m.group(1), m.start(), 2)

    return {v: s for v, s in scores.items() if s >= _CODE_MIN_SCORE}


def _score_links(text: str) -> dict[str, int]:
    scores: dict[str, int] = {}
    for m in _URL.finditer(text):
        url = m.group(0).rstrip(".,);]}'\"")
        s = 0
        if _LINK_HINT.search(url):
            s += 10
        if _LINK_HINT.search(text[max(0, m.start() - 60):m.start()]):
            s += 4
        if s > scores.get(url, 0):
            scores[url] = s
    # Só links que parecem de confirmação — senão inundaria com tracking/unsub.
    return {u: s for u, s in scores.items() if s > 0}


def extract_candidates(text: str, *, max_each: int = 6) -> list[Candidate]:
    """Extrai códigos e links de confirmação, ordenados por confiança.

    Códigos primeiro (o alvo principal), depois links. Cada grupo limitado a
    `max_each` itens; vazio se nada casar.
    """
    if not text:
        return []

    codes = sorted(_score_codes(text).items(), key=lambda kv: -kv[1])[:max_each]
    links = sorted(_score_links(text).items(), key=lambda kv: -kv[1])[:max_each]

    out = [Candidate("código", v, s) for v, s in codes]
    out += [Candidate("link", u, s) for u, s in links]
    return out


def copy_to_clipboard(text: str) -> bool:
    """Copia `text` pro clipboard. Tenta wl-copy (Wayland), xclip, xsel.

    Devolve True se algum copiou; False se nenhum binário existe ou todos
    falharam (a UI avisa o dono).
    """
    candidatos = (
        ["wl-copy"],
        ["xclip", "-selection", "clipboard"],
        ["xsel", "-ib"],
    )
    for cmd in candidatos:
        if not shutil.which(cmd[0]):
            continue
        try:
            subprocess.run(cmd, input=text.encode(), check=True, timeout=5)
            return True
        except Exception:
            continue
    return False
