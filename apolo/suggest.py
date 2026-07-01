"""Sugestões determinísticas de regra a partir do histórico de despacho.

Nunca aplica nada sozinho — só olha pro que o dono já decidiu (`emails.acao_aplicada`)
e propõe promover um padrão consistente (domínio, ou domínio + mesmo assunto
recorrente) a regra permanente de allowlist/blocklist. Quem decide é a tela
(apolo/ui/suggest_screen.py); este módulo só gera candidatas.

MIN_AMOSTRA e LIMIAR_SKEW são um primeiro palpite — ajustar olhando casos reais,
mesmo espírito do disclaimer em apolo/verify.py.
"""

from __future__ import annotations

from dataclasses import dataclass
from datetime import datetime
from pathlib import Path

from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER, _casa_dominio, parse_sender
from apolo.rules.writer import list_entries

MIN_AMOSTRA = 3  # abaixo disso é ruído estatístico, não hábito
LIMIAR_SKEW = 0.8  # pelo menos 80% das ocorrências do grupo concordam na mesma ação


@dataclass(frozen=True)
class Sugestao:
    chave: str
    tipo: str  # "dominio" | "dominio_assunto"
    dominio: str
    assunto: str | None
    acao: str  # ACAO_LIXEIRA | ACAO_MANTER
    total: int
    concordantes: int
    frequencia: str
    exemplos: tuple[str, ...]


def _norm_assunto(assunto: str | None) -> str:
    return " ".join((assunto or "").split()).strip().lower()


def _parse_iso(raw: str) -> datetime | None:
    try:
        return datetime.fromisoformat(raw)
    except (TypeError, ValueError):
        return None


def _frequencia(datas_iso: list[str]) -> str:
    datas = sorted(d for d in (_parse_iso(x) for x in datas_iso) if d is not None)
    if len(datas) < 2:
        return "amostra única" if datas else ""
    span_dias = max((datas[-1] - datas[0]).total_seconds() / 86400, 1 / 24)
    total = len(datas)
    if span_dias <= 1:
        return f"{total}x num só dia"
    por_semana = total / (span_dias / 7)
    if por_semana >= 1:
        return f"~{por_semana:.1f}x/semana"
    por_mes = total / (span_dias / 30)
    return f"~{por_mes:.1f}x/mês"


def _acao_predominante(rows: list) -> tuple[str, int, int]:
    total = len(rows)
    n_lixeira = sum(1 for r in rows if r["acao_aplicada"] == ACAO_LIXEIRA)
    n_manter = total - n_lixeira
    if n_lixeira >= n_manter:
        return ACAO_LIXEIRA, n_lixeira, total
    return ACAO_MANTER, n_manter, total


def gerar_sugestoes(rows, *, rules_path: Path, ignoradas: set[str]) -> list[Sugestao]:
    """Agrupa o histórico despachado por domínio e por (domínio, assunto) e
    devolve as combinações consistentes o bastante pra virar regra.
    """
    por_dominio: dict[str, list] = {}
    por_dominio_assunto: dict[tuple[str, str], list] = {}

    for row in rows:
        _, dominio = parse_sender(row["remetente"] or "")
        if not dominio:
            continue
        por_dominio.setdefault(dominio, []).append(row)
        assunto_norm = _norm_assunto(row["assunto"])
        if assunto_norm:
            por_dominio_assunto.setdefault((dominio, assunto_norm), []).append(row)

    entries = list_entries(rules_path)
    dominios_com_regra = {valor for _, tipo, valor in entries if tipo == "dominio"}

    def _ja_coberto(dominio: str) -> bool:
        return any(_casa_dominio(dominio, d) for d in dominios_com_regra)

    def _construir(grupo: list, tipo: str, dominio: str, assunto: str | None) -> Sugestao | None:
        acao, concordantes, total = _acao_predominante(grupo)
        if total < MIN_AMOSTRA or concordantes / total < LIMIAR_SKEW:
            return None
        if _ja_coberto(dominio):
            return None
        chave = f"{tipo}|{dominio}|{assunto or ''}|{acao}"
        if chave in ignoradas:
            return None
        datas = [r["processado_em"] for r in grupo if r["processado_em"]]
        exemplos = tuple(sorted({r["assunto"] for r in grupo if r["assunto"]}))[:3]
        return Sugestao(chave, tipo, dominio, assunto, acao, total, concordantes, _frequencia(datas), exemplos)

    sugestoes_dom_assunto = []
    for (dominio, assunto), grupo in por_dominio_assunto.items():
        sug = _construir(grupo, "dominio_assunto", dominio, assunto)
        if sug is not None:
            sugestoes_dom_assunto.append(sug)

    dominios_reforcados = {s.dominio for s in sugestoes_dom_assunto}
    sugestoes_dominio = []
    for dominio, grupo in por_dominio.items():
        if dominio in dominios_reforcados:
            continue
        sug = _construir(grupo, "dominio", dominio, None)
        if sug is not None:
            sugestoes_dominio.append(sug)

    todas = sugestoes_dom_assunto + sugestoes_dominio
    todas.sort(key=lambda s: s.total, reverse=True)
    return todas
