# Ícone da bandeja (Windows)

Equivalente Windows do botão da Waybar no Linux (ver
[`../docs/waybar.md`](../docs/waybar.md)): um ícone clicável que abre
`apolo review` num console novo. Não faz parte do núcleo do Apolo — é
puramente um lançador.

## Usar o `.exe` pronto

1. Gere `Apolo.exe` (veja "Gerar o `.exe`" abaixo) ou pegue um já gerado.
2. Copie **dois arquivos** — `Apolo.exe` e `apolo.ico` — para a **raiz do
   projeto**, a mesma pasta onde fica `.venv\`. O script detecta o projeto
   olhando a própria pasta em busca de `.venv\Scripts\python.exe`; se não
   achar ali, tenta a pasta pai (útil se você preferir manter os dois dentro
   de `windows\` em vez de mover pra raiz).
3. Dê duplo clique. O ícone aparece na bandeja do sistema; clique duplo nele
   (ou "Abrir revisão" no menu) abre um console com `apolo review`.

Sem `.venv\Scripts\python.exe`, cai no `python` do `PATH` — funciona, mas sem
o Textual instalado a UI não abre (rode o setup do `../README.md` primeiro).

## Gerar o `.exe`

O PyInstaller **não faz cross-compile**: o `.exe` precisa ser gerado numa
máquina Windows de verdade (não dá pra montar por aqui, num ambiente Linux).

```bat
cd windows
build.bat
```

Isso cria um venv de build isolado (`.buildvenv`, separado do `.venv` do
projeto), instala `pystray`/`Pillow`/`pyinstaller` (`requirements.txt` deste
diretório) e gera `windows\dist\Apolo.exe`.

## Por que não empacota o `apolo/` inteiro dentro do `.exe`

`apolo_tray.py` só chama `python -m apolo.cli review` como subprocesso — não
importa nada de `apolo/`. Isso mantém o `.exe` pequeno (só pystray + Pillow)
e evita ter que reconstruí-lo toda vez que o Apolo muda; só a lógica do
próprio ícone da bandeja justifica um rebuild.
