"""Modelo de um item da fila + helpers de formatação compartilhados pelas telas."""

from __future__ import annotations

from email.utils import parsedate_to_datetime

from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER, ACAO_REVISAR

# Ícone + rótulo + classe de cor (TCSS) por ação.
ACAO_ICONE = {ACAO_LIXEIRA: "", ACAO_MANTER: "", ACAO_REVISAR: ""}
ACAO_ROTULO = {ACAO_LIXEIRA: "lixeira", ACAO_MANTER: "manter", ACAO_REVISAR: "revisar"}
ACAO_COR = {ACAO_LIXEIRA: "tomato", ACAO_MANTER: "springgreen", ACAO_REVISAR: "gold"}


def fmt_data(raw: str) -> str:
    """Header Date -> '24/06 10:03'. Tolera formato estranho (corta o cru)."""
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return raw[:16]
    return dt.strftime("%d/%m %H:%M") if dt else raw[:16]


def fmt_run(iso: str | None) -> str:
    """ISO -> 'hoje 14:30' / '24/06 14:30'; '(nunca)' se vazio."""
    if not iso:
        return "(nunca)"
    from datetime import datetime, timezone

    try:
        dt = datetime.fromisoformat(iso).astimezone()
    except ValueError:
        return iso[:16]
    hoje = datetime.now(timezone.utc).astimezone().date()
    prefixo = "hoje" if dt.date() == hoje else dt.strftime("%d/%m")
    return f"{prefixo} {dt.strftime('%H:%M')}"


class Item:
    """Um email da fila de revisão; `acao` é a decisão atual (editável)."""

    def __init__(self, row):
        self.conta = row["conta"] if "conta" in row.keys() else "proton"
        self.pasta = row["pasta"]
        self.uidvalidity = row["uidvalidity"]
        self.uid = row["uid"]
        self.message_id = row["message_id"]
        self.provider_id = row["provider_id"] if "provider_id" in row.keys() else None
        self.remetente = row["remetente"] or ""
        self.assunto = row["assunto"] or ""
        self.data = row["data"] or ""
        self.regra = row["regra_casada"] or ""
        self.acao = row["acao_sugerida"] or ACAO_REVISAR
