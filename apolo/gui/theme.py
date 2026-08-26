"""Identidade visual do Apolo — sol, louro, mármore, noite egeia.

Paleta tirada do próprio motivo do nome (ver README, "Por que Apolo?"): sol
(luz/esclarecimento) pra ênfase e ação primária, louro pra "manter", terracota
queimada pra "lixeira". A janela em si assume um único mundo visual, escuro
(ver docs/ui.md) — sem tema claro pro app; cor chapada, sem gradiente.
"""

from __future__ import annotations

# ---------- paleta ----------
BG = "#14161C"          # noite egeia — fundo da janela
SURFACE = "#1B1E27"      # cartão/linha
SURFACE_2 = "#20232D"    # nav/cabeçalho
BORDER = "#2A2D38"

INK = "#ECE7D8"          # texto principal (parchment)
INK_DIM = "#B4AF9C"
INK_FAINT = "#928D78"    # texto fraco

SOL = "#D9A441"          # ouro — ênfase, ação primária
SOL_INK = "#221803"      # texto sobre botão dourado
LOURO = "#7EA25C"        # manter
TERRACOTA = "#C06A4C"    # lixeira
AMBAR = "#C68A41"        # revisar

# Cor por ação — mesmos nomes que o model.py já importava do tema antigo.
COR_LIXEIRA = TERRACOTA
COR_MANTER = LOURO
COR_REVISAR = AMBAR

GUTTER = "▌"
