# Apolo

Triador pessoal de emails. Roda em lote (não é daemon eterno), reduz ruído de
forma determinística e enfileira o resíduo pra revisão manual. Ferramenta de uso
pessoal, na própria máquina — lê o Proton Mail via **Proton Bridge** (IMAP local).

Documentação: arquitetura e princípios em [`docs/apolo.md`](docs/apolo.md); a
interface gráfica em [`docs/ui.md`](docs/ui.md); o botão da Waybar em
[`docs/waybar.md`](docs/waybar.md); como as senhas são guardadas em
[`docs/secrets.md`](docs/secrets.md).

## Por que Apolo?

O nome não é aleatório. Apolo é o deus grego associado à ordem, à proporção e
à harmonia, exatamente o que este projeto tenta impor sobre uma caixa de
entrada que cresce sem controle: transformar um fluxo disperso de emails numa
estrutura previsível, com regras, fila e revisão.

Ele também é o deus da luz e, por extensão, do esclarecimento. Separar sinal
de ruído, tirar da escuridão do "depois eu leio" as poucas mensagens que
realmente importam, é basicamente trazer clareza pra caixa de entrada.

Há ainda a associação com o conhecimento, a razão e a previsão (o oráculo de
Delfos). Faz sentido num projeto cujo passo 4 é justamente usar um modelo de
linguagem pra interpretar e classificar o resíduo que as regras
determinísticas não resolveram.

Apolo também é lembrado como figura central que coordena esferas diferentes
(música, medicina, profecia). Aqui ele coordena fontes diferentes de email
sob a mesma cascata de regras e a mesma fila de revisão, em vez de cada conta
ser tratada como um mundo à parte.

Por fim, um motivo prático: um deus grego dá uma identidade visual forte
(lira, arco, coroa de louros, sol), muito mais fácil de estilizar como ícone
de software do que o envelope genérico de todo cliente de email.

## Estado atual — passos 1 a 5 do roadmap

Implementado o **backbone** (fetch incremental + SQLite), a **limpeza de corpo
HTML/CSS**, o **motor de regras determinístico**, a **UI de revisão (Textual)** —
um hub com fila, gerenciador de regras e configurações — além de `block`/`allow`
de terminal, a **classificação do resíduo via Ollama** e as **notificações
`notify-send` + timer do systemd** (`apolo setup`).

```
apolo/
  __init__.py
  config.py            # credenciais/Bridge via env + .env (parser stdlib)
  config_writer.py     # escrita parcial e atômica do .env (UI de configurações)
  secrets.py           # senha do Bridge/IMAP via pass+GPG — ver docs/secrets.md
  cli.py               # run / status / review / block / allow / rules / setup
  clean.py             # HTML/CSS -> texto limpo (passo 1.5)
  actions.py           # despacha a fila: move pra Trash + loga (passo 3)
  scheduler.py         # controle do systemd timer (usado pela UI e pelo setup)
  notify.py            # notify-send best-effort (passo 5)
  ui/                  # interface Textual (hub + telas) — ver docs/ui.md
  ai/ollama.py         # classificação do resíduo via Ollama (passo 4)
  fetch/imap.py        # conexão Bridge, busca incremental + copy/expunge
  storage/db.py        # SQLite: emails + acoes (log p/ undo) + meta
  rules/engine.py      # cascata de precedência (passo 2)
  rules/writer.py      # leitura/escrita das regras no TOML (block/allow + unsubscribe)
  rules/config.toml    # regras de exemplo — semente copiada pro XDG no 1º uso (ver Config)
  systemd/             # templates apolo.service + apolo.timer (passo 5)
```

Núcleo sem dependências externas — tudo stdlib (`imaplib`, `sqlite3`, `email`,
`ssl`, `html.parser`, `tomllib`, `urllib`); notificação via `notify-send`
(libnotify) e agendamento via `systemd --user`. O caminho do timer (`apolo run`)
nunca importa nada de terceiros.

A **única** dependência externa é o [Textual](https://textual.textualize.io/),
usado só pela UI (`apolo review`, em `apolo/ui/`) e importado de forma *lazy*.
Vive num venv do projeto:

```bash
python -m venv .venv
.venv/bin/pip install -r requirements.txt   # textual
```

A UI (e o botão da Waybar) rodam com `.venv/bin/python -m apolo.cli review`.

## Limpeza de corpo (passo 1.5)

`apolo/clean.py` transforma corpo de email em texto legível antes de chegar nas
regras (passo 2) ou na IA (passo 4):

- `strip_html(html)` — remove tags, atributos (CSS inline) e o conteúdo de
  `<style>`/`<script>`/`<head>`; decodifica entidades (`&nbsp;`, `&ccedil;`…).
- `message_to_text(msg)` — escolhe a melhor parte MIME (prefere `text/plain`,
  cai pro `text/html` limpo), ignora anexos.
- `clean_for_classification(text)` — normaliza espaços e trunca em linhas/caracteres
  (assunto + primeiras linhas é o que vai pra IA).

O corpo só é buscado sob demanda, via `BridgeClient.fetch_message(uid)`
(`BODY.PEEK[]`, não marca lido) — coerente com "corpo só se a IA precisar".

**Stop words não são removidas, de propósito.** Stop word removal ajuda modelos
clássicos (bag-of-words/TF-IDF) mas atrapalha um LLM, que depende da linguagem
natural pra entender contexto. O texto segue como linguagem natural, só limpo.

## Configuração

Copie `.env.example` para `.env` e preencha com as credenciais **do Bridge**
(usuário e senha do Bridge, não da conta Proton):

```bash
cp .env.example .env
```

| Variável              | Padrão                          | Descrição                              |
| --------------------- | ------------------------------- | -------------------------------------- |
| `APOLO_IMAP_HOST`     | `127.0.0.1`                     | host do Bridge                         |
| `APOLO_IMAP_PORT`     | `1143`                          | porta IMAP do Bridge                   |
| `APOLO_USERNAME`      | —                               | usuário do Bridge                      |
| `APOLO_PASSWORD`      | —                               | senha do Bridge                        |
| `APOLO_IMAP_SECURITY` | `STARTTLS`                      | `STARTTLS` ou `PLAIN`                  |
| `APOLO_FOLDERS`       | `INBOX`                         | pastas a vigiar (separadas por vírgula)|
| `APOLO_DB_PATH`       | `~/.local/share/apolo/apolo.db` | caminho do banco de estado             |
| `APOLO_RULES_PATH`    | `~/.config/apolo/rules.toml`    | arquivo de regras (semeado do repo no 1º uso) |
| `APOLO_TRASH_FOLDER`  | `Trash`                         | pasta de lixeira do Proton             |
| `APOLO_AI_ENABLED`    | `true`                          | liga/desliga a classificação por IA    |
| `APOLO_OLLAMA_URL`    | `http://127.0.0.1:11434`        | endereço do Ollama                     |
| `APOLO_OLLAMA_MODEL`  | `llama3.2`                      | modelo do Ollama (ajuste pro seu)      |
| `APOLO_OLLAMA_KEEP_ALIVE` | `30m`                       | mantém o modelo quente na RAM          |

## Uso

```bash
python -m apolo.cli run                  # busca, classifica e enfileira
python -m apolo.cli run --quiet          # idem, sem notificação de desktop
python -m apolo.cli run --loop           # roda sozinho a cada 15min (Ctrl+C pra parar) —
                                          # não depende de systemd/Task Scheduler/launchd
python -m apolo.cli run --loop --interval 5min
python -m apolo.cli status               # contadores e ações sugeridas
python -m apolo.cli review               # abre o hub (UI Textual) — ver docs/ui.md
python -m apolo.cli rules                # lista as regras
python -m apolo.cli block promo.x.com    # adiciona à blocklist
python -m apolo.cli allow chefe@x.com    # adiciona à allowlist
python -m apolo.cli setup                # instala o timer do systemd (user, onde disponível)
```

`--loop` é a forma que funciona em **qualquer sistema operacional** sem
depender de nada além do Python — `apolo setup` (systemd) continua sendo a
forma "de verdade" de agendar num Linux com systemd --user, mas deixa de ser
pré-requisito pra usar o Apolo em segundo plano. Notificação de desktop
(`apolo/notify.py`) e clipboard (`apolo/extract.py`) já têm backend nativo
pra Linux, Windows e macOS — ver `apolo/platform/`. O equivalente ao botão
da Waybar (ver [`docs/waybar.md`](docs/waybar.md)) é um ícone de bandeja no
Windows ([`windows/`](windows/)) e um item de barra de menu no macOS
([`macos/`](macos/)) — ambos também ligam o Proton Bridge quando ele estiver
desligado (sem ele, só contas Gmail funcionam).

### Problemas de portabilidade noutro SO

O Apolo foi desenvolvido e testado principalmente em Linux. Se algo quebrar
no Windows ou no macOS (um backend de `apolo/platform/`, o cofre de senha, a
notificação, o clipboard, o `apolo setup`...), o jeito mais rápido de
investigar é colar o contexto abaixo numa IA, junto do erro exato:

```
Estou rodando o Apolo (triador pessoal de emails, Python, repositório
apolo-ai) no seguinte sistema operacional: [Windows 11 / macOS 14 / etc,
com versão].

Versão do Python: [python --version]
Comando que rodei: [ex.: python -m apolo.cli run --loop]
Erro ou comportamento inesperado: [cole aqui o traceback completo ou a
descrição do que aconteceu]

Contexto do projeto: o núcleo (fetch/regras/banco) usa só a stdlib do
Python; funcionalidades que dependem do SO (notificação, clipboard, cofre
de senha) ficam isoladas em apolo/platform/, com um backend por sistema
operacional. Me ajude a diagnosticar a causa raiz desse problema de
portabilidade e sugira uma correção que mantenha essa separação (stdlib no
núcleo, backend específico do SO isolado em apolo/platform/).
```

## Notificações e agendamento (passo 5)

Cada `run` abre uma notificação "Analisando…" e, no fim, a **substitui** pelo
resumo da passada (`apolo/notify.py`) — fica uma só na tela em vez de empilhar:

> **Apolo: 12 analisado(s)** — 8 mantido(s), 4 pra revisar · fila: 4

Sem novidade o aviso vira curto e de baixa urgência (o timer roda direto, não
vale empurrar popup à toa); com algo pra revisar, urgência normal. Tudo é
**best-effort**: se `notify-send` faltar (headless, sem D-Bus), a triagem segue
sem reclamar. Use `run --quiet` pra silenciar.

O `apolo setup` renderiza os templates de `apolo/systemd/` (preenchendo o
interpretador e a raiz do projeto detectados na hora) em
`~/.config/systemd/user/`, recarrega o systemd e ativa o `apolo.timer`:

```bash
python -m apolo.cli setup                  # a cada 15min (padrão), ativa o timer
python -m apolo.cli setup --interval 30min # outro intervalo (regrava as units)
python -m apolo.cli setup --no-enable      # só escreve as units, não ativa
```

O serviço é `Type=oneshot` (acorda, processa o lote, morre — nada de daemon
eterno). Logs com `journalctl --user -u apolo -f`; rodar o setup de novo é
seguro e reentrante. O timer roda em **sessão de usuário**, então precisa do
Bridge e do D-Bus de pé — se o Bridge estiver fora, o login IMAP falha e a
passada aborta limpa.

## Motor de regras (passo 2)

A cada email novo a cascata avalia de cima pra baixo e a **primeira regra que
casar decide** (`apolo/rules/engine.py`), tudo sem IA:

1. **allowlist** (remetente/domínio confiável) → mantém, nunca é tocado.
2. **blocklist** (ruído conhecido) → sugere lixeira.
3. **`List-Unsubscribe` + termo de marketing** (2 sinais) → newsletter, ação
   conforme o config. O header sozinho **não** decide — bulk importante (banco,
   recibo, GitHub) também o tem; só vira ação se casar também um termo de
   `[unsubscribe].exige`. Sem 2º sinal, segue a cascata. `exige = []` volta ao
   header-sozinho.
4. **palavras-chave** no assunto/remetente → ação por grupo.
5. *(IA — passo 4, ainda não)*
6. **default** → sem confiança, vai pra fila de revisão.

Domínio casa subdomínio (`loja-exemplo.com.br` pega `promo.loja-exemplo.com.br`). As regras
ficam num TOML editável à mão — `~/.config/apolo/rules.toml` por padrão (ou
aponte outro com `APOLO_RULES_PATH`); `apolo/rules/config.toml` no repo é só o
exemplo, copiado pra lá no primeiro uso. "Adicionar uma fonte" = adicionar uma
linha lá.

**Tudo começa em modo sugestão.** Nada é apagado pela cascata: `manter` é decisão
terminal e o resto entra na fila de revisão (`aguardando`) com a ação sugerida.
A execução automática (sem o dono) só chega quando uma regra for promovida (passo 6).

## Interface de revisão (passo 3, em Textual)

`apolo review` abre o **hub** — uma UI dark, keyboard-first, em
[Textual](https://textual.textualize.io/) (substitui a antiga TUI em curses).
Do hub você navega por seta + Enter para: a **fila de revisão**, o **gerenciador
de regras** (listar/remover/adicionar com prévia ao vivo), as **configurações**
(timer, IA, unsubscribe) e o **status**. Documentação completa em
[`docs/ui.md`](docs/ui.md).

O Textual é a **única** dependência externa e vive num venv do projeto; o botão
da Waybar e o `apolo review` rodam com `.venv/bin/python` (ver topo do README e
[`docs/waybar.md`](docs/waybar.md)). O caminho do timer (`apolo run`) nunca o
importa.

Na fila (`aguardando`), cada email já vem com a ação sugerida pela cascata:

```
↑/↓ mover   d lixeira   m manter   b block   a allow   c código   u desfazer   ↵ aplicar   esc voltar
```

`b`/`a` são o **loop de aprendizado**: gravam o domínio do remetente na
block/allowlist na hora (via `apolo/rules/writer.py`, que preserva os comentários
do TOML e valida antes de salvar) e ajustam a ação do item; `u` desfaz (e remove
a regra recém-criada).

`c` **pega o código**: para emails de confirmação, puxa o corpo do item
selecionado (Proton via Bridge, Gmail via API — `BODY.PEEK`, sem marcar lido) e
extrai candidatos a **código** (6 dígitos, com ou sem `-`, ou alfanumérico) e
**links de confirmação** (`apolo/extract.py`). Um modal lista por confiança e o
`enter` copia o escolhido pro clipboard (`wl-copy`/`xclip`/`xsel`) — útil pra
colar o OTP sem abrir o email no cliente.

Ao apertar **enter**, a UI volta ao hub e o dispatch aplica as decisões: itens `manter`
só saem da fila; itens `lixeira` são movidos pra Trash. Como o Bridge não tem
`MOVE`, a remoção é `COPY` pra Trash + `\Deleted` + `EXPUNGE` — **reversível**,
já que a mensagem fica na Trash, e cada remoção é registrada na tabela `acoes`
com dado pra reverter (base do `apolo undo`, passo 6). Itens deixados em `revisar`
permanecem na fila pra próxima.

Mover pra lixeira aqui é sempre **manual** (você despacha). A execução sem o dono
fica pro passo 6.

## Classificação por IA (passo 4)

Só o **resíduo** que a cascata deixou em `default`/`revisar` vai pro Ollama —
as regras burras resolvem a maior parte de graça. Pra esses, o `run` busca o
corpo (`BODY.PEEK[]`, não marca lido), limpa o HTML e manda **só assunto +
primeiras linhas** (`apolo/ai/ollama.py`), nunca o corpo inteiro: rápido e
privado. A conversa é via API HTTP do Ollama (`urllib`, stdlib).

- `keep_alive` alto mantém o modelo quente na RAM entre execuções — o custo vira
  inferir, não recarregar.
- A IA só **sugere**: o resultado vira a ação sugerida (`regra_casada = ia:<cat>`)
  e o email **continua na fila** pro dono confirmar. Nada é apagado pela IA.
- É opcional e tolerante a falha: se o Ollama estiver fora, o modelo ausente, ou
  a resposta vier fora do contrato, o resíduo só fica em `revisar` e o `run`
  segue normal. Ajuste `APOLO_OLLAMA_MODEL` pro modelo que você tem instalado.

## Como funciona o fetch incremental

- Seleciona a pasta em modo **readonly** (`EXAMINE`) e usa **`BODY.PEEK`**, então
  a varredura nunca marca mensagens como lidas.
- Guarda o último UID visto por pasta (tabela `meta`) e busca só `UID último+1:*`.
  O quirk do IMAP de `n:*` sempre devolver a maior mensagem é tratado filtrando
  `uid > último_visto`.
- Guarda o `UIDVALIDITY` por pasta. Se o Bridge resetar os UIDs daquela pasta, a
  validade muda e o Apolo ressincroniza só aquela pasta.

## Próximos passos (roadmap)

5. `notify-send` + systemd timer.
6. Promoção de regra pra automático + `apolo undo`.
