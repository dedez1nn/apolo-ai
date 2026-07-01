# UI do Apolo (Textual)

A interface gráfica do Apolo — um **hub** em terminal que abre no clique do botão
da Waybar (ou em `apolo review`). Substitui a antiga TUI em curses por uma UI dark,
com ícones, cores por ação e foco total em **teclado** (seta + Enter, sem mouse
obrigatório). Construída em [Textual](https://textual.textualize.io/).

> Mockups/screenshots: a UI usa glyphs de **nerd font** (ícones) — eles aparecem no
> terminal real (kitty), mas não em conversões SVG sem a fonte.

## Princípios

- **Lazy + isolada.** Todo o pacote `apolo/ui/` só é importado quando a UI abre
  (import lazy no `cli.cmd_review`). O caminho do timer (`apolo run`) **nunca**
  importa Textual — o núcleo segue 100% stdlib. Veja [README](../README.md).
- **A UI não toca a rede.** Ela só **lê a fila** (passada pelo `cli`) e **escreve
  arquivos locais** (regras no TOML, ajustes no `.env`, units do systemd). O
  *dispatch* real via IMAP (mover pra Trash) acontece **depois que o app fecha**,
  no `cli`, com as decisões que o app devolve. Mantém o modelo offline do passo 3.
- **Keyboard-first.** Cada tela lista os atalhos no rodapé (Footer do Textual).

## Como abre

```
botão da Waybar ──► kitty -e apolo-review.sh ──► .venv/bin/python -m apolo.cli review
                                                          │
                                                  cli.cmd_review:
                                                   1. lê a fila + stats do SQLite
                                                   2. run_ui(rows, rules_path, stats, config)
                                                   3. ao fechar, despacha via IMAP os itens decididos
```

A UI depende do **Textual**, que vive no venv do projeto (`~/proton-api/.venv`).
O launcher da Waybar (`~/.config/waybar/apolo-review.sh`) já aponta pro python do
venv — detalhes em [waybar.md](waybar.md).

## Telas

### 󰊫 Hub (inicial)

Menu navegável por `↑↓`/`jk` + `Enter`. Mostra relógio ao vivo, tamanho da fila e
a última passada, com *badges* de contagem (fila, regras). `q`/`esc` fecha o app.

| Item | Estado |
|---|---|
| Revisar fila | ✅ pronto |
| Adicionar regra | → unificado na tela **Regras** |
| Prévia (dry-run da cascata na INBOX) | 🚧 stub |
| Regras configuradas | ✅ pronto |
| Rodar agora (uma passada) | 🚧 stub |
| Configurações | ✅ pronto |
| Status & contadores | ✅ pronto |

### 󰋚 Revisar fila

A fila de revisão (`aguardando`) repaginada. Cada email mostra a ação sugerida
(cor + ícone), remetente, data e assunto.

```
d lixeira   m manter   b block   a allow   c código   u desfazer   ↵ aplicar   esc voltar
```

- Decidir (`d`/`m`/`b`/`a`) **tira o email da lista na hora** (vai pra uma pilha de
  histórico da sessão); `u` desfaz a última.
- `c` **pega o código**: puxa o corpo do email selecionado (Proton via Bridge,
  Gmail via API — `BODY.PEEK`, não marca lido) e extrai candidatos a **código de
  confirmação** (6 dígitos, com ou sem `-`, ou alfanumérico) e **links de
  confirmação** (`apolo/extract.py`). Um modal lista os candidatos por confiança;
  `↵` copia o escolhido pro clipboard (`wl-copy`/`xclip`/`xsel`), `esc` fecha. É a
  única ação da fila que toca a rede na hora, junto do `↵` aplicar.
- `b`/`a` são o **loop de aprendizado**: gravam o *domínio* do remetente na
  block/allowlist na hora (via `rules/writer.py`) e ajustam a ação do item; `u`
  também remove a regra recém-criada.
- `↵` **aplica**: as decisões viram itens de dispatch e a tela volta ao Hub. Sair
  com `esc` **cancela** as decisões não aplicadas (devolve à fila).
- O dispatch IMAP (mover `lixeira` pra Trash) roda no `cli`, ao fechar o app.

### 󰈙 Regras (listar · remover · adicionar)

Gerenciador das listas allow/block. Lista as entradas (allowlist em verde,
blocklist em vermelho) com tipo e valor.

```
a adicionar    x (ou Delete) remover    esc voltar
```

- **`x`** remove a regra selecionada na hora (via `rules/writer.remove_rule_entry`).
- **`a`** abre o modal **Nova regra** com **prévia ao vivo**: você escolhe a lista,
  digita o valor (o tipo — domínio vs remetente — é detectado), e a tela mostra
  **na hora quantos e quais emails da fila aquela regra pegaria** antes de salvar
  (`ctrl+s`). Grava via `rules/writer.add_rule_entry`.
- O contador de regras do Hub é atualizado ao voltar.

### ⚙ Configurações

A **única** tela que muda estado fora da fila. Três grupos, três destinos. Nada é
aplicado enquanto você edita — só no **`ctrl+s`** (salvar); `esc` volta.

| Grupo | Edita | Grava em |
|---|---|---|
| **Agendamento** | intervalo + timer lig/des | `systemd --user` (via `apolo.scheduler`) |
| **IA · Ollama** | classificar resíduo, modelo, keep_alive | `.env` (escrita parcial) |
| **Newsletters** | ação do List-Unsubscribe (lixeira/revisar) | `rules/config.toml` |
| **Geral** | pastas, lixeira, IMAP, caminhos | — (somente leitura) |

Ressalvas (intencionais):

- **Timer:** salvar dispara `systemctl --user` de verdade (escreve as units e
  ativa/desativa o `apolo.timer`). É a única ação de sistema da UI.
- **`.env`:** a escrita é **parcial e atômica** (`apolo/config_writer.py`) —
  atualiza só as chaves mexidas e **preserva o resto, inclusive as credenciais do
  Bridge**. Como o `.env` é lido uma vez no início, a mudança **vale a partir da
  próxima passada** (`apolo run`), não na sessão aberta.
- **TOML:** a ação do unsubscribe vale já na próxima passada (o engine relê o TOML
  a cada execução).

### 󰋽 Status

Leitura pura: última passada, tamanho da fila, total de regras e contadores por
status / ação sugerida (juntados pelo `cli` antes de abrir o app).

## Arquivos

```
apolo/ui/
  __init__.py        # expõe run_ui (import lazy de tudo)
  app.py             # ApoloApp (App raiz) + UiStats + run_ui()
  model.py           # Item da fila + ícones/cores/formatadores
  hub.py             # HubScreen (menu inicial + roteamento)
  queue.py           # QueueScreen (fila de revisão)
  rules_screen.py    # RulesScreen + AddRuleModal (prévia ao vivo)
  settings.py        # SettingsScreen (timer / .env / TOML)
  status.py          # StatusScreen (leitura)
  app.tcss           # tema dark (TCSS)

apolo/scheduler.py        # controle do systemd timer (stdlib) — usado pela UI e pelo `setup`
apolo/config_writer.py    # escrita parcial e atômica do .env (stdlib)
apolo/rules/writer.py     # + list_entries / set_unsubscribe_acao / get_unsubscribe_acao
```

`apolo/scheduler.py` e `apolo/config_writer.py` são **stdlib** e ficam fora de
`ui/` de propósito: são lógica de escrita testável, sem Textual.

## Testes

As telas são exercitadas *headless* com `App.run_test()` (piloto do Textual):
navegação, decisões na fila, salvar configurações (com checagem de que as
credenciais do `.env` são preservadas) e o ciclo adicionar→prever→remover de
regras. Os ícones nerd-font não influenciam a lógica testada.
