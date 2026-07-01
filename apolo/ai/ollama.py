"""Classificação do resíduo via Ollama local — só o que as regras não resolveram.

Conversa com o Ollama pela API HTTP (`/api/generate`) usando urllib (stdlib),
sem dependência. Manda só assunto + primeiras linhas (rápido e privado, nunca o
corpo inteiro) e pede JSON. `keep_alive` alto mantém o modelo quente na RAM
entre execuções — o custo vira inferir, não recarregar.

A IA só SUGERE: o resultado vira acao_sugerida e o email continua na fila de
revisão. Nada é apagado por conta da IA. Qualquer falha (Ollama parado, modelo
ausente, JSON inválido) é engolida e o email fica como estava (resíduo/revisar).
"""

import json
import urllib.error
import urllib.request
from dataclasses import dataclass

from apolo.rules.engine import ACAO_LIXEIRA, ACAO_MANTER, ACAO_REVISAR

_ACOES_VALIDAS = {ACAO_LIXEIRA, ACAO_MANTER, ACAO_REVISAR}

_PROMPT = """Classifique o email abaixo escolhendo UMA ação:
- lixeira: spam, promoção, marketing, newsletter, notificação automática sem valor
- manter: pessoal, trabalho, financeiro, segurança, alguém escrevendo de verdade
- revisar: qualquer dúvida

"Automático" ou "no-reply" NÃO é motivo sozinho pra lixeira: alerta de login/
segurança, aviso de conta, comunicado de escola/faculdade e recibo são
"manter" mesmo sendo automáticos. Só é lixeira se o CONTEÚDO for propaganda,
promoção ou newsletter.

Exemplos:
De: no-reply@cofre-exemplo.com | Assunto: New Device Logged In -> manter (alerta de segurança)
De: do-not-reply@portal-academico-exemplo.com | Assunto: aviso sobre nota da disciplina -> manter (comunicado acadêmico)
De: promo@loja.com | Assunto: 50% OFF só hoje -> lixeira (promoção)

Assunto: {assunto}
De: {remetente}
Trecho: {trecho}"""

_FORMAT_SCHEMA = {
    "type": "object",
    "properties": {"acao": {"type": "string", "enum": ["lixeira", "manter", "revisar"]}},
    "required": ["acao"],
}

_ACAO_CATEGORIA = {
    ACAO_LIXEIRA: "ruido",
    ACAO_MANTER: "confiavel",
    ACAO_REVISAR: "desconhecido",
}


@dataclass(frozen=True)
class AIDecision:
    categoria: str
    acao: str


class OllamaClient:
    """Cliente fino do Ollama. Tolerante a falha: nunca derruba o `run`."""

    def __init__(
        self,
        url: str,
        model: str,
        *,
        keep_alive: str = "30m",
        timeout: int = 120,
    ):
        # OLLAMA_HOST às vezes vem sem esquema (ex.: "127.0.0.1:11434").
        if not url.startswith(("http://", "https://")):
            url = "http://" + url
        self.url = url.rstrip("/")
        self.model = model
        self.keep_alive = keep_alive
        self.timeout = timeout

    def available(self) -> bool:
        """True se o daemon responde em /api/tags."""
        try:
            req = urllib.request.Request(f"{self.url}/api/tags", method="GET")
            with urllib.request.urlopen(req, timeout=5) as resp:
                return resp.status == 200
        except (urllib.error.URLError, OSError, ValueError):
            return False

    def classify(self, *, assunto: str, remetente: str, trecho: str) -> AIDecision | None:
        """Classifica um email. Retorna None em qualquer falha."""
        prompt = _PROMPT.format(
            assunto=assunto or "(sem assunto)",
            remetente=remetente or "(desconhecido)",
            trecho=trecho or "(sem corpo)",
        )
        payload = {
            "model": self.model,
            "prompt": prompt,
            "stream": False,
            "format": _FORMAT_SCHEMA,  # constrange a saída ao enum válido
            "keep_alive": self.keep_alive,
            "options": {"temperature": 0},  # determinístico
        }
        try:
            data = self._post("/api/generate", payload)
            raw = data.get("response", "")
            parsed = json.loads(raw)
        except (urllib.error.URLError, OSError, ValueError, json.JSONDecodeError):
            return None

        acao = str(parsed.get("acao", "")).lower().strip()
        if acao not in _ACOES_VALIDAS:
            # modelo pequeno às vezes coloca a ação em outro campo — varre tudo
            for v in parsed.values():
                candidate = str(v).lower().strip()
                if candidate in _ACOES_VALIDAS:
                    acao = candidate
                    break
            else:
                acao = ACAO_REVISAR
        categoria = str(parsed.get("categoria", "")).lower().strip()
        categoria = categoria or _ACAO_CATEGORIA.get(acao, "ia")
        return AIDecision(categoria=categoria, acao=acao)

    def unload(self) -> None:
        """Descarrega o modelo da RAM agora (keep_alive=0). Best-effort: falha é engolida."""
        try:
            self._post("/api/generate", {"model": self.model, "keep_alive": 0})
        except (urllib.error.URLError, OSError, ValueError):
            pass

    def _post(self, path: str, payload: dict) -> dict:
        body = json.dumps(payload).encode("utf-8")
        req = urllib.request.Request(
            f"{self.url}{path}",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))
