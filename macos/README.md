# Item da barra de menu (macOS)

Equivalente macOS do botão da Waybar no Linux (ver
[`../docs/waybar.md`](../docs/waybar.md)) e do ícone de bandeja do Windows
(ver [`../windows/`](../windows/)): um ícone clicável na barra de menu que
abre `apolo review` num Terminal e liga o Proton Bridge quando ele estiver
desligado. Não faz parte do núcleo do Apolo — é puramente um lançador.

## Por que "Ligar Proton Bridge" existe

O Bridge **não** liga sozinho no login por padrão. Sem ele rodando, só
contas Gmail funcionam no Apolo — contas Proton dependem do IMAP local que
só o Bridge expõe. O item de menu testa a conexão em
`APOLO_IMAP_HOST:APOLO_IMAP_PORT` (lido do `.env` do projeto) e, se não
responder, roda `open -a "Proton Mail Bridge"`.

## Usar o `.app` pronto

1. Gere `Apolo.app` (veja "Gerar o `.app`" abaixo) ou pegue um já gerado.
2. Copie `Apolo.app` para a **raiz do projeto**, a mesma pasta onde fica
   `.venv/`. O script detecta o projeto olhando a própria pasta em busca de
   `.venv/bin/python`; se não achar ali, tenta a pasta pai (útil se preferir
   manter `Apolo.app` dentro de `macos/` em vez de mover pra raiz).
3. Duplo clique. Na **primeira vez**, o Gatekeeper bloqueia apps sem
   assinatura da Apple — clique direito → "Abrir" em vez de duplo clique,
   confirme uma vez, e depois disso o duplo clique normal funciona.
4. O ícone aparece na barra de menu. "Abrir revisão" abre um Terminal com
   `apolo review`; "Ligar Proton Bridge" liga o Bridge se estiver desligado.

Sem `.venv/bin/python`, cai no `python3` do `PATH` — funciona, mas sem o
Textual instalado a UI não abre (rode o setup do `../README.md` primeiro).

## Gerar o `.app`

py2app **não faz cross-compile**, e o `.icns` usa `sips`/`iconutil`
(nativos do macOS) — nada disso dá pra montar fora de um Mac de verdade.

```bash
cd macos
./build.sh
```

Isso gera `apolo.icns` a partir de `apolo.png`, cria um venv de build
isolado (`.buildvenv`, separado do `.venv` do projeto), instala
`rumps`/`py2app` (`requirements.txt` deste diretório) e produz
`macos/dist/Apolo.app`.

## Por que não empacota o `apolo/` inteiro dentro do `.app`

`apolo_tray.py` só chama `python -m apolo.cli review` como subprocesso e lê
o `.env` na unha — não importa nada de `apolo/`. Isso mantém o `.app`
pequeno (só rumps) e evita ter que reconstruí-lo toda vez que o Apolo muda;
só a lógica do próprio item de menu justifica um rebuild.
