"""Camada 2 pós-IA — score determinístico pra pegar falso negativo do modelo pequeno.

Quando o Ollama sugere 'lixeira', essa camada soma pontos de termos batidos no
assunto/remetente/trecho pra três categorias sensíveis (segurança, bancário,
faculdade) e, se o score passar do limiar da categoria, rebaixa a sugestão de
'lixeira' pra 'revisar' — não promove direto pra 'manter': é rede de segurança
conservadora, ainda exige revisão manual antes de confiar de vez.

Os termos vivem no rules/config.toml (seção `[verify.<categoria>]`), separados
por idioma (termos_pt / termos_en / termos_ambos) porque o mesmo termo pesa
diferente dependendo do idioma do email — por isso detectamos PT ou EN antes
de pontuar (heurístico simples por contagem de palavras funcionais, sem lib
externa). Se não der pra identificar o idioma com confiança (empate, incluindo
0 a 0), a verificação não roda: sem saber qual lista aplicar, a sugestão
original do Ollama (lixeira) fica como está — "lixeira certeira", sem sinal
pra questionar.

Ponto único que os 6 lugares que chamavam `ollama.classify` direto (cli.py e
sync.py) agora chamam: `apply_ia_decision`. A lógica da camada 2 mora só aqui.
"""

from __future__ import annotations

import re
import tomllib
from dataclasses import dataclass, field
from pathlib import Path

from apolo.ai.ollama import AIDecision, OllamaClient
from apolo.rules.engine import ACAO_LIXEIRA, ACAO_REVISAR

_PALAVRA = re.compile(r"[a-zà-öø-ÿ]+")

# Palavras funcionais (não substantivos do domínio) usadas só pra decidir se o
# texto é PT ou EN — contagem simples, o idioma com mais acertos vence; empate
# (incluindo 0 a 0) vira "não identificado".
_MARCADORES_PT = {
    "de", "da", "do", "das", "dos", "para", "não", "com", "uma", "que", "em",
    "no", "na", "seu", "sua", "você", "está", "são", "foi", "mais", "muito",
    "também", "já", "isso", "essa", "esse", "pelo", "pela", "ou",
}
_MARCADORES_EN = {
    "the", "and", "you", "your", "is", "was", "are", "were", "with", "for",
    "this", "that", "have", "has", "from", "will", "would", "been", "not",
    "but", "please", "on", "of",
}


@dataclass(frozen=True)
class CategoriaVerify:
    nome: str
    limiar: int
    termos_pt: dict[str, int]
    termos_en: dict[str, int]
    termos_ambos: dict[str, int]


@dataclass(frozen=True)
class VerifyConfig:
    categorias: tuple[CategoriaVerify, ...] = field(default_factory=tuple)

    @classmethod
    def from_file(cls, path: Path) -> "VerifyConfig":
        """Lê `[verify.<categoria>]` do TOML.

        Arquivo ausente ou sem seção `verify` -> sem categorias (a camada vira
        no-op: `verify()` sempre devolve a decisão do Ollama como veio).
        """
        path = Path(path)
        if not path.is_file():
            return cls()
        with path.open("rb") as f:
            rules = tomllib.load(f)
        verify_raw = rules.get("verify", {}) or {}
        categorias = tuple(
            CategoriaVerify(
                nome=str(nome),
                limiar=int(dados.get("limiar", 5)),
                termos_pt=_termos(dados, "termos_pt"),
                termos_en=_termos(dados, "termos_en"),
                termos_ambos=_termos(dados, "termos_ambos"),
            )
            for nome, dados in verify_raw.items()
        )
        return cls(categorias)


def _termos(dados: dict, chave: str) -> dict[str, int]:
    return {str(k).lower(): int(v) for k, v in (dados.get(chave) or {}).items()}


def _detect_language(texto: str) -> str | None:
    """PT, EN ou None (sem sinal suficiente pra distinguir)."""
    palavras = _PALAVRA.findall(texto.lower())
    pt = sum(1 for p in palavras if p in _MARCADORES_PT)
    en = sum(1 for p in palavras if p in _MARCADORES_EN)
    if pt == en:
        return None
    return "pt" if pt > en else "en"


def _score(categoria: CategoriaVerify, texto: str, idioma: str) -> int:
    total = sum(peso for termo, peso in categoria.termos_ambos.items() if termo in texto)
    especificos = categoria.termos_pt if idioma == "pt" else categoria.termos_en
    total += sum(peso for termo, peso in especificos.items() if termo in texto)
    return total


def verify(config: VerifyConfig, decisao: AIDecision, *, assunto: str, remetente: str, trecho: str) -> AIDecision:
    """Roda só quando o Ollama sugeriu lixeira; senão devolve a decisão como veio."""
    if decisao.acao != ACAO_LIXEIRA or not config.categorias:
        return decisao

    idioma = _detect_language(f"{assunto} {trecho}")
    if idioma is None:
        return decisao  # idioma não identificado: lixeira segue "certeira"

    texto = f"{assunto} {remetente} {trecho}".lower()
    for categoria in config.categorias:
        if _score(categoria, texto, idioma) >= categoria.limiar:
            return AIDecision(categoria=f"{decisao.categoria}→verify:{categoria.nome}", acao=ACAO_REVISAR)
    return decisao


def apply_ia_decision(
    ollama: OllamaClient,
    verify_config: VerifyConfig,
    *,
    assunto: str,
    remetente: str,
    trecho: str,
) -> AIDecision | None:
    """Chama o Ollama e, se ele sugeriu lixeira, roda a verificação por cima."""
    decisao = ollama.classify(assunto=assunto, remetente=remetente, trecho=trecho)
    if decisao is None:
        return None
    return verify(verify_config, decisao, assunto=assunto, remetente=remetente, trecho=trecho)
