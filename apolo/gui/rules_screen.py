"""Gerenciador de regras: listar, remover e adicionar/editar allow/blocklist.

Lista navegável (↑↓), remove com X, adiciona/edita com A/E num modal com
prévia ao vivo (quantos emails da fila aquela regra pegaria).
"""

from __future__ import annotations

import asyncio

import flet as ft

from apolo.gui.theme import COR_LIXEIRA, COR_MANTER, INK, INK_DIM, INK_FAINT, SOL, SURFACE
from apolo.gui.widgets import ARROW_DOWN, ARROW_UP, ESCAPE, flash, header, key, keybar, rodape, scaffold

_LISTA_COR = {"allowlist": COR_MANTER, "blocklist": COR_LIXEIRA}
_LISTA_ICONE = {"allowlist": "✓", "blocklist": "●"}


def _casa_fila(queue, tipo: str, valor: str) -> list:
    from apolo.rules.engine import _casa_dominio, parse_sender

    valor = valor.strip().lower().lstrip("@")
    if not valor:
        return []
    out = []
    for it in queue:
        addr, dom = parse_sender(it.remetente)
        if (tipo == "remetente" and addr == valor) or (tipo == "dominio" and _casa_dominio(dom, valor)):
            out.append(it)
    return out


class RulesScreen:
    def __init__(self) -> None:
        self.idx = 0
        self.on_close = None
        self._entries: list[tuple[str, str, str]] = []
        self._rows: list[ft.GestureDetector] = []
        self._msg_ref: ft.Ref[ft.Text] = ft.Ref()
        self._header_ref: ft.Ref[ft.Text] = ft.Ref()
        self._list_col = ft.Column(
            spacing=4, scroll=ft.ScrollMode.AUTO, expand=True,
            horizontal_alignment=ft.CrossAxisAlignment.STRETCH,
        )
        self._mounted = False

    def build(self) -> ft.Control:
        self._recarregar()
        self._mounted = True
        return scaffold(
            header("Regras"),
            ft.Column(
                [
                    ft.Text("", ref=self._header_ref, size=13, color=INK_DIM),
                    self._list_col,
                ],
                spacing=10, expand=True,
            ),
            rodape(
                flash(self._msg_ref),
                keybar([("A", "Adicionar"), ("E", "Editar"), ("X", "Remover"), ("Esc", "Voltar")]),
            ),
        )

    # ----- dados -----
    def _recarregar(self) -> None:
        from apolo.rules.writer import list_entries

        self._entries = list_entries(self.app.rules_path)
        if self.app.stats is not None:
            self.app.stats.rules_count = len(self._entries)
        self.idx = min(self.idx, max(0, len(self._entries) - 1))
        self._render_lista()

    def _render_lista(self) -> None:
        n_allow = sum(1 for e in self._entries if e[0] == "allowlist")
        n_block = sum(1 for e in self._entries if e[0] == "blocklist")
        cab = f"{n_allow} allowlist   ·   {n_block} blocklist"
        if not self._entries:
            cab += "   ·   nenhuma regra ainda, A pra criar a primeira"
        if self._header_ref.current:
            self._header_ref.current.value = cab
            if self._mounted:
                self._header_ref.current.update()

        self._rows = []
        controls = []
        for i, (lista, tipo, valor) in enumerate(self._entries):
            row = self._row(i, lista, tipo, valor)
            self._rows.append(row)
            controls.append(row)
        self._list_col.controls = controls
        if self._mounted:
            self._list_col.update()

    def _row(self, i: int, lista: str, tipo: str, valor: str) -> ft.GestureDetector:
        selecionado = i == self.idx
        cor = _LISTA_COR.get(lista, SOL)
        icone = _LISTA_ICONE.get(lista, "·")
        caixa = ft.Container(
            content=ft.Row(
                [
                    ft.Text(icone, color=cor, weight=ft.FontWeight.BOLD),
                    ft.Text(lista, size=13, color=cor, width=90),
                    ft.Text(tipo, size=12, color=INK_FAINT, width=90),
                    ft.Text(
                        valor, size=13, color="#FFFFFF" if selecionado else INK,
                        no_wrap=True, overflow=ft.TextOverflow.ELLIPSIS, expand=True,
                    ),
                ],
                spacing=10,
            ),
            bgcolor=SOL if selecionado else SURFACE,
            padding=ft.Padding(left=12, right=12, top=8, bottom=8),
            border_radius=6,
        )
        # GestureDetector em vez de Container(ink=True, on_click=...): ver
        # nota equivalente em queue.py/hub.py (evita a linha disputar foco
        # de teclado nativo do Flutter com a navegação manual por seta).
        return ft.GestureDetector(content=caixa, on_tap=lambda e, idx=i: self._selecionar(idx))

    def _selecionar(self, idx: int) -> None:
        self.idx = idx
        self._render_lista()

    def _msg(self, texto: str) -> None:
        if self._msg_ref.current:
            self._msg_ref.current.value = texto
            self._msg_ref.current.update()

    # ----- teclado -----
    def on_key(self, e: ft.KeyboardEvent) -> None:
        k = key(e)
        if k in ARROW_UP:
            self._mover(-1)
        elif k in ARROW_DOWN:
            self._mover(1)
        elif k == "a":
            self._abrir_form(None)
        elif k == "e":
            self._abrir_form(self._atual())
        elif k in ("x", "delete"):
            self._remover()
        elif k in ESCAPE or k == "q":
            (self.on_close or self.app.pop_screen)()

    def _atual(self) -> tuple[str, str, str] | None:
        if 0 <= self.idx < len(self._entries):
            return self._entries[self.idx]
        return None

    def _mover(self, delta: int) -> None:
        if not self._entries:
            return
        self.idx = (self.idx + delta) % len(self._entries)
        self._render_lista()

    def _remover(self) -> None:
        entry = self._atual()
        if entry is None:
            self._msg("nada selecionado")
            return
        lista, tipo, valor = entry
        from apolo.rules.writer import remove_rule_entry

        try:
            status = remove_rule_entry(self.app.rules_path, lista=lista, tipo=tipo, valor=valor)
        except Exception as e:
            self._msg(f"erro ao remover: {e}")
            return
        self._recarregar()
        verbo = "removida" if status == "removed" else "não estava lá"
        self._msg(f"✗ {lista}: {valor} {verbo}")

    def _abrir_form(self, original: tuple[str, str, str] | None) -> None:
        async def _run() -> None:
            resultado = await RuleFormModal(self.app, original).ask()
            self._recarregar()
            if resultado:
                lista, tipo, valor, status = resultado
                verbo = "já existia" if status == "exists" else "salva"
                self._msg(f"✓ {lista}: {tipo} {valor} {verbo}")

        self.app.page.run_task(_run)


class RuleFormModal:
    """Nova regra (`original=None`) ou edição (`original=(lista, tipo, valor)`), com prévia ao vivo."""

    def __init__(self, app, original: tuple[str, str, str] | None = None):
        self.app = app
        self.original = original
        self._future: asyncio.Future | None = None
        self._shown = False

        lista0 = original[0] if original else "blocklist"
        valor0 = original[2] if original else ""

        self.lista_field = ft.Dropdown(
            label="Lista",
            options=[
                ft.DropdownOption(key="blocklist", text="blocklist → lixeira"),
                ft.DropdownOption(key="allowlist", text="allowlist → manter"),
            ],
            value=lista0, on_select=self._mudou,
        )
        self.valor_field = ft.TextField(
            label="Valor", hint_text="dominio.com  ou  nome@dominio.com",
            value=valor0, autofocus=True, on_change=self._mudou, color=INK,
        )
        self.tipo_text = ft.Text("", size=12, color=INK_DIM)
        self.preview_text = ft.Text("", size=12, color=INK_DIM)
        self._atualizar_preview()

        titulo = "Editar regra" if original else "Nova regra"
        self.dialog = ft.AlertDialog(
            modal=True,
            title=ft.Text(titulo, color=INK, weight=ft.FontWeight.BOLD),
            content=ft.Column(
                [self.lista_field, self.valor_field, self.tipo_text, self.preview_text],
                tight=True, spacing=10, width=420,
            ),
            actions=[
                ft.TextButton("Cancelar (Esc)", on_click=lambda e: self._resolve(None)),
                ft.FilledButton("Salvar (Ctrl+S)", on_click=lambda e: self._salvar()),
            ],
        )

    def _mudou(self, e: ft.ControlEvent) -> None:
        self._atualizar_preview()

    def _atualizar_preview(self) -> None:
        from apolo.rules.writer import detect_tipo

        valor = (self.valor_field.value or "").strip()
        tipo = detect_tipo(valor) if valor else "-"
        self.tipo_text.value = f"tipo detectado: {tipo}"
        if not valor:
            self.preview_text.value = "digite um valor para ver o que casaria na fila…"
        else:
            casados = _casa_fila(self.app.queue, tipo, valor)
            self.preview_text.value = f"prévia: {len(casados)} na fila casaria(m)" if casados else "prévia: 0 na fila casariam"
        if self._shown:
            self.tipo_text.update()
            self.preview_text.update()

    def _salvar(self) -> None:
        from apolo.rules.writer import add_rule_entry, detect_tipo, normalize_valor, remove_rule_entry

        valor = (self.valor_field.value or "").strip()
        if not valor:
            self.preview_text.value = "valor vazio, nada criado"
            self.preview_text.update()
            return
        lista = self.lista_field.value
        tipo = detect_tipo(valor)

        if self.original:
            orig_lista, orig_tipo, orig_valor = self.original
            if (
                lista == orig_lista
                and tipo == orig_tipo
                and normalize_valor(tipo, valor) == normalize_valor(orig_tipo, orig_valor)
            ):
                self._resolve(None)
                return
            try:
                remove_rule_entry(self.app.rules_path, lista=orig_lista, tipo=orig_tipo, valor=orig_valor)
            except Exception as e:
                self.preview_text.value = f"erro ao remover original: {e}"
                self.preview_text.update()
                return

        try:
            status = add_rule_entry(self.app.rules_path, lista=lista, tipo=tipo, valor=valor)
        except Exception as e:
            if self.original:
                orig_lista, orig_tipo, orig_valor = self.original
                try:
                    add_rule_entry(self.app.rules_path, lista=orig_lista, tipo=orig_tipo, valor=orig_valor)
                except Exception:
                    pass
            self.preview_text.value = f"erro ao salvar: {e}"
            self.preview_text.update()
            return
        self._resolve((lista, tipo, valor.lower().lstrip("@"), status))

    def _resolve(self, result) -> None:
        if self._future is not None and not self._future.done():
            self._future.set_result(result)
        self.app.close_dialog()

    def on_key(self, e: ft.KeyboardEvent) -> None:
        k = key(e)
        if k in ESCAPE:
            self._resolve(None)
        elif e.ctrl and k == "s":
            self._salvar()

    async def ask(self):
        self._future = asyncio.get_running_loop().create_future()
        self.app.open_dialog(self.dialog, key_handler=self.on_key)
        self._shown = True
        return await self._future
