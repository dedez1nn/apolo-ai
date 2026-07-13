"""Modelo de um item da fila + helpers de formatação compartilhados pelas telas."""

from __future__ import annotations

from email.utils import parseaddr, parsedate_to_datetime

from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER, ACAO_PENDENTE, ACAO_REVISAR
from apolo.ui.theme import COR_LIXEIRA, COR_MANTER, COR_REVISAR, INK_FAINT

# Glyph + rótulo + cor (hex do tema) por ação. Glyphs Unicode comuns — sem
# dependência de nerd font: ● lixeira, ✓ manter, ◆ revisar, ○ pendente (a
# cascata não decidiu e a IA está desligada — ninguém analisou ainda).
ACAO_ICONE = {ACAO_LIXEIRA: "●", ACAO_MANTER: "✓", ACAO_REVISAR: "◆", ACAO_PENDENTE: "○"}
ACAO_ROTULO = {
    ACAO_LIXEIRA: "lixeira", ACAO_MANTER: "manter", ACAO_REVISAR: "revisar",
    ACAO_PENDENTE: "pendente",
}
ACAO_COR = {
    ACAO_LIXEIRA: COR_LIXEIRA, ACAO_MANTER: COR_MANTER, ACAO_REVISAR: COR_REVISAR,
    ACAO_PENDENTE: INK_FAINT,
}


def fmt_data(raw: str) -> str:
    """Header Date -> '24/06 10:03'. Tolera formato estranho (corta o cru)."""
    if not raw:
        return ""
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return raw[:16]
    return dt.strftime("%d/%m %H:%M") if dt else raw[:16]


def fmt_remetente(raw: str) -> str:
    """Header From cru -> 'dominio.com.br - "Nome Nome"' (domínio primeiro, nome depois)."""
    if not raw:
        return "(sem remetente)"
    nome, addr = parseaddr(raw)
    nome = nome.strip()
    dominio = addr.rsplit("@", 1)[-1].lower() if "@" in addr else ""
    if dominio and nome:
        return f'{dominio} - "{nome}"'
    if dominio:
        return dominio
    if nome:
        return f'"{nome}"'
    return raw.strip()


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
        # True enquanto aguarda o Ollama (sincronização ao vivo) — não é uma
        # ação de despacho, só um estado transitório de exibição.
        self.analisando = False

    @classmethod
    def from_sync(cls, s, *, acao: str) -> "Item":
        """Constrói a partir de um `SyncItem` (apolo.sync) — sem passar por `row`."""
        obj = cls.__new__(cls)
        obj.conta = s.conta
        obj.pasta = s.pasta
        obj.uidvalidity = s.uidvalidity
        obj.uid = s.uid
        obj.message_id = s.message_id
        obj.provider_id = s.provider_id
        obj.remetente = s.remetente or ""
        obj.assunto = s.assunto or ""
        obj.data = s.data or ""
        obj.regra = ""
        obj.acao = acao or ACAO_REVISAR
        obj.analisando = False
        return obj
