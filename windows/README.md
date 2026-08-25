# Ícone da bandeja (Windows)

Equivalente Windows do botão da Waybar no Linux (ver
[`../docs/waybar.md`](../docs/waybar.md)): clicar no atalho já abre a
interface de revisão (`apolo review`) num console novo, e sobe um ícone na
bandeja do sistema que fica disponível depois pra reabrir a revisão ou
ligar o Proton Bridge. Não faz parte do núcleo do Apolo — é puramente um
lançador.

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

1. Cria o **`.venv` do projeto na raiz** (se ainda não existir) e instala
   `requirements.txt` da raiz — é esse venv que roda o Apolo de verdade
   (`apolo review`, `apolo run`...). Se já existir, pula essa parte (não
   reinstala a cada build).
2. Cria um venv de build isolado (`.buildvenv`, dentro de `windows\`,
   **diferente** do `.venv` do passo 1) e instala `pystray`/`Pillow`/
   `pyinstaller` (`requirements.txt` deste diretório) — só ferramenta de
   empacotamento, não tem nada a ver com rodar o Apolo.
3. Empacota `windows\Apolo.exe` com o `apolo.ico` embutido (`--add-data`) —
   o `.exe` **fica em `windows\`**, do lado do `.buildvenv`, não vai pra
   raiz do projeto.
4. Cria um **atalho na raiz do projeto** (`Apolo.lnk`) apontando pro `.exe`
   em `windows\`, já com o ícone do Apolo.

Ou seja: `build.bat` sozinho já deixa o Apolo pronto pra rodar (falta só o
`.env` com as credenciais do Bridge — ver `../README.md`) *e* o ícone de
bandeja, tudo num clique.

A janela do `cmd` fica aberta no final (com `pause`) pra dar tempo de ler o
resultado ou um eventual erro.

Depois disso, o atalho `Apolo.lnk` na raiz é o que você usa: copie ele (não
o `.exe`) pra Área de Trabalho, Menu Iniciar, barra de tarefas — onde
preferir. O `.exe` de verdade continua parado em `windows\`, ao lado do
`.venv\` do projeto (que é onde o script espera achar
`.venv\Scripts\python.exe`).

Duplo clique no atalho (ou no `.exe` direto): abre **na hora** um console
com `apolo review` e sobe o ícone na bandeja do sistema (visível ou
escondido atrás da seta `^`, perto do relógio). Fechar o console não
encerra o Apolo — o ícone continua ali, e pelo menu dele dá pra:
- "Abrir revisão" — reabre o console com `apolo review` (é o item padrão:
  clique duplo no ícone já dispara direto, sem precisar abrir o menu);
- "Ligar Proton Bridge" (o texto muda pra "Bridge: rodando ✓" quando ele já
  está de pé) — tenta ligar o Bridge;
- "Sair" — encerra o ícone de vez.

Sem `.venv\Scripts\python.exe`, cai no `python` do `PATH` — funciona, mas sem
o Textual instalado a UI não abre (rode o setup do `../README.md` primeiro).

## Se nada acontecer ao clicar (nem console, nem erro visível)

Como é `--windowed` (sem console), qualquer erro na inicialização — falha
ao importar `pystray`/`Pillow` no `.exe`, ícone não encontrado, o `Popen`
que abriria a revisão falhando — morre em silêncio, sem traceback visível
em lugar nenhum. Todo esse caminho tem log em **`windows\apolo_tray.log`**
(criado do lado do `.exe` assim que ele roda, uma linha por passo). Depois
de tentar abrir, olhe esse arquivo — ele mostra até onde chegou antes de
travar.

Rodar `build.bat` de novo (pra atualizar o `.exe`) é seguro: sobrescreve o
`.exe` e reaponta o atalho, nada duplica.

## Por que não empacota o `apolo/` inteiro dentro do `.exe`

`apolo_tray.py` só chama `python -m apolo.cli review` como subprocesso — não
importa nada de `apolo/`. Isso mantém o `.exe` pequeno (só pystray + Pillow)
e evita ter que reconstruí-lo toda vez que o Apolo muda; só a lógica do
próprio ícone da bandeja justifica um rebuild.
