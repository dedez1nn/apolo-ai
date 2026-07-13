"""Identidade visual do Apolo — tema "Liquid Glass" + helpers compartilhados.

Paleta fria (azul-ardósia) pra casar com o desktop de onde o app abre (Waybar /
Hyprland Liquid Glass). As cores semânticas das ações (lixeira/manter/revisar)
moram aqui, num lugar só, e são referenciadas tanto no TCSS (via $variáveis do
tema) quanto no markup inline das telas (via constantes hex).
"""

from __future__ import annotations

from textual.theme import Theme

# ---------- paleta ----------
BG = "#0C1118"        # meia-noite azul-ardósia (não preto puro)
SURFACE = "#121A24"   # vidro / superfície base
PANEL = "#18222F"     # painel elevado
EDGE = "#2A3949"      # hairline / borda
INK = "#DCE5EE"       # off-white frio (texto)
INK_DIM = "#8597AB"   # ardósia abafado
INK_FAINT = "#566578"  # mais fraco ainda

AZURE = "#5AA2E0"     # marca / acento / cursor
AZURE_BRT = "#85C0F0"  # azure realçado
AZURE_DEEP = "#3D7FBF"

MINT = "#54D1A0"      # manter (keep)
CORAL = "#F0705C"     # lixeira (trash)
AMBER = "#E8B85C"     # revisar (review)

# Cor por ação — usada no markup inline (hex é à prova de tema).
COR_LIXEIRA = CORAL
COR_MANTER = MINT
COR_REVISAR = AMBER

# Glyphs seguros (Unicode comum, sem depender de nerd font).
GUTTER = "▌"
DIAMOND = "◈"
MARK = "◀"
DOT = "·"


APOLO_THEME = Theme(
    name="apolo-glass",
    primary=AZURE,
    secondary=AZURE_DEEP,
    accent=AZURE_BRT,
    foreground=INK,
    background=BG,
    surface=SURFACE,
    panel=PANEL,
    success=MINT,
    warning=AMBER,
    error=CORAL,
    dark=True,
    variables={
        # cores semânticas das ações (pro TCSS: $lixeira, $manter, $revisar)
        "lixeira": CORAL,
        "manter": MINT,
        "revisar": AMBER,
        "edge": EDGE,
        "ink-dim": INK_DIM,
        "ink-faint": INK_FAINT,
        "azure-bright": AZURE_BRT,
        # realce de seleção (cursor) e foco
        "block-cursor-foreground": BG,
        "block-cursor-background": AZURE,
        "border": EDGE,
        "scrollbar": EDGE,
        "input-selection-background": f"{AZURE} 35%",
    },
)


def mesc(s: str) -> str:
    """Escapa `[` pra texto vindo de fora (assunto, remetente, erro) entrar
    seguro no markup — sem escape, um "[Tag]" vira tag de estilo (o texto
    some) e um "[/x]" estoura MarkupError. `]` solto não precisa de escape;
    escapá-lo deixaria uma `\\` literal no render.
    """
    return s.replace("[", "\\[")


def keybar(pairs: list[tuple]) -> str:
    """Markup de uma barra de atalhos: tecla em destaque + rótulo em Title Case.

    Cada par é (tecla, rótulo) ou (tecla, rótulo, cor_da_tecla). Rende algo como
    `D Lixeira  ·  M Manter  ·  ↵ Aplicar`, com a tecla colorida.
    """
    chunks = []
    for p in pairs:
        key, label = p[0], p[1]
        cor = p[2] if len(p) > 2 else AZURE_BRT
        chunks.append(f"[{cor} b]{key}[/] [{INK_DIM}]{label}[/]")
    sep = f"  [{INK_FAINT}]{DOT}[/]  "
    return "  " + sep.join(chunks)
