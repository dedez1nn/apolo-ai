# Botão do apolo na Waybar

Botão na Waybar (setup JaKooLit / Hyprland-Dots) que abre a TUI de revisão
(`apolo review`) com um clique. Fica no **centro** da barra, entre o clima e o
relógio, mostrando a foto do apolo como ícone.

## Arquivos envolvidos

Tudo vive em `~/.config/waybar/` (não no repo — são dotfiles do sistema):

| Arquivo | Papel |
|---|---|
| `apolo.png` | Ícone, redimensionado de `~/Downloads/apolo.png` (1024² → 48×48) |
| `apolo-review.sh` | Launcher: `cd ~/proton-api && python -m apolo.cli review` |
| `UserModules` | Define o módulo `custom/apolo` (formato, tooltip, `on-click`) |
| `configs/[TOP] Andre Liquid Glass` | `config` ativo — `custom/apolo` em `modules-center` |
| `style/[Extra] Liquid Glass.css` | Estilo de `#custom-apolo` (ícone + hover) |

> `~/.config/waybar/config` e `style.css` são **symlinks** pros arquivos em
> `configs/` e `style/`. Edite sempre o alvo real do symlink.

## Como reproduzir

### 1. Redimensionar a imagem (ImageMagick)

```bash
magick ~/Downloads/apolo.png -resize 48x48 -background none \
       -gravity center -extent 48x48 ~/.config/waybar/apolo.png
```

### 2. Launcher — `~/.config/waybar/apolo-review.sh` (`chmod +x`)

```bash
#!/usr/bin/env bash
# Lançador da UI de revisão do apolo — chamado pelo botão da Waybar (kitty -e).
cd "$HOME/proton-api" || { echo "pasta ~/proton-api não encontrada"; read -rn1; exit 1; }
# A UI usa Textual, que vive no venv do projeto; o python do sistema não o tem.
PY="$HOME/proton-api/.venv/bin/python"
[ -x "$PY" ] || PY="python"   # fallback se o venv ainda não existir
"$PY" -m apolo.cli review
rc=$?
if [ "$rc" -ne 0 ]; then
    echo
    read -rn1 -p "apolo saiu com erro ($rc). Pressione qualquer tecla para fechar..."
fi
```

A UI é Textual (`apolo/ui/`), então precisa de um terminal — o `on-click` abre o
`kitty` com `-e` apontando pra este script. O Textual mora no venv
(`~/proton-api/.venv`); recrie com `python -m venv .venv && .venv/bin/pip
install -r requirements.txt`.

### 3. Módulo — em `~/.config/waybar/UserModules`

```jsonc
"custom/apolo": {
    "format": " ",
    "tooltip": true,
    "tooltip-format": "apolo · triagem de emails (clique pra abrir)",
    "on-click": "kitty --title 'apolo · review' --class apolo-review -e $HOME/.config/waybar/apolo-review.sh"
},
```

O `format` é um espaço — o ícone em si vem do `background-image` no CSS.

### 4. Posição — em `modules-center` do config ativo

```jsonc
"modules-center": [
    "custom/weather",
    "custom/apolo",
    "clock",
],
```

### 5. Estilo — em `style/[Extra] Liquid Glass.css`

```css
#custom-apolo {
    background-image: url("/home/andrelmi/.config/waybar/apolo.png");
    background-repeat: no-repeat;
    background-position: center;
    background-size: 20px 20px;
    min-width: 22px;
    padding: 0 6px;
    margin: 0 2px;
    transition: background-size 0.15s ease;
}

#custom-apolo:hover {
    background-size: 24px 24px;   /* leve zoom ao passar o mouse */
}
```

### 6. Recarregar a Waybar

```bash
killall -SIGUSR2 waybar          # reload do config + estilo
# se o módulo novo não aparecer, restart completo:
killall waybar && waybar & disown
```

## Ajustes comuns

- **Tamanho do ícone:** `background-size` (e o do `:hover`).
- **Terminal:** troque `kitty` por `alacritty` no `on-click`.
- **Posição:** mova `"custom/apolo"` dentro de `modules-center`/`-left`/`-right`.
- **Mais "liquid glass":** dá pra virar uma pill de vidro (fundo translúcido +
  `border` + `box-shadow` com brilho interno), espelhando o estilo de
  `#workspaces button.active` no mesmo CSS.

## Observações

- O blur "glass" do tema vem do **Hyprland** (layerrule de blur na camada da
  Waybar), não do CSS — GTK3 não suporta `backdrop-filter`.
- Esses arquivos são restaurados pelo `copy.sh` do JaKooLit; ao reinstalar os
  dotfiles, reaplicar os passos acima.
