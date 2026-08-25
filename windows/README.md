# Ícone da bandeja (Windows)

Equivalente Windows do botão da Waybar no Linux (ver
[`../docs/waybar.md`](../docs/waybar.md)): um ícone clicável que abre
`apolo review` num console novo e liga o Proton Bridge quando ele estiver
desligado. Não faz parte do núcleo do Apolo — é puramente um lançador.

## Por que "Ligar Proton Bridge" existe

O Bridge **não** liga sozinho no login por padrão. Sem ele rodando, só
contas Gmail funcionam no Apolo — contas Proton dependem do IMAP local que
só o Bridge expõe. O item de menu testa a conexão em
`APOLO_IMAP_HOST:APOLO_IMAP_PORT` (lido do `.env` do projeto) e, se não
responder, procura o `.exe` do Bridge nos caminhos de instalação padrão do
Windows e abre. Se não achar em nenhum (instalação fora do padrão), avisa
pra abrir manualmente.

## Gerar e usar

O PyInstaller **não faz cross-compile**: precisa rodar numa máquina Windows
de verdade (não dá pra montar por aqui, num ambiente Linux). É só dar duplo
clique em `build.bat` — ele:

1. Cria um venv de build isolado (`.buildvenv`, separado do `.venv` do
   projeto) e instala `pystray`/`Pillow`/`pyinstaller`
   (`requirements.txt` deste diretório).
2. Empacota `windows\Apolo.exe` com o `apolo.ico` embutido (`--add-data`) —
   o `.exe` **fica em `windows\`**, do lado do `.buildvenv`, não vai pra
   raiz do projeto.
3. Cria um **atalho na raiz do projeto** (`Apolo.lnk`) apontando pro `.exe`
   em `windows\`, já com o ícone do Apolo.

A janela do `cmd` fica aberta no final (com `pause`) pra dar tempo de ler o
resultado ou um eventual erro.

Depois disso, o atalho `Apolo.lnk` na raiz é o que você usa: copie ele (não
o `.exe`) pra Área de Trabalho, Menu Iniciar, barra de tarefas — onde
preferir. O `.exe` de verdade continua parado em `windows\`, ao lado do
`.venv\` do projeto (que é onde o script espera achar
`.venv\Scripts\python.exe`).

Duplo clique no atalho (ou no `.exe` direto): o ícone aparece na bandeja do
sistema:
- clique duplo (ou "Abrir revisão" no menu) abre um console com
  `apolo review`;
- "Ligar Proton Bridge" (o texto muda pra "Bridge: rodando ✓" quando ele já
  está de pé) tenta ligar o Bridge.

Sem `.venv\Scripts\python.exe`, cai no `python` do `PATH` — funciona, mas sem
o Textual instalado a UI não abre (rode o setup do `../README.md` primeiro).

Rodar `build.bat` de novo (pra atualizar o `.exe`) é seguro: sobrescreve o
`.exe` e reaponta o atalho, nada duplica.

## Por que não empacota o `apolo/` inteiro dentro do `.exe`

`apolo_tray.py` só chama `python -m apolo.cli review` como subprocesso — não
importa nada de `apolo/`. Isso mantém o `.exe` pequeno (só pystray + Pillow)
e evita ter que reconstruí-lo toda vez que o Apolo muda; só a lógica do
próprio ícone da bandeja justifica um rebuild.
