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

## Usar o `.exe` pronto

1. Gere `Apolo.exe` (veja "Gerar o `.exe`" abaixo) ou pegue um já gerado.
2. Copie **só o `Apolo.exe`** (o ícone vai embutido dentro dele) para a
   **raiz do projeto**, a mesma pasta onde fica `.venv\`. O script detecta o
   projeto olhando a própria pasta em busca de `.venv\Scripts\python.exe`;
   se não achar ali, tenta a pasta pai (útil se preferir manter o `.exe`
   dentro de `windows\` em vez de mover pra raiz).
3. Dê duplo clique. O ícone aparece na bandeja do sistema:
   - clique duplo (ou "Abrir revisão" no menu) abre um console com
     `apolo review`;
   - "Ligar Proton Bridge" (o texto muda pra "Bridge: rodando ✓" quando ele
     já está de pé) tenta ligar o Bridge.

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
diretório) e gera `windows\dist\Apolo.exe` com o `apolo.ico` embutido
(`--add-data`) — um arquivo só, sem nada solto do lado.

## Por que não empacota o `apolo/` inteiro dentro do `.exe`

`apolo_tray.py` só chama `python -m apolo.cli review` como subprocesso — não
importa nada de `apolo/`. Isso mantém o `.exe` pequeno (só pystray + Pillow)
e evita ter que reconstruí-lo toda vez que o Apolo muda; só a lógica do
próprio ícone da bandeja justifica um rebuild.
