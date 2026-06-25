"""Gerenciador de regras — listar, remover e adicionar allow/blocklist.

A tela "Regras" do Hub: lista as entradas (navegável), remove com `x`, e abre um
modal de adicionar (`a`) com **prévia ao vivo** — enquanto você digita o valor,
mostra quais emails da fila aquela regra pegaria. Tudo offline (só edita o TOML).
"""

from __future__ import annotations

import asyncio

from textual.app import ComposeResult
from textual.binding import Binding
from textual.containers import Horizontal, Vertical, VerticalScroll
from textual.screen import ModalScreen, Screen
from textual.widgets import Button, Footer, Input, Label, ListItem, ListView, Select, Static

from apolo.rules.engine import _casa_dominio, parse_sender
from apolo.rules.writer import (
    add_rule_entry,
    detect_tipo,
    list_entries,
    normalize_valor,
    remove_rule_entry,
)

# cor + ícone por lista
_LISTA_COR = {"allowlist": "springgreen", "blocklist": "tomato"}
_LISTA_ICONE = {"allowlist": "", "blocklist": ""}
_LISTA_ACAO = {"allowlist": "manter", "blocklist": "lixeira"}


def _casa_fila(queue, tipo: str, valor: str) -> list:
    """Itens da fila que a regra (tipo/valor) pegaria — pra prévia ao vivo."""
    valor = valor.strip().lower().lstrip("@")
    if not valor:
        return []
    out = []
    for it in queue:
        addr, dom = parse_sender(it.remetente)
        if (tipo == "remetente" and addr == valor) or (tipo == "dominio" and _casa_dominio(dom, valor)):
            out.append(it)
    return out


class RuleRow(ListItem):
    def __init__(self, lista: str, tipo: str, valor: str):
        super().__init__(classes="rule-row")
        self.lista, self.tipo, self.valor = lista, tipo, valor

    def compose(self) -> ComposeResult:
        cor = _LISTA_COR.get(self.lista, "white")
        icone = _LISTA_ICONE.get(self.lista, "")
        yield Label(
            f"[b {cor}]{icone} {self.lista:<9}[/]  [dim]{self.tipo:<10}[/]  {self.valor}",
            markup=True,
        )


class RulesScreen(Screen):
    BINDINGS = [
        Binding("a", "adicionar", "adicionar"),
        Binding("e", "editar", "editar"),
        Binding("x,delete", "remover", "remover"),
        Binding("escape,q", "app.pop_screen", "voltar"),
        Binding("up,k", "cursor_up", "cima", show=False),
        Binding("down,j", "cursor_down", "baixo", show=False),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="rules"):
            yield Static(id="rules-header")
            yield ListView(id="rules-list")
        yield Static("", id="rules-msg")
        yield Footer()

    async def on_mount(self) -> None:
        await self._recarregar()

    # ----- dados -----
    def _entries(self) -> list[tuple[str, str, str]]:
        return list_entries(self.app.rules_path)

    def _atualizar_header(self, entries: list) -> None:
        if self.app.stats is not None:
            self.app.stats.rules_count = len(entries)
        n_allow = sum(1 for e in entries if e[0] == "allowlist")
        n_block = sum(1 for e in entries if e[0] == "blocklist")
        self.query_one("#rules-header", Static).update(
            "[b]  Regras[/]\n"
            f"  [springgreen] {n_allow} allowlist[/]   [tomato] {n_block} blocklist[/]"
            "    [dim]a adicionar · e editar · x remover[/]"
            + ("" if entries else "\n\n  [dim](nenhuma regra ainda — aperte 'a' pra criar)[/]")
        )

    async def _recarregar(self) -> None:
        """Reload completo da lista (usado no mount e após adicionar)."""
        entries = self._entries()
        lst = self.query_one("#rules-list", ListView)
        idx = lst.index
        await lst.clear()
        if entries:
            await lst.extend([RuleRow(*e) for e in entries])
        self._atualizar_header(entries)
        if entries:
            lst.index = min(idx, len(entries) - 1) if idx is not None else 0
            lst.focus()

    def _msg(self, texto: str) -> None:
        self.query_one("#rules-msg", Static).update(texto)

    # ----- ações -----
    def action_cursor_up(self) -> None:
        self.query_one("#rules-list", ListView).action_cursor_up()

    def action_cursor_down(self) -> None:
        self.query_one("#rules-list", ListView).action_cursor_down()

    async def action_remover(self) -> None:
        lst = self.query_one("#rules-list", ListView)
        row = lst.highlighted_child
        if not isinstance(row, RuleRow):
            self._msg("[dim]nada selecionado[/]")
            return
        try:
            status = remove_rule_entry(
                self.app.rules_path, lista=row.lista, tipo=row.tipo, valor=row.valor
            )
        except Exception as e:  # não derruba a tela
            self._msg(f"[tomato]erro ao remover: {e}[/]")
            return

        # Salva índice antes de remover; após await o DOM estará atualizado.
        idx = lst.index or 0
        await row.remove()

        entries = self._entries()
        self._atualizar_header(entries)

        if lst._nodes:
            lst.index = min(idx, len(lst._nodes) - 1)
            lst.focus()

        verbo = "removida" if status == "removed" else "não estava lá"
        self._msg(f"[tomato]✗ {row.lista}: {row.valor} {verbo}[/]")

    def action_adicionar(self) -> None:
        self.app.push_screen(AddRuleModal(), self._apos_add)

    def _apos_add(self, resultado) -> None:
        asyncio.ensure_future(self._recarregar())
        if resultado:
            lista, tipo, valor, status = resultado
            verbo = "já existia" if status == "exists" else "criada"
            self._msg(f"[{_LISTA_COR[lista]}]✓ {lista}: {tipo} {valor} {verbo}[/]")

    def action_editar(self) -> None:
        row = self.query_one("#rules-list", ListView).highlighted_child
        if not isinstance(row, RuleRow):
            self._msg("[dim]nada selecionado[/]")
            return
        self.app.push_screen(EditRuleModal(row.lista, row.tipo, row.valor), self._apos_editar)

    def _apos_editar(self, resultado) -> None:
        asyncio.ensure_future(self._recarregar())
        if resultado:
            nova_lista, novo_tipo, novo_valor, status = resultado
            verbo = "já existia" if status == "exists" else "atualizada"
            self._msg(f"[{_LISTA_COR[nova_lista]}]✎ {nova_lista}: {novo_tipo} {novo_valor} {verbo}[/]")


class AddRuleModal(ModalScreen):
    """Form de nova regra com prévia ao vivo do que casa na fila."""

    BINDINGS = [
        Binding("ctrl+s", "salvar", "salvar", priority=True),
        Binding("escape", "cancelar", "cancelar"),
    ]

    def compose(self) -> ComposeResult:
        with Vertical(id="add-box"):
            yield Static("[b]  Nova regra[/]", classes="cfg-title")
            with Horizontal(classes="cfg-row"):
                yield Label("Lista", classes="cfg-lbl")
                yield Select(
                    [("blocklist → lixeira", "blocklist"), ("allowlist → manter", "allowlist")],
                    value="blocklist", allow_blank=False, id="r-lista",
                )
            with Horizontal(classes="cfg-row"):
                yield Label("Valor", classes="cfg-lbl")
                yield Input(placeholder="dominio.com  ou  nome@dominio.com", id="r-valor")
            yield Static("", id="r-tipo")
            yield Static("", id="r-preview")
            with Horizontal(id="cfg-actions"):
                yield Button("Salvar (ctrl+s)", variant="primary", id="r-save")
                yield Button("Cancelar (esc)", id="r-cancel")

    def on_mount(self) -> None:
        self.query_one("#r-valor", Input).focus()
        self._prever()

    def on_input_changed(self, _e: Input.Changed) -> None:
        self._prever()

    def on_select_changed(self, _e: Select.Changed) -> None:
        self._prever()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "r-save":
            self.action_salvar()
        else:
            self.dismiss(None)

    def _prever(self) -> None:
        valor = self.query_one("#r-valor", Input).value.strip()
        tipo = detect_tipo(valor) if valor else "—"
        self.query_one("#r-tipo", Static).update(f"  [dim]tipo detectado:[/] [b]{tipo}[/]")
        prev = self.query_one("#r-preview", Static)
        if not valor:
            prev.update("  [dim]digite um valor pra ver o que casaria na fila…[/]")
            return
        casados = _casa_fila(self.app.queue, tipo, valor)
        if not casados:
            prev.update("  [dim]prévia: 0 na fila casariam[/]")
            return
        linhas = [f"  [b]prévia · casaria {len(casados)} na fila[/]"]
        for it in casados[:8]:
            assunto = (it.assunto or "")[:48]
            linhas.append(f"   • {it.remetente[:34]:<34} [dim]{assunto}[/]")
        if len(casados) > 8:
            linhas.append(f"   [dim]… e mais {len(casados) - 8}[/]")
        prev.update("\n".join(linhas))

    def action_salvar(self) -> None:
        valor = self.query_one("#r-valor", Input).value.strip()
        if not valor:
            self.query_one("#r-preview", Static).update("  [tomato]valor vazio — nada criado[/]")
            return
        lista = self.query_one("#r-lista", Select).value
        tipo = detect_tipo(valor)
        try:
            status = add_rule_entry(self.app.rules_path, lista=lista, tipo=tipo, valor=valor)
        except Exception as e:
            self.query_one("#r-preview", Static).update(f"  [tomato]erro: {e}[/]")
            return
        self.dismiss((lista, tipo, valor.lower().lstrip("@"), status))

    def action_cancelar(self) -> None:
        self.dismiss(None)


class EditRuleModal(ModalScreen):
    """Edita uma regra existente: troca lista, valor ou tipo."""

    BINDINGS = [
        Binding("ctrl+s", "salvar", "salvar", priority=True),
        Binding("escape", "cancelar", "cancelar"),
    ]

    def __init__(self, lista: str, tipo: str, valor: str):
        super().__init__()
        self._orig_lista = lista
        self._orig_tipo = tipo
        self._orig_valor = valor

    def compose(self) -> ComposeResult:
        with Vertical(id="add-box"):
            yield Static("[b]  Editar regra[/]", classes="cfg-title")
            with Horizontal(classes="cfg-row"):
                yield Label("Lista", classes="cfg-lbl")
                yield Select(
                    [("blocklist → lixeira", "blocklist"), ("allowlist → manter", "allowlist")],
                    value=self._orig_lista, allow_blank=False, id="r-lista",
                )
            with Horizontal(classes="cfg-row"):
                yield Label("Valor", classes="cfg-lbl")
                yield Input(value=self._orig_valor, id="r-valor")
            yield Static("", id="r-tipo")
            yield Static("", id="r-preview")
            with Horizontal(id="cfg-actions"):
                yield Button("Salvar (ctrl+s)", variant="primary", id="r-save")
                yield Button("Cancelar (esc)", id="r-cancel")

    def on_mount(self) -> None:
        inp = self.query_one("#r-valor", Input)
        inp.focus()
        inp.cursor_position = len(inp.value)
        self._prever()

    def on_input_changed(self, _e: Input.Changed) -> None:
        self._prever()

    def on_select_changed(self, _e: Select.Changed) -> None:
        self._prever()

    def on_button_pressed(self, event: Button.Pressed) -> None:
        if event.button.id == "r-save":
            self.action_salvar()
        else:
            self.dismiss(None)

    def _prever(self) -> None:
        valor = self.query_one("#r-valor", Input).value.strip()
        tipo = detect_tipo(valor) if valor else "—"
        self.query_one("#r-tipo", Static).update(f"  [dim]tipo detectado:[/] [b]{tipo}[/]")
        prev = self.query_one("#r-preview", Static)
        if not valor:
            prev.update("  [dim]digite um valor pra ver o que casaria na fila…[/]")
            return
        casados = _casa_fila(self.app.queue, tipo, valor)
        if not casados:
            prev.update("  [dim]prévia: 0 na fila casariam[/]")
            return
        linhas = [f"  [b]prévia · casaria {len(casados)} na fila[/]"]
        for it in casados[:8]:
            assunto = (it.assunto or "")[:48]
            linhas.append(f"   • {it.remetente[:34]:<34} [dim]{assunto}[/]")
        if len(casados) > 8:
            linhas.append(f"   [dim]… e mais {len(casados) - 8}[/]")
        prev.update("\n".join(linhas))

    def action_salvar(self) -> None:
        novo_valor = self.query_one("#r-valor", Input).value.strip()
        if not novo_valor:
            self.query_one("#r-preview", Static).update("  [tomato]valor vazio — nada alterado[/]")
            return
        nova_lista = self.query_one("#r-lista", Select).value
        novo_tipo = detect_tipo(novo_valor)

        orig_norm = normalize_valor(self._orig_tipo, self._orig_valor)
        novo_norm = normalize_valor(novo_tipo, novo_valor)

        if nova_lista == self._orig_lista and novo_tipo == self._orig_tipo and novo_norm == orig_norm:
            self.dismiss(None)
            return

        prev = self.query_one("#r-preview", Static)
        try:
            remove_rule_entry(
                self.app.rules_path,
                lista=self._orig_lista, tipo=self._orig_tipo, valor=self._orig_valor,
            )
        except Exception as e:
            prev.update(f"  [tomato]erro ao remover original: {e}[/]")
            return
        try:
            status = add_rule_entry(
                self.app.rules_path, lista=nova_lista, tipo=novo_tipo, valor=novo_valor,
            )
        except Exception as e:
            # Rollback: tenta restaurar o original
            try:
                add_rule_entry(
                    self.app.rules_path,
                    lista=self._orig_lista, tipo=self._orig_tipo, valor=self._orig_valor,
                )
            except Exception:
                pass
            prev.update(f"  [tomato]erro ao salvar: {e}[/]")
            return

        self.dismiss((nova_lista, novo_tipo, novo_norm, status))

    def action_cancelar(self) -> None:
        self.dismiss(None)
