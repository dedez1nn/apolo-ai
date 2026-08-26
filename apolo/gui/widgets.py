"""Pedaços de UI repetidos entre telas: cabeçalho, rodapé de atalhos, scaffold."""

from __future__ import annotations

import flet as ft

from apolo.gui.theme import BORDER, INK, INK_DIM, INK_FAINT, SOL, SURFACE_2


def header(titulo: str, subtitulo: str = "") -> ft.Container:
    controls = [ft.Text(titulo, size=18, weight=ft.FontWeight.W_600, color=INK)]
    if subtitulo:
        controls.append(ft.Text(subtitulo, size=12, color=INK_DIM))
    return ft.Container(
        content=ft.Column(controls, spacing=2),
        bgcolor=SURFACE_2,
        padding=ft.Padding(left=20, right=20, top=14, bottom=14),
        border=ft.Border(bottom=ft.BorderSide(width=1, color=BORDER)),
    )


def keybar(pairs: list[tuple[str, str]]) -> ft.Container:
    """pairs: [(tecla, rótulo), ...] -> rodapé fixo com os atalhos da tela."""
    chunks = []
    for tecla, rotulo in pairs:
        if chunks:
            chunks.append(ft.Text("·", color=INK_FAINT, size=12))
        chunks.append(
            ft.Row(
                [
                    ft.Container(
                        content=ft.Text(tecla, size=11, weight=ft.FontWeight.BOLD, color="#221803"),
                        bgcolor=SOL, padding=ft.Padding(left=6, right=6, top=1, bottom=1),
                        border_radius=4,
                    ),
                    ft.Text(rotulo, size=12, color=INK_DIM),
                ],
                spacing=6,
            )
        )
    # wrap=True no Row está quebrado nesta versão do Flet -- em vez de só
    # quebrar linha quando falta espaço, ele quebra em TODO chip, um por
    # linha, não importa a largura disponível (reproduzido isolado, sem
    # ligação com o resto do layout). Sem `wrap`, o Row alinha tudo numa
    # linha só (correto) -- adiciona rolagem horizontal como rede de
    # segurança pra quando a janela for estreita demais pra caber tudo.
    return ft.Container(
        content=ft.Row(chunks, spacing=10, scroll=ft.ScrollMode.AUTO),
        bgcolor=SURFACE_2,
        padding=ft.Padding(left=20, right=20, top=8, bottom=8),
        border=ft.Border(top=ft.BorderSide(width=1, color=BORDER)),
    )


def scaffold(head: ft.Control, body: ft.Control, foot: ft.Control | None = None) -> ft.Control:
    controls = [head, ft.Container(content=body, expand=True, padding=ft.Padding(left=20, right=20, top=12, bottom=12))]
    if foot is not None:
        controls.append(foot)
    # horizontal_alignment=STRETCH: sem isso os filhos (cabeçalho, corpo,
    # rodapé) encolhem pro próprio tamanho em vez de ocupar a largura toda —
    # é a mesma causa por trás da barra de atalhos quebrando um item por
    # linha quando a coluna raiz não força a largura.
    return ft.Column(controls, spacing=0, expand=True, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)


# Rótulo de tecla do Flet: estilo DOM, sem espaço ("ArrowDown", confirmado no
# docstring de KeyDownEvent do próprio pacote instalado) -- não "Arrow Down"
# como o texto solto de outras partes da documentação sugeria. Mantém a forma
# com espaço como fallback (achado testando ao vivo, não custa nada manter).
ARROW_UP = {"arrowup", "arrow up", "up"}
ARROW_DOWN = {"arrowdown", "arrow down", "down"}
ESCAPE = {"escape", "esc"}
ENTER = {"enter", "numpad enter"}
TAB = {"tab"}


def key(e: ft.KeyboardEvent) -> str:
    return (e.key or "").lower()


def rodape(*controles: ft.Control) -> ft.Column:
    """Empilha controles do rodapé (ex.: `flash()` + `keybar()`) esticados na
    largura toda — um `ft.Column` comum encolhe pro tamanho do próprio
    conteúdo (mesma causa do scaffold antes do fix), e sem isso a `keybar`
    quebra um atalho por linha em vez de ficar lado a lado."""
    return ft.Column(list(controles), spacing=0, horizontal_alignment=ft.CrossAxisAlignment.STRETCH)


def flash(ref: ft.Ref[ft.Text]) -> ft.Container:
    """Linha de mensagem transitória (feedback de ação) — atualizada via `ref.current.value = ...`."""
    return ft.Container(
        content=ft.Text("", ref=ref, size=12, color=INK),
        padding=ft.Padding(left=20, right=20, top=0, bottom=6),
    )
