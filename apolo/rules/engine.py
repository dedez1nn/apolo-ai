"""Motor de regras — a cascata determinística que reduz a maior parte do ruído.

Precedência fixa; a PRIMEIRA regra que casar decide (apolo.md):
  1. allowlist        -> manter (rede de segurança contra falso positivo)
  2. blocklist        -> lixeira
  3. List-Unsubscribe -> conforme config (newsletter/marketing)
  4. keywords         -> conforme cada grupo
  5. (IA, passo 4 — não entra aqui)
  6. default          -> revisar

Regras vivem num TOML editável à mão (rules/config.toml), nunca no banco.
Lê com tomllib (stdlib, Python 3.11+); nenhuma dependência externa.
"""

import tomllib
from dataclasses import dataclass
from email.utils import parseaddr
from pathlib import Path

# Ações sugeridas possíveis.
ACAO_MANTER = "manter"
ACAO_LIXEIRA = "lixeira"
ACAO_REVISAR = "revisar"

_ACOES_VALIDAS = {ACAO_MANTER, ACAO_LIXEIRA, ACAO_REVISAR}


@dataclass(frozen=True)
class Decision:
    """Resultado da classificação de um email pela cascata."""

    categoria: str       # rótulo legível: "confiavel", "ruido", "newsletter"...
    acao_sugerida: str   # ACAO_MANTER | ACAO_LIXEIRA | ACAO_REVISAR
    regra_casada: str    # qual regra decidiu, ex.: "blocklist:dominio:loja-exemplo.com.br"

    @property
    def precisa_revisao(self) -> bool:
        """'manter' é decisão terminal; o resto vai pra fila de revisão."""
        return self.acao_sugerida != ACAO_MANTER


def _email_e_dominio(remetente: str) -> tuple[str, str]:
    """De '\"KaBuM\" <x@promo.loja-exemplo.com.br>' tira ('x@promo.loja-exemplo.com.br', 'promo.loja-exemplo.com.br')."""
    addr = parseaddr(remetente)[1].lower().strip()
    dominio = addr.rsplit("@", 1)[-1] if "@" in addr else ""
    return addr, dominio


def _casa_dominio(dominio: str, alvo: str) -> bool:
    """Casa o domínio exato ou qualquer subdomínio dele."""
    alvo = alvo.lower().strip().lstrip("@")
    return bool(dominio) and (dominio == alvo or dominio.endswith("." + alvo))


class RuleEngine:
    """Carrega o TOML e classifica emails. Reabra pra pegar edições do config."""

    def __init__(self, rules: dict):
        self._allow_remetentes = {r.lower() for r in _get_list(rules, "allowlist", "remetentes")}
        self._allow_dominios = [d.lower() for d in _get_list(rules, "allowlist", "dominios")]
        self._block_remetentes = {r.lower() for r in _get_list(rules, "blocklist", "remetentes")}
        self._block_dominios = [d.lower() for d in _get_list(rules, "blocklist", "dominios")]

        unsub = rules.get("unsubscribe", {}) or {}
        self._unsub_ativo = bool(unsub.get("ativo", False))
        self._unsub_acao = _valida_acao(unsub.get("acao", ACAO_REVISAR), "unsubscribe")

        self._keywords = []
        for grupo in rules.get("keywords", []) or []:
            self._keywords.append(
                {
                    "nome": str(grupo.get("nome", "keyword")),
                    "campo": str(grupo.get("campo", "ambos")).lower(),
                    "acao": _valida_acao(grupo.get("acao", ACAO_REVISAR), "keyword"),
                    "padroes": [str(p).lower() for p in grupo.get("padroes", [])],
                }
            )

    @classmethod
    def from_file(cls, path: Path) -> "RuleEngine":
        """Lê o TOML; se o arquivo não existir, roda com regras vazias (tudo -> revisar)."""
        path = Path(path)
        if not path.is_file():
            return cls({})
        with path.open("rb") as f:
            return cls(tomllib.load(f))

    def classify(self, *, remetente: str, assunto: str, list_unsubscribe: str = "") -> Decision:
        addr, dominio = _email_e_dominio(remetente)

        # 1. allowlist — passa sempre, nunca é tocado.
        if addr and addr in self._allow_remetentes:
            return Decision("confiavel", ACAO_MANTER, f"allowlist:remetente:{addr}")
        for d in self._allow_dominios:
            if _casa_dominio(dominio, d):
                return Decision("confiavel", ACAO_MANTER, f"allowlist:dominio:{d}")

        # 2. blocklist — ruído conhecido.
        if addr and addr in self._block_remetentes:
            return Decision("ruido", ACAO_LIXEIRA, f"blocklist:remetente:{addr}")
        for d in self._block_dominios:
            if _casa_dominio(dominio, d):
                return Decision("ruido", ACAO_LIXEIRA, f"blocklist:dominio:{d}")

        # 3. List-Unsubscribe — newsletter/marketing de graça.
        if self._unsub_ativo and list_unsubscribe.strip():
            return Decision("newsletter", self._unsub_acao, "list-unsubscribe")

        # 4. palavras-chave.
        assunto_l = (assunto or "").lower()
        addr_l = (remetente or "").lower()
        for grupo in self._keywords:
            alvo = {
                "assunto": assunto_l,
                "remetente": addr_l,
            }.get(grupo["campo"], assunto_l + " " + addr_l)
            for padrao in grupo["padroes"]:
                if padrao and padrao in alvo:
                    return Decision(grupo["nome"], grupo["acao"], f"keyword:{grupo['nome']}:{padrao}")

        # 5. (IA entra aqui no passo 4.)

        # 6. default — sem confiança, vai pra fila e não faz nada.
        return Decision("desconhecido", ACAO_REVISAR, "default")


def _get_list(rules: dict, secao: str, chave: str) -> list[str]:
    return [str(x) for x in (rules.get(secao, {}) or {}).get(chave, []) or []]


def _valida_acao(valor, origem: str) -> str:
    acao = str(valor).lower().strip()
    if acao not in _ACOES_VALIDAS:
        raise ValueError(
            f"ação inválida {valor!r} em {origem}: use {sorted(_ACOES_VALIDAS)}"
        )
    return acao
