"""Motor de regras — a cascata determinística que reduz a maior parte do ruído.

Precedência fixa; a PRIMEIRA regra que casar decide (apolo.md):
  1. allowlist        -> manter (rede de segurança contra falso positivo)
  2. blocklist        -> lixeira
  3. List-Unsubscribe + termo de marketing (2 sinais) -> conforme config
  4. keywords         -> conforme cada grupo
  5. (IA, passo 4 — não entra aqui)
  6. default          -> revisar

Regras vivem num TOML editável à mão (rules/config.toml), nunca no banco.
Lê com tomllib (stdlib, Python 3.11+); nenhuma dependência externa.
"""

import tomllib
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.utils import parseaddr, parsedate_to_datetime
from pathlib import Path

# Ações sugeridas possíveis.
ACAO_MANTER = "manter"
ACAO_LIXEIRA = "lixeira"
ACAO_REVISAR = "revisar"

_ACOES_VALIDAS = {ACAO_MANTER, ACAO_LIXEIRA, ACAO_REVISAR}

# Categoria do grupo [[keywords]] "codigo_verificacao" (config.toml) — usada por
# `descartar_codigo_lido` pra saber se a categoria casada é "código de uso único".
CATEGORIA_CODIGO_VERIFICACAO = "codigo_verificacao"

# Pseudo-ação: nunca sai da cascata (TOML não pode configurar isso, por isso
# fica fora de _ACOES_VALIDAS) — é o sync/cli que a atribui quando o resíduo
# cai em 'default' e a IA está desligada. Sinaliza "ninguém decidiu nada"
# (nem regra, nem IA), diferente de 'revisar' (que sugere um sinal explícito
# — ex.: keyword configurada pra 'revisar' — pedindo confirmação humana).
ACAO_PENDENTE = "pendente"

# 2º sinal exigido pela regra do List-Unsubscribe (ver classify). Usado quando o
# TOML não traz [unsubscribe].exige — instalações antigas ganham o comportamento
# novo sem precisar editar o config.
_UNSUB_EXIGE_PADRAO = [
    "oferta", "promoção", "promocao", "desconto", "off", "cupom", "black friday",
    "newsletter", "novidades", "sale", "deal",
]


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


def parse_sender(remetente: str) -> tuple[str, str]:
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
        # Chave ausente -> default (2 sinais). Lista vazia explícita -> header sozinho.
        exige_raw = unsub.get("exige")
        if exige_raw is None:
            exige_raw = _UNSUB_EXIGE_PADRAO
        self._unsub_exige = [str(p).lower() for p in exige_raw if str(p).strip()]

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
        addr, dominio = parse_sender(remetente)

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

        assunto_l = (assunto or "").lower()
        addr_l = (remetente or "").lower()

        # 3. List-Unsubscribe — sinal de "email em massa", não de "lixo". Bulk
        # importante (banco, recibo, GitHub) também carrega o header, então ele
        # SOZINHO não decide: só vira `acao` se, além do header, casar um termo de
        # marketing (2 sinais). Sem 2º sinal, segue a cascata e acaba em 'revisar'.
        # [unsubscribe].exige = [] desliga a exigência (header sozinho volta a decidir).
        if self._unsub_ativo and list_unsubscribe.strip():
            if not self._unsub_exige:
                return Decision("newsletter", self._unsub_acao, "list-unsubscribe")
            alvo = assunto_l + " " + addr_l
            for termo in self._unsub_exige:
                if termo in alvo:
                    return Decision("newsletter", self._unsub_acao, f"list-unsubscribe+kw:{termo}")
            # header presente mas sem termo de marketing: não decide aqui.

        # 4. palavras-chave.
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


def eh_recente(data_raw: str, dias: int = 90) -> bool:
    """Header Date -> True se dentro dos últimos `dias` (padrão: 3 meses).

    Header ausente ou malformado conta como recente — melhor mandar pra IA de
    mais do que perder um email por causa de um header que não deu pra ler.
    """
    if not data_raw:
        return True
    try:
        dt = parsedate_to_datetime(data_raw)
    except (TypeError, ValueError, IndexError):
        return True
    if dt is None:
        return True
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt >= datetime.now(timezone.utc) - timedelta(days=dias)


def acao_efetiva(decisao: Decision, ai_ready: bool, recente: bool = True) -> str:
    """Ação de fato a gravar/exibir, dada se a IA vai rodar nesta passada.

    'default' + (IA desligada OU email com mais de `dias` — ver eh_recente) ->
    ninguém vai analisar isso por padrão: ACAO_PENDENTE, não ACAO_REVISAR (que
    soaria como uma decisão explícita da cascata). Emails antigos só passam
    pela IA se pedido explicitamente (ex.: "Reclassificar pendentes (IA)" no
    Hub, ou `apolo retry-ia`), que não filtra por idade. Qualquer outro caso
    segue a sugestão da cascata sem alteração — inclusive 'default' com IA
    ligada e recente, que já vai ser reclassificado logo em seguida.
    """
    if decisao.regra_casada == "default" and not (ai_ready and recente):
        return ACAO_PENDENTE
    return decisao.acao_sugerida


def descartar_codigo_lido(decisao: Decision, efetiva: str, lido: bool) -> str:
    """Código de verificação já lido não tem mais valor — vira lixeira mesmo
    quando a cascata sugeriu 'manter' (o dono já usou/viu o código antes do
    Apolo sincronizar). Chame depois de `acao_efetiva` e antes da proteção de
    favorito, pra um código favoritado ainda cair em revisão em vez de sumir.
    """
    if lido and decisao.categoria == CATEGORIA_CODIGO_VERIFICACAO and efetiva == ACAO_MANTER:
        return ACAO_LIXEIRA
    return efetiva


def _get_list(rules: dict, secao: str, chave: str) -> list[str]:
    return [str(x) for x in (rules.get(secao, {}) or {}).get(chave, []) or []]


def _valida_acao(valor, origem: str) -> str:
    acao = str(valor).lower().strip()
    if acao not in _ACOES_VALIDAS:
        raise ValueError(
            f"ação inválida {valor!r} em {origem}: use {sorted(_ACOES_VALIDAS)}"
        )
    return acao
