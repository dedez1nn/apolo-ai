#!/usr/bin/env bash
# Gera macos/dist/Apolo.app (item de barra de menu que abre "apolo review" e
# liga o Proton Bridge). Rodar numa máquina macOS de verdade -- py2app não
# faz cross-compile de outro SO pra macOS, e o .icns usa `sips`/`iconutil`,
# que só existem no macOS.
set -euo pipefail
cd "$(dirname "$0")"

# --- ícone: apolo.png (1024x1024) -> apolo.iconset -> apolo.icns ---
rm -rf apolo.iconset apolo.icns
mkdir apolo.iconset
for size in 16 32 128 256 512; do
    sips -z "$size" "$size" apolo.png --out "apolo.iconset/icon_${size}x${size}.png" >/dev/null
    double=$((size * 2))
    sips -z "$double" "$double" apolo.png --out "apolo.iconset/icon_${size}x${size}@2x.png" >/dev/null
done
iconutil -c icns apolo.iconset -o apolo.icns
rm -rf apolo.iconset

# --- build ---
python3 -m venv .buildvenv
source .buildvenv/bin/activate
pip install -r requirements.txt

rm -rf build dist
python setup.py py2app

echo
echo "Pronto: macos/dist/Apolo.app"
echo "Copie Apolo.app para a raiz do projeto (ao lado de .venv/) e abra com"
echo "duplo clique. Na primeira vez, clique direito -> Abrir (app sem"
echo "assinatura da Apple, o Gatekeeper bloqueia o duplo clique direto)."
