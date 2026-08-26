# Botão do apolo na Waybar

Botão na Waybar (setup JaKooLit / Hyprland-Dots) que abre o app desktop de
revisão (`apolo review`) com um clique. Fica no **centro** da barra, entre o
clima e o relógio, mostrando a foto do apolo como ícone.

## Arquivos envolvidos

Tudo vive em `~/.config/waybar/` (não no repo — são dotfiles do sistema):

| Arquivo | Papel |
|---|---|
| `apolo.png` | Ícone, redimensionado de `~/Downloads/apolo.png` (1024² → 48×48) |
| `apolo-review.sh` | Launcher: `~/proton-api/.venv/bin/python -m apolo.cli review` |
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
# Lançador da revisão do apolo — chamado pelo botão da Waybar direto,
# sem terminal no meio (o app já abre a própria janela).
PY="$HOME/proton-api/.venv/bin/python"
[ -x "$PY" ] || PY="python"   # fallback se o venv ainda não existir
cd "$HOME/proton-api" && exec "$PY" -m apolo.cli review
```

A UI é um app desktop (Flet, `apolo/gui/`) — abre a própria janela, não
precisa de terminal nenhum no meio. O `on-click` chama este script direto
(sem `kitty -e`); erros de inicialização vão pro log do apolo, não pra tela,
já que não há mais console pra segurar a mensagem. O Flet mora no venv do
projeto (`~/proton-api/.venv`); recrie com `python -m venv .venv &&
.venv/bin/pip install -r requirements.txt`.

### 3. Módulo — em `~/.config/waybar/UserModules`

```jsonc
"custom/apolo": {
    "format": " ",
    "tooltip": true,
    "tooltip-format": "apolo · triagem de emails (clique pra abrir)",
    "on-click": "$HOME/.config/waybar/apolo-review.sh"
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
    /* GTK CSS não expande ~/$HOME em url() — use o caminho absoluto real */
    background-image: url("/home/<seu-usuario>/.config/waybar/apolo.png");
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
- **Posição:** mova `"custom/apolo"` dentro de `modules-center`/`-left`/`-right`.
- **Mais "liquid glass":** dá pra virar uma pill de vidro (fundo translúcido +
  `border` + `box-shadow` com brilho interno), espelhando o estilo de
  `#workspaces button.active` no mesmo CSS.

## Observações

- O blur "glass" do tema vem do **Hyprland** (layerrule de blur na camada da
  Waybar), não do CSS — GTK3 não suporta `backdrop-filter`.
- Esses arquivos são restaurados pelo `copy.sh` do JaKooLit; ao reinstalar os
  dotfiles, reaplicar os passos acima.

## O que isso é, na verdade

Esse módulo é só um **launcher**: um ícone que abre a janela de revisão do
apolo (`apolo/gui/`) direto, sem terminal no meio. Nada disso mora no
repositório — é puramente dotfiles do Hyprland/Waybar deste sistema. O app
em si não sabe o que é Waybar; abre igual rodando `apolo review` de qualquer
lugar, em qualquer SO.

Um equivalente noutro ambiente é a mesma ideia com outra casca — nenhum deles
precisa ser construído pra usar o Apolo, só ajuda a lembrar que a fila tem
algo esperando:

- **Windows:** um ícone na bandeja do sistema via
  [`pystray`](https://pypi.org/project/pystray/) (`windows/apolo_tray.py`),
  cujo clique roda `python -m apolo.cli review` como processo comum — sem
  console, a janela do app já é a interface.
- **macOS:** um item na barra de menu via
  [`rumps`](https://pypi.org/project/rumps/) (`macos/apolo_tray.py`), mesma
  ideia.
- **Qualquer SO, sem instalar nada:** um alias de shell (`alias apolo-review='python
  -m apolo.cli review'`) ou atalho de teclado do próprio ambiente — dá menos
  destaque visual, mas cobre o mesmo caso de uso (abrir a fila com um comando
  rápido) sem depender de nenhuma dependência nova.
