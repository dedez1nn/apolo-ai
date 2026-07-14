"""Pré-visualizador de email — reconstrói HTML em uma página rolável."""

from __future__ import annotations

import base64
import binascii
import contextlib
import logging
import os
import re
import struct
import tempfile
import urllib.parse
import urllib.request
from dataclasses import dataclass, field
from email.message import Message
from html.parser import HTMLParser
from pathlib import Path

from textual import work
from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Center, Vertical, VerticalScroll
from textual.screen import ModalScreen
from textual.widgets import Static
from textual_image.widget import AutoImage, TGPImage

from apolo.actions import fetch_message
from apolo.ui.model import Item, fmt_data, fmt_remetente
from apolo.ui.theme import AMBER, AZURE_BRT, COR_LIXEIRA, INK_DIM, INK_FAINT, keybar, mesc

logger = logging.getLogger("apolo.ui.email_preview")

_ImageWidget = TGPImage if ("kitty" in os.environ.get("TERM", "") or os.environ.get("KITTY_WINDOW_ID")) else AutoImage

_DROP_CONTENT = {"head", "noscript", "script", "style", "title"}
_BLOCK_TAGS = {
    "article", "aside", "blockquote", "div", "figcaption", "footer", "h1", "h2", "h3",
    "h4", "h5", "h6", "header", "li", "main", "p", "pre", "section",
}
# Tags sem fechamento correspondente — não empilham em _hidden_stack (senão
# um <br> sem barra de auto-fechamento desalinha a pilha pro resto do doc).
_VOID_TAGS = {"br", "hr", "img", "meta", "link", "input", "area", "base", "col", "embed", "source", "track", "wbr"}

# Preheader oculto: newsletters escondem um textão (geralmente preenchido com
# caracteres invisíveis tipo zero-width) atrás de `display:none`/`visibility:
# hidden` só pra controlar o preview do cliente de email. Sem isso, esse lixo
# aparecia como o primeiro "parágrafo" da prévia — a origem mais comum do
# efeito "estrofe de poema" (texto sem sentido, cheio de espaços/pontos soltos).
# Não inclui `font-size:0` — é um reset de espaçamento comuníssimo em
# templates baseados em tabela (MJML e cia) e escondê-lo apaga o corpo inteiro.
_HIDDEN_STYLE_RE = re.compile(
    r"display\s*:\s*none|visibility\s*:\s*hidden|mso-hide\s*:\s*all",
    re.IGNORECASE,
)


def _looks_hidden(attrs: dict[str, str]) -> bool:
    if "hidden" in attrs:
        return True
    if (attrs.get("aria-hidden") or "").strip().lower() == "true":
        return True
    return bool(_HIDDEN_STYLE_RE.search(attrs.get("style") or ""))


@dataclass
class _ImageAsset:
    path: Path
    label: str


@dataclass
class _PreviewBlock:
    kind: str
    text: str = ""
    variant: str = "body"
    align: str = "left"
    asset: _ImageAsset | None = None
    caption: str = ""


@dataclass
class _PreviewDoc:
    blocks: list[_PreviewBlock]
    resumo: str
    links: list[str] = field(default_factory=list)


def _decode_part(part: Message) -> str:
    payload = part.get_payload(decode=True)
    if payload is None:
        raw = part.get_payload()
        return raw if isinstance(raw, str) else ""
    charset = part.get_content_charset() or "utf-8"
    try:
        return payload.decode(charset, errors="replace")
    except LookupError:
        return payload.decode("utf-8", errors="replace")


def _normalize_cid(value: str | None) -> str:
    if not value:
        return ""
    cid = value.strip().strip("<>").strip()
    if cid.lower().startswith("cid:"):
        cid = cid[4:]
    return urllib.parse.unquote(cid)


def _safe_ext(value: str | None, default: str = "bin") -> str:
    ext = (value or default).lower().strip()
    if ext == "jpeg":
        ext = "jpg"
    ext = re.sub(r"[^a-z0-9]+", "", ext)
    return ext or default


def _validate_png(raw: bytes) -> None:
    """Valida a estrutura básica de um PNG para evitar o crash do Pillow.

    O erro visto em runtime (`SyntaxError: broken PNG file (chunk)`) acontece
    quando a imagem chega truncada/corrompida. Validamos a assinatura, o fluxo
    dos chunks, o CRC e a presença do IEND antes de montar o widget.
    """
    sig = b"\x89PNG\r\n\x1a\n"
    if not raw.startswith(sig):
        raise ValueError("assinatura PNG inválida")
    pos = len(sig)
    saw_iend = False
    while pos < len(raw):
        if pos + 8 > len(raw):
            raise ValueError("PNG truncado no cabeçalho do chunk")
        length = struct.unpack(">I", raw[pos:pos + 4])[0]
        ctype = raw[pos + 4:pos + 8]
        pos += 8
        end = pos + length
        if end + 4 > len(raw):
            raise ValueError(f"PNG truncado no chunk {ctype.decode('latin1', 'replace')}")
        data = raw[pos:end]
        crc_expected = struct.unpack(">I", raw[end:end + 4])[0]
        crc_actual = binascii.crc32(ctype)
        crc_actual = binascii.crc32(data, crc_actual) & 0xFFFFFFFF
        if crc_actual != crc_expected:
            raise ValueError(f"CRC inválido no chunk {ctype.decode('latin1', 'replace')}")
        pos = end + 4
        if ctype == b"IEND":
            saw_iend = True
            if pos != len(raw):
                raise ValueError("dados extras após IEND")
            break
    if not saw_iend:
        raise ValueError("PNG sem IEND")


def _validate_image_bytes(raw: bytes, ext: str | None = None, ctype: str | None = None) -> None:
    ext = _safe_ext(ext or "", "")
    mime = (ctype or "").lower()
    if raw.startswith(b"\x89PNG\r\n\x1a\n") or ext == "png" or mime == "image/png":
        _validate_png(raw)


def _write_image_file(asset_dir: Path, filename: str, raw: bytes, *, ext: str | None = None,
                      ctype: str | None = None) -> Path:
    _validate_image_bytes(raw, ext=ext, ctype=ctype)
    path = asset_dir / filename
    path.write_bytes(raw)
    return path


class _ImageResolver:
    def __init__(self, asset_dir: Path, cid_images: dict[str, _ImageAsset], *, external_limit: int = 6):
        self.asset_dir = asset_dir
        self.cid_images = cid_images
        self.external_limit = external_limit
        self.external_loaded = 0
        self.external_failed = 0
        self.data_loaded = 0
        self.used_cids: set[str] = set()
        self._counter = 0

    def resolve(self, src: str, alt: str = "") -> _ImageAsset | None:
        src = (src or "").strip()
        if not src:
            return None
        if src.startswith("cid:"):
            cid = _normalize_cid(src)
            asset = self.cid_images.get(cid)
            if asset is not None:
                self.used_cids.add(cid)
            return asset
        if src.startswith("data:image/"):
            asset = self._from_data_uri(src, alt)
            if asset is not None:
                self.data_loaded += 1
            return asset
        if src.startswith(("http://", "https://")):
            if self.external_loaded >= self.external_limit:
                self.external_failed += 1
                return None
            asset = self._download(src, alt)
            if asset is None:
                self.external_failed += 1
            else:
                self.external_loaded += 1
            return asset
        return None

    def _next_path(self, ext: str) -> Path:
        self._counter += 1
        return self.asset_dir / f"img-{self._counter}.{_safe_ext(ext, 'png')}"

    def _from_data_uri(self, src: str, alt: str) -> _ImageAsset | None:
        head, _, body = src.partition(",")
        mediatype = head[5:].split(";", 1)[0] or "image/png"
        ext = mediatype.split("/", 1)[-1]
        try:
            if ";base64" in head:
                raw = base64.b64decode(body, validate=False)
            else:
                raw = urllib.parse.unquote_to_bytes(body)
        except (ValueError, binascii.Error):
            return None
        try:
            path = _write_image_file(self.asset_dir, self._next_path(ext).name, raw, ext=ext, ctype=mediatype)
        except ValueError as e:
            logger.warning("prévia: imagem data URI inválida (%s): %s", alt or mediatype, e)
            return None
        return _ImageAsset(path=path, label=alt or "imagem embutida")

    def _download(self, url: str, alt: str) -> _ImageAsset | None:
        req = urllib.request.Request(url, headers={"User-Agent": "apolo-email-preview/1.0"})
        try:
            with urllib.request.urlopen(req, timeout=5) as resp:
                ctype = resp.headers.get_content_type()
                if not ctype.startswith("image/"):
                    logger.warning("prévia: URL externa não retornou imagem (%s -> %s)", url, ctype)
                    return None
                raw = resp.read(2_000_000)
        except Exception as e:
            logger.warning("prévia: falha ao baixar imagem externa (%s): %s: %s", url, type(e).__name__, e)
            return None
        ext = ctype.split("/", 1)[-1]
        try:
            path = _write_image_file(self.asset_dir, self._next_path(ext).name, raw, ext=ext, ctype=ctype)
        except ValueError as e:
            logger.warning("prévia: imagem externa inválida (%s): %s", url, e)
            return None
        host = urllib.parse.urlparse(url).netloc or "imagem externa"
        return _ImageAsset(path=path, label=alt or host)


class _HtmlToBlocks(HTMLParser):
    def __init__(self, resolver: _ImageResolver):
        super().__init__(convert_charrefs=True)
        self.resolver = resolver
        self.blocks: list[_PreviewBlock] = []
        self.links: list[str] = []
        self._skip_depth = 0
        self._hidden_stack: list[bool] = []
        self._tag_stack: list[str] = []
        self._link_stack: list[str] = []
        self._link_ref_stack: list[int | None] = []
        self._list_stack: list[dict[str, int]] = []
        self._variant = "body"
        self._align = "left"
        self._markup_parts: list[str] = []
        self._plain_parts: list[str] = []
        self._table_rows: list[tuple[list[str], bool]] | None = None
        self._table_row: list[str] | None = None
        self._table_header = False
        self._table_cell: list[str] | None = None

    def handle_starttag(self, tag: str, attrs) -> None:
        tag = tag.lower()
        attrs_map = dict(attrs)
        hidden = bool(self._hidden_stack and self._hidden_stack[-1]) or _looks_hidden(attrs_map)
        if tag not in _VOID_TAGS:
            self._hidden_stack.append(hidden)
        if hidden:
            return
        if tag in _DROP_CONTENT:
            self._skip_depth += 1
            return
        if self._skip_depth:
            return
        if self._table_rows is not None:
            self._handle_table_start(tag)
            return
        if tag == "table":
            self._flush_text()
            self._table_rows = []
            return
        if tag == "img":
            self._flush_text()
            src = attrs_map.get("src", "")
            alt = attrs_map.get("alt", "")
            asset = self.resolver.resolve(src, alt)
            caption = alt or src or "imagem"
            self.blocks.append(_PreviewBlock(kind="image", asset=asset, caption=caption))
            return
        if tag == "br":
            self._append_linebreak()
            return
        if tag == "hr":
            self._flush_text()
            self.blocks.append(_PreviewBlock(kind="divider"))
            return
        if tag in _BLOCK_TAGS:
            self._flush_text()
            self._variant = tag
            self._align = self._align_from_attrs(attrs_map)
            if tag == "li":
                prefixo = self._list_prefix()
                self._append_inline(prefixo, prefixo)
            return
        if tag in {"ol", "ul"}:
            self._flush_text()
            self._list_stack.append({"tag": tag, "index": 0})
            return
        if tag == "a":
            href = attrs_map.get("href", "")
            self._link_stack.append(href)
            self._link_ref_stack.append(self._register_link(href))
        self._tag_stack.append(tag)

    def handle_endtag(self, tag: str) -> None:
        tag = tag.lower()
        was_hidden = bool(self._hidden_stack and self._hidden_stack[-1])
        if tag not in _VOID_TAGS and self._hidden_stack:
            self._hidden_stack.pop()
        if was_hidden:
            return
        if tag in _DROP_CONTENT and self._skip_depth > 0:
            self._skip_depth -= 1
            return
        if self._skip_depth:
            return
        if self._table_rows is not None:
            self._handle_table_end(tag)
            return
        if tag == "a":
            href = self._link_stack.pop() if self._link_stack else ""
            ref = self._link_ref_stack.pop() if self._link_ref_stack else None
            if href and ref is not None and href not in "".join(self._plain_parts):
                marker = f" [{INK_FAINT} @click=screen.abrir_link({ref})]({ref})[/]"
                self._append_inline(marker, f" ({ref})")
        if tag in _BLOCK_TAGS:
            self._flush_text()
        if tag in {"ol", "ul"} and self._list_stack:
            self._flush_text()
            self._list_stack.pop()
        self._pop_tag(tag)

    def handle_data(self, data: str) -> None:
        if self._hidden_stack and self._hidden_stack[-1]:
            return
        if self._skip_depth or not data:
            return
        if self._table_cell is not None:
            text = re.sub(r"\s+", " ", data)
            if text:
                self._table_cell.append(text)
            return
        if self._table_rows is not None:
            return
        if "pre" not in self._tag_stack and self._variant != "pre":
            data = re.sub(r"\s+", " ", data)
        if not data.strip() and "\n" not in data:
            return
        self._append_inline(self._styled_text(data), data)

    def close(self) -> None:
        super().close()
        self._flush_text()
        if self._table_rows:
            self.blocks.append(_PreviewBlock(kind="text", text=self._render_table(), variant="table"))
        self._table_rows = None

    def _handle_table_start(self, tag: str) -> None:
        if tag == "tr":
            self._table_row = []
            self._table_header = False
        elif tag in {"td", "th"}:
            self._table_cell = []
            if tag == "th":
                self._table_header = True

    def _handle_table_end(self, tag: str) -> None:
        if tag in {"td", "th"} and self._table_row is not None and self._table_cell is not None:
            cell = "".join(self._table_cell).strip()
            self._table_row.append(cell)
            self._table_cell = None
            return
        if tag == "tr" and self._table_row is not None:
            if any(cell.strip() for cell in self._table_row):
                self._table_rows.append((self._table_row, self._table_header))
            self._table_row = None
            self._table_header = False
            return
        if tag == "table":
            self.blocks.append(_PreviewBlock(kind="text", text=self._render_table(), variant="table"))
            self._table_rows = None
            self._table_row = None
            self._table_header = False
            self._table_cell = None

    def _render_table(self) -> str:
        rows = self._table_rows or []
        if not rows:
            return ""
        ncols = max(len(cells) for cells, _ in rows)
        widths = []
        for col in range(ncols):
            width = max(len((cells[col] if col < len(cells) else "")) for cells, _ in rows)
            widths.append(min(max(width, 4), 28))
        linhas: list[str] = []
        for cells, is_header in rows:
            padded = []
            for col, width in enumerate(widths):
                value = (cells[col] if col < len(cells) else "")[:width]
                padded.append(f"{value:<{width}}")
            bruto = " | ".join(padded).rstrip()
            line = mesc(bruto)
            if is_header:
                linhas.append(f"[b]{line}[/]")
                linhas.append(f"[{INK_FAINT}]{'-' * min(len(bruto), 80)}[/]")
            else:
                linhas.append(line)
        return "\n".join(linhas)

    def _align_from_attrs(self, attrs: dict[str, str]) -> str:
        align = (attrs.get("align") or "").strip().lower()
        if align in {"left", "center", "right"}:
            return align
        style = (attrs.get("style") or "").lower()
        if "text-align:center" in style or "text-align: center" in style:
            return "center"
        if "text-align:right" in style or "text-align: right" in style:
            return "right"
        return "left"

    def _list_prefix(self) -> str:
        if not self._list_stack:
            return "• "
        top = self._list_stack[-1]
        if top["tag"] == "ol":
            top["index"] += 1
            return f"{top['index']}. "
        return "• "

    def _register_link(self, href: str) -> int | None:
        """Registra a URL numa lista à parte (ver `_build_preview`) em vez de
        despejá-la inline — links de rastreamento chegam a centenas de
        caracteres e, dentro do parágrafo, quebravam a frase em várias linhas
        curtas sem sentido (o efeito "poema" reportado na prévia)."""
        href = (href or "").strip()
        if not href.startswith(("http://", "https://", "mailto:")):
            return None
        if href in self.links:
            return self.links.index(href) + 1
        self.links.append(href)
        return len(self.links)

    def _append_linebreak(self) -> None:
        if not self._markup_parts:
            return
        self._markup_parts.append("\n")
        self._plain_parts.append("\n")

    def _append_inline(self, markup: str, plain: str | None = None) -> None:
        if not markup:
            return
        self._markup_parts.append(markup)
        self._plain_parts.append(markup if plain is None else plain)

    def _styled_text(self, text: str) -> str:
        escaped = mesc(text)
        styles: list[str] = []
        if self._variant in {"h1", "h2", "h3", "h4", "h5", "h6"}:
            styles.append(f"b {AZURE_BRT}")
        elif any(tag in self._tag_stack for tag in {"b", "strong"}):
            styles.append("b")
        if any(tag in self._tag_stack for tag in {"em", "i"}):
            styles.append("i")
        if any(tag in self._tag_stack for tag in {"code", "tt"}):
            styles.append(INK_FAINT)
        if self._link_stack:
            styles.extend([AZURE_BRT, "u"])
            ref = self._link_ref_stack[-1] if self._link_ref_stack else None
            if ref is not None:
                styles.append(f"@click=screen.abrir_link({ref})")
        if not styles:
            return escaped
        return "".join(f"[{style}]" for style in styles) + escaped + ("[/]" * len(styles))

    def _flush_text(self) -> None:
        plain = "".join(self._plain_parts).strip()
        if not plain:
            self._markup_parts.clear()
            self._plain_parts.clear()
            self._variant = "body"
            self._align = "left"
            return
        markup = "".join(self._markup_parts).strip()
        if self._variant == "blockquote":
            markup = "\n".join(
                f"[{AZURE_BRT}]▌[/] {line}" if line.strip() else ""
                for line in markup.splitlines()
            )
        self.blocks.append(
            _PreviewBlock(kind="text", text=markup, variant=self._variant or "body", align=self._align)
        )
        self._markup_parts.clear()
        self._plain_parts.clear()
        self._variant = "body"
        self._align = "left"

    def _pop_tag(self, tag: str) -> None:
        for idx in range(len(self._tag_stack) - 1, -1, -1):
            if self._tag_stack[idx] == tag:
                del self._tag_stack[idx]
                break


def _save_mime_image(part: Message, asset_dir: Path, index: int) -> _ImageAsset | None:
    payload = part.get_payload(decode=True)
    if not payload:
        return None
    filename = part.get_filename() or ""
    ext = Path(filename).suffix.lstrip(".") or part.get_content_subtype() or "png"
    name = f"mime-{index}.{_safe_ext(ext, 'png')}"
    try:
        path = _write_image_file(asset_dir, name, payload, ext=ext, ctype=part.get_content_type())
    except ValueError as e:
        logger.warning(
            "prévia: imagem MIME inválida (%s uid? n/a chunk): %s",
            filename or part.get("Content-ID") or name, e,
        )
        return None
    label = filename or _normalize_cid(part.get("Content-ID")) or f"imagem {index}"
    return _ImageAsset(path=path, label=label)


def _extract_parts(msg: Message, asset_dir: Path) -> tuple[str, str, dict[str, _ImageAsset], list[_ImageAsset]]:
    plain_parts: list[str] = []
    html_parts: list[str] = []
    cid_images: dict[str, _ImageAsset] = {}
    loose_images: list[_ImageAsset] = []
    img_index = 0

    if msg.is_multipart():
        parts = msg.walk()
    else:
        parts = [msg]

    for part in parts:
        if part.get_content_maintype() == "multipart":
            continue
        disposition = (part.get("Content-Disposition") or "").lower()
        ctype = part.get_content_type()
        if ctype == "text/plain" and "attachment" not in disposition:
            plain_parts.append(_decode_part(part))
            continue
        if ctype == "text/html" and "attachment" not in disposition:
            html_parts.append(_decode_part(part))
            continue
        if part.get_content_maintype() == "image":
            img_index += 1
            asset = _save_mime_image(part, asset_dir, img_index)
            if asset is None:
                continue
            cid = _normalize_cid(part.get("Content-ID"))
            if cid:
                cid_images[cid] = asset
            else:
                loose_images.append(asset)

    return "\n".join(html_parts), "\n\n".join(plain_parts), cid_images, loose_images


def _plain_blocks(text: str) -> list[_PreviewBlock]:
    texto = text.strip()
    if not texto:
        return []
    blocks: list[_PreviewBlock] = []
    for para in re.split(r"\n\s*\n", texto):
        conteudo = para.strip()
        if conteudo:
            blocks.append(_PreviewBlock(kind="text", text=mesc(conteudo), variant="body"))
    return blocks


def _build_preview(msg: Message, asset_dir: Path) -> _PreviewDoc:
    html, plain, cid_images, loose_images = _extract_parts(msg, asset_dir)
    blocks: list[_PreviewBlock]
    resumo: list[str] = []
    links: list[str] = []

    if html.strip():
        resolver = _ImageResolver(asset_dir, cid_images)
        parser = _HtmlToBlocks(resolver)
        parser.feed(html)
        parser.close()
        blocks = [b for b in parser.blocks if b.kind != "text" or b.text.strip()]
        for cid, asset in cid_images.items():
            if cid not in resolver.used_cids:
                blocks.append(_PreviewBlock(kind="image", asset=asset, caption=asset.label))
        for asset in loose_images:
            blocks.append(_PreviewBlock(kind="image", asset=asset, caption=asset.label))
        if not blocks:
            blocks = _plain_blocks(plain)
        resumo.append("HTML reconstruído")
        imagens = sum(1 for b in blocks if b.kind == "image" and b.asset is not None)
        if imagens:
            resumo.append(f"{imagens} imagem(ns)")
        if resolver.external_failed:
            resumo.append(f"{resolver.external_failed} externa(s) indisponível(is)")
        links = parser.links
        if parser.links:
            corpo = "\n".join(
                f"[{INK_FAINT} @click=screen.abrir_link({i})]({i}) {mesc(link)}[/]"
                for i, link in enumerate(parser.links, start=1)
            )
            blocks.append(_PreviewBlock(kind="divider"))
            blocks.append(
                _PreviewBlock(kind="text", text=f"[{AZURE_BRT} b]Links mencionados[/]\n{corpo}", variant="links")
            )
            resumo.append(f"{len(parser.links)} link(s)")
    else:
        blocks = _plain_blocks(plain)
        resumo.append("texto simples")
        for asset in loose_images:
            blocks.append(_PreviewBlock(kind="image", asset=asset, caption=asset.label))
    if not blocks:
        blocks = [_PreviewBlock(kind="text", text=f"[{INK_FAINT}](sem corpo renderizável)[/]")]
    return _PreviewDoc(blocks=blocks, resumo=" · ".join(resumo), links=links)


class EmailPreviewModal(ModalScreen):
    BINDINGS = [
        Binding("escape,q", "fechar", "fechar"),
        Binding("g", "abrir_origem", "abrir origem"),
    ]

    def __init__(self, item: Item):
        super().__init__()
        self._item = item
        self._tmp = tempfile.TemporaryDirectory(prefix="apolo-email-preview-")
        self._links: list[str] = []

    def compose(self) -> ComposeResult:
        with Vertical(id="mail-box"):
            yield Static(self._header_markup(), id="mail-head", markup=True)
            yield Static(f"[{INK_FAINT}]carregando email…[/]", id="mail-status", markup=True)
            with VerticalScroll(id="mail-scroll"):
                yield Vertical(Static(f"[{INK_FAINT}]buscando corpo e assets…[/]", markup=True), id="mail-page")
            yield Static(
                keybar([("Q", "Fechar"), ("G", self._origem_rotulo()), ("Mouse", "Rolar / abrir link")]),
                classes="keybar",
            )

    def on_mount(self) -> None:
        self.query_one("#mail-scroll", VerticalScroll).focus()
        self._carregar()

    def on_unmount(self) -> None:
        with contextlib.suppress(Exception):
            self._tmp.cleanup()

    def _header_markup(self) -> str:
        assunto = mesc(self._item.assunto or "(sem assunto)")
        remetente = mesc(fmt_remetente(self._item.remetente))
        data = fmt_data(self._item.data)
        meta = f"{self._item.conta}"
        if data:
            meta += f"   [{INK_FAINT}]·[/]   {data}"
        return (
            f"[{AZURE_BRT} b]Pré-visualizar email[/]\n"
            f"[b]{assunto}[/]\n"
            f"[{INK_DIM}]{remetente}[/]   [{INK_FAINT}]·[/]   [{INK_FAINT}]{meta}[/]"
        )

    @work(thread=True)
    def _carregar(self) -> None:
        try:
            msg = fetch_message(self.app.config, self._item)
            if msg is None:
                logger.warning(
                    "prévia: mensagem indisponível (%s %s uid=%s)",
                    self._item.conta, self._item.pasta, self._item.uid,
                )
                self.app.call_from_thread(self._mostrar_erro, "não consegui buscar a mensagem")
                return
            doc = _build_preview(msg, Path(self._tmp.name))
        except Exception as exc:
            logger.exception(
                "prévia: falha ao carregar (%s %s uid=%s)",
                self._item.conta, self._item.pasta, self._item.uid,
            )
            self.app.call_from_thread(self._mostrar_erro, f"{type(exc).__name__}: {exc}")
            return
        self.app.call_from_thread(self._mostrar, doc)

    def _mostrar_erro(self, err: str) -> None:
        self.query_one("#mail-status", Static).update(f"[{COR_LIXEIRA}]erro:[/] {mesc(err)}")
        page = self.query_one("#mail-page", Vertical)
        page.query("*").remove()
        page.mount(Static(f"[{COR_LIXEIRA}]falha ao montar a prévia.[/]", markup=True, classes="mail-block"))

    def _mostrar(self, doc: _PreviewDoc) -> None:
        try:
            self._links = doc.links
            self.query_one("#mail-status", Static).update(f"[{INK_FAINT}]{mesc(doc.resumo)}[/]")
            page = self.query_one("#mail-page", Vertical)
            page.query("*").remove()
            for block in doc.blocks:
                self._mount_block(page, block)
        except Exception as exc:
            logger.exception(
                "prévia: falha ao renderizar (%s %s uid=%s)",
                self._item.conta, self._item.pasta, self._item.uid,
            )
            self._mostrar_erro(f"{type(exc).__name__}: {exc}")

    def _mount_block(self, page: Vertical, block: _PreviewBlock) -> None:
        if block.kind == "divider":
            page.mount(Static(f"[{INK_FAINT}]{'─' * 72}[/]", markup=True, classes="mail-block"))
            return
        if block.kind == "image":
            if block.asset is not None:
                page.mount(Center(_ImageWidget(str(block.asset.path), classes="mail-img"), classes="mail-img-wrap"))
                if block.caption:
                    page.mount(
                        Static(
                            f"[{INK_FAINT}]{mesc(block.caption)}[/]",
                            markup=True,
                            classes="mail-img-caption",
                        )
                    )
            else:
                page.mount(
                    Static(
                        f"[{AMBER}]imagem indisponível[/]  [{INK_FAINT}]{mesc(block.caption)}[/]",
                        markup=True,
                        classes="mail-block",
                    )
                )
            return
        widget = Static(block.text, markup=True, classes=f"mail-block mail-{block.variant}")
        if block.align == "center":
            page.mount(Center(widget))
        else:
            page.mount(widget)

    def action_fechar(self) -> None:
        self.dismiss()

    def _origem_rotulo(self) -> str:
        return "Abrir no Gmail" if self._item.conta.startswith("gmail:") else "Abrir no Proton"

    def _origem_url(self) -> str | None:
        if self._item.conta.startswith("gmail:"):
            msgid = (self._item.message_id or "").strip().strip("<>")
            if not msgid:
                return None
            query = urllib.parse.quote(f"rfc822msgid:{msgid}")
            return f"https://mail.google.com/mail/u/0/#search/{query}"
        if self._item.conta == "proton":
            # O Bridge (IMAP local) não expõe o ID web da mensagem — abre a
            # caixa de entrada do Proton, não o email específico.
            return "https://mail.proton.me/u/0/inbox"
        return None

    def action_abrir_origem(self) -> None:
        url = self._origem_url()
        if url is None:
            self.query_one("#mail-status", Static).update(
                f"[{COR_LIXEIRA}]sem link direto pra essa mensagem[/]"
            )
            return
        self.app.open_url(url)

    def action_abrir_link(self, ref: int) -> None:
        """Disparado pelo `@click` embutido no markup dos links da prévia (ver
        `_HtmlToBlocks`/`_build_preview`) — clicar no texto do link ou na
        referência numerada `(n)` abre a URL de verdade no navegador."""
        if not (1 <= ref <= len(self._links)):
            return
        self.app.open_url(self._links[ref - 1])
