"""Limpeza de corpo de email — HTML/CSS para texto legível.

Passo 1.5: muitos emails são text/html puro (marketing), recheados de tags e
CSS. Antes de mandar pra IA (passo 4) ou casar palavras-chave (passo 2),
extraímos só o texto. Tudo stdlib (html.parser); nenhuma dependência.

Decisão de design: NÃO removemos stop words. Stop word removal ajuda modelos
clássicos (bag-of-words/TF-IDF), mas atrapalha um LLM — ele depende da
linguagem natural pra entender contexto, e "não é cobrança" não pode virar
"cobrança". O texto segue como linguagem natural, só limpo e truncado.
"""

import re
from email.message import Message
from html.parser import HTMLParser

# Conteúdo destas tags é descartado por inteiro (inclui o CSS dentro de <style>).
_DROP_CONTENT = {"script", "style", "head", "title", "noscript"}

# Tags de bloco viram quebra de linha pra preservar a estrutura do texto.
_BLOCK = {
    "p", "br", "div", "tr", "li", "ul", "ol", "table", "blockquote",
    "section", "article", "header", "footer", "hr", "h1", "h2", "h3",
    "h4", "h5", "h6", "pre",
}


class _TextExtractor(HTMLParser):
    """Extrai texto visível de HTML, descartando tags, atributos e CSS."""

    def __init__(self) -> None:
        # convert_charrefs=True já transforma &amp;, &nbsp; etc. em texto.
        super().__init__(convert_charrefs=True)
        self._parts: list[str] = []
        self._skip_depth = 0  # profundidade dentro de tags de conteúdo descartável

    def handle_starttag(self, tag: str, attrs) -> None:
        if tag in _DROP_CONTENT:
            self._skip_depth += 1
        elif tag in _BLOCK:
            self._parts.append("\n")

    def handle_startendtag(self, tag: str, attrs) -> None:
        # Tags auto-fechadas (<br/>, <hr/>) não passam por handle_endtag.
        if tag in _BLOCK:
            self._parts.append("\n")

    def handle_endtag(self, tag: str) -> None:
        if tag in _DROP_CONTENT and self._skip_depth > 0:
            self._skip_depth -= 1
        elif tag in _BLOCK:
            self._parts.append("\n")

    def handle_data(self, data: str) -> None:
        if self._skip_depth == 0:
            self._parts.append(data)

    def get_text(self) -> str:
        return "".join(self._parts)


def normalize_whitespace(text: str) -> str:
    """Colapsa espaços e linhas em branco, tira espaço nas pontas das linhas."""
    # Espaços/tabs repetidos -> um espaço.
    text = re.sub(r"[ \t ]+", " ", text)
    # Tira espaço no começo/fim de cada linha.
    text = "\n".join(line.strip() for line in text.splitlines())
    # 3+ quebras de linha -> no máximo 2 (separação de parágrafo).
    text = re.sub(r"\n{3,}", "\n\n", text)
    return text.strip()


def strip_html(html: str) -> str:
    """HTML -> texto limpo. Remove tags, atributos (CSS inline) e <style>/<script>."""
    parser = _TextExtractor()
    parser.feed(html)
    parser.close()
    return normalize_whitespace(parser.get_text())


def _looks_like_html(text: str) -> bool:
    return bool(re.search(r"<\s*(html|body|div|table|p|br|a|span)\b", text, re.I))


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        return ""
    charset = part.get_content_charset() or "utf-8"
    return payload.decode(charset, errors="replace")


def message_to_text(msg: Message) -> str:
    """Extrai o melhor texto de um email.

    Prefere a parte text/plain; se só houver text/html, limpa o HTML. Ignora
    anexos (Content-Disposition: attachment).
    """
    plain_parts: list[str] = []
    html_parts: list[str] = []

    if msg.is_multipart():
        for part in msg.walk():
            if part.get_content_maintype() == "multipart":
                continue
            disposition = (part.get("Content-Disposition") or "").lower()
            if "attachment" in disposition:
                continue
            ctype = part.get_content_type()
            if ctype == "text/plain":
                plain_parts.append(_decode_part(part))
            elif ctype == "text/html":
                html_parts.append(_decode_part(part))
    else:
        body = _decode_part(msg)
        if msg.get_content_type() == "text/html":
            html_parts.append(body)
        else:
            plain_parts.append(body)

    if plain_parts:
        text = "\n".join(plain_parts)
        # Alguns clientes mandam HTML rotulado como text/plain.
        if _looks_like_html(text):
            text = strip_html(text)
        return normalize_whitespace(text)

    if html_parts:
        return strip_html("\n".join(html_parts))

    return ""


def clean_for_classification(text: str, *, max_lines: int = 20, max_chars: int = 1500) -> str:
    """Prepara o texto pro classificador: limpo, truncado em linhas e caracteres.

    A arquitetura manda só assunto + primeiras linhas pra IA — rápido e privado.
    """
    text = normalize_whitespace(text)
    lines = [ln for ln in text.splitlines() if ln.strip()]
    text = "\n".join(lines[:max_lines])
    if len(text) > max_chars:
        text = text[:max_chars].rstrip() + "…"
    return text
