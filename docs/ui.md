# UI do Apolo (Flet)

A interface gráfica do Apolo é um app **desktop nativo** (não terminal) que abre
no clique do botão da Waybar/bandeja (ou em `apolo review`). Substitui a antiga
TUI em Textual: mesma UI dark, cores por ação e foco em **teclado** (seta +
Enter, com clique como complemento, não substituto), agora numa janela de
verdade, sem depender de terminal nenhum. Construída em
[Flet](https://flet.dev/).

Paleta tirada do próprio motivo do nome do projeto (ver
[README](../README.md), "Por que Apolo?"): sol (ouro, ênfase/ação primária),
louro (verde, "manter"), terracota queimada ("lixeira"), sobre um fundo único
escuro ("noite egeia"). Sem tema claro pro app em si, sem gradiente.

> Migração faseada (Textual → Flet): esta página documenta a **Fase 1**,
> caminho principal só. Swipe, Sugestões de regra, Ruído e os formulários de
> conta Gmail/IMAP ainda não foram portados (ver seção "O que falta" no fim).

## Princípios

- **Lazy + isolada.** Todo o pacote `apolo/gui/` só é importado quando a UI abre
  (import lazy no `cli.cmd_review`). O caminho do timer (`apolo run`) **nunca**
  importa Flet: o núcleo segue 100% stdlib. Veja [README](../README.md).
- **A UI não toca a rede** (com três exceções pontuais e conscientes: checar
  token Gmail ao abrir, buscar corpo do email pra "pegar código"/"ver
  corpo", e autorizar conta em "Configurar Gmail"). Fora isso, ela só **lê a
  fila** (passada pelo `cli`) e **escreve
  arquivos locais** (regras no TOML, ajustes no `.env`, units do systemd). O
  *dispatch* real via IMAP (mover pra Trash) acontece **dentro da própria
  tela de fila**, ao aplicar, sem precisar fechar o app. Mantém o modelo
  offline do passo 3.
- **Keyboard-first.** Cada tela lista os atalhos no rodapé; clique é um
  complemento (seleciona/abre), nunca a única forma de operar uma ação.

## Como abre

```
botão da Waybar/bandeja ──► .venv/bin/python -m apolo.cli review
                                       │
                               cli.cmd_review:
                                1. lê a fila + stats do SQLite
                                2. run_ui(rows, rules_path, stats, config): abre a janela, bloqueia
                                3. ao fechar, despacha por garantia qualquer item que tenha
                                   voltado sem ter sido aplicado inline (raro, ver "Revisar fila")
```

Sem terminal no meio: o app é uma janela desktop, então o launcher (Waybar,
bandeja do Windows, barra de menu do macOS) só precisa rodar o comando.
Nenhum deles abre mais um `kitty`/console pra isso. Detalhes por SO em
[waybar.md](waybar.md) (Linux) e `windows/apolo_tray.py` (Windows).

## Telas (Fase 1)

### Hub (inicial)

Lista navegável por `↑↓`/`jk` + `Enter` (clique também abre). Mostra a fila
atual e a última passada no cabeçalho, com *badges* de contagem (fila,
regras). `q`/`esc` fecha o app.

| Item | Estado |
|---|---|
| Revisar fila | ✅ pronto |
| Regras configuradas | ✅ pronto |
| Configurar Gmail | ✅ pronto |
| Configurações | ✅ pronto |
| Status & contadores | ✅ pronto |

O painel mestre-detalhe de prévia ao vivo que a versão Textual tinha (mostrar
amostra da fila/regras conforme o cursor move, sem entrar na sub-tela) foi
cortado nesta fase, ver "O que falta".

### Revisar fila

A fila de revisão (`aguardando`) repaginada. Cada email mostra a ação sugerida
(cor + ícone), remetente, data e assunto.

```
d lixeira   m manter   b block   a allow   v ver corpo   c código   s sincronizar   u desfazer   tab conta   ↵ aplicar   esc voltar
```

- Decidir (`d`/`m`/`b`/`a`) **tira o email da lista na hora** (vai pra uma pilha de
  histórico da sessão); `u` desfaz a última.
- `s` **sincroniza ao vivo**: varre contas/pastas por completo
  (`apolo.sync.run_sync`) numa thread, sem travar a tela. Itens novos entram
  direto na lista, e os que dependem do Ollama aparecem como "analisando" até
  a resposta chegar.
- `v` **ver corpo**: mostra o corpo do email selecionado como texto limpo
  (`apolo.clean.message_to_text`), versão enxuta da Fase 1, sem resolução de
  imagem inline por CID (ver "O que falta").
- `c` **pega o código**: puxa o corpo do email selecionado (Proton via Bridge,
  Gmail via API, `BODY.PEEK`, não marca lido) e extrai candidatos a **código de
  confirmação** (6 dígitos, com ou sem `-`, ou alfanumérico) e **links de
  confirmação** (`apolo/extract.py`). Um diálogo lista os candidatos por
  confiança; `↵` copia o escolhido pro clipboard, `esc` fecha.
- `b`/`a` são o **loop de aprendizado**: gravam o *domínio* do remetente na
  block/allowlist na hora (via `rules/writer.py`) e ajustam a ação do item; `u`
  também remove a regra recém-criada.
- `↵` **aplica**: despacha as decisões AGORA (via IMAP/Gmail, num diálogo com
  progresso numa thread) e a tela volta ao Hub. Sair com `esc` **cancela** as
  decisões não aplicadas (devolve à fila).

### Regras (listar · remover · adicionar/editar)

Gerenciador das listas allow/block. Lista as entradas (allowlist em louro,
blocklist em terracota) com tipo e valor.

```
a adicionar    e editar    x (ou Delete) remover    esc voltar
```

- **`x`** remove a regra selecionada na hora (via `rules/writer.remove_rule_entry`).
- **`a`/`e`** abrem um diálogo **Nova regra**/**Editar regra** com **prévia ao
  vivo**: escolhe a lista, digita o valor (o tipo, domínio vs remetente, é
  detectado), e o diálogo mostra **na hora quantos emails da fila aquela
  regra pegaria** antes de salvar (`ctrl+s`).
- O contador de regras do Hub é atualizado ao voltar.

### Configurar Gmail

Autoriza uma conta nova ou reautoriza uma existente via OAuth2 (loopback
redirect, `apolo.fetch.gmail.GmailClient.authorize()`). Credenciais OAuth2
(client_id/client_secret) vêm do `.env`, compartilhadas entre contas; a tela
só pede o nome. `ctrl+s` autoriza (abre o navegador sozinho, com a URL
também mostrada pra copiar se não abrir); `esc` volta.

A prévia do Hub (cursor parado no item, sem abrir) já lista as contas
vinculadas com o estado do token: badge terracota no menu (e "reautorizar:
`<motivo>`" na prévia) quando `apolo.fetch.gmail.GmailClient.check_token()`
(rodado em segundo plano na abertura do app) encontra um token revogado ou
expirado, em vez de só um aviso passageiro.

### Configurações

A **única** tela que muda estado fora da fila. Três grupos, três destinos. Nada é
aplicado enquanto você edita, só no **`ctrl+s`** (salvar); `esc` volta.

| Grupo | Edita | Grava em |
|---|---|---|
| **Agendamento** | intervalo + timer lig/des | `systemd --user` (via `apolo.scheduler`) |
| **IA · Ollama** | classificar resíduo, modelo, keep_alive | `.env` (escrita parcial) |
| **Newsletters** | ação do List-Unsubscribe (lixeira/revisar) | `rules/config.toml` |
| **Geral** | pastas, lixeira, IMAP, caminhos | (somente leitura) |

Ressalvas (intencionais):

- **Timer:** salvar dispara `systemctl --user` de verdade (escreve as units e
  ativa/desativa o `apolo.timer`). É a única ação de sistema da UI.
- **`.env`:** a escrita é **parcial e atômica** (`apolo/config_writer.py`):
  atualiza só as chaves mexidas e **preserva o resto, inclusive as credenciais do
  Bridge**. Como o `.env` é lido uma vez no início, a mudança **vale a partir da
  próxima passada** (`apolo run`), não na sessão aberta.
- **TOML:** a ação do unsubscribe vale já na próxima passada (o engine relê o TOML
  a cada execução).

### Status

Leitura pura: última passada, tamanho da fila, total de regras e contadores por
status / ação sugerida (juntados pelo `cli` antes de abrir o app).

## Arquivos

```
apolo/gui/
  __init__.py        # expõe run_ui (import lazy de tudo)
  app.py             # ApoloApp (estado raiz + pilha de navegação/teclado) + UiStats + run_ui()
  theme.py           # paleta Apolo (sol/louro/terracota/mármore/noite egeia)
  model.py           # Item da fila + ícones/cores/formatadores
  widgets.py         # cabeçalho/rodapé/scaffold compartilhados entre telas
  hub.py             # HubScreen (menu inicial + roteamento)
  queue.py           # QueueScreen + DispatchProgress + CodeModal
  rules_screen.py    # RulesScreen + RuleFormModal (adicionar/editar, prévia ao vivo)
  gmail_setup.py     # GmailSetupScreen (autoriza/reautoriza conta via OAuth2)
  settings.py        # SettingsScreen (timer / .env / TOML)
  status.py          # StatusScreen (leitura)
  confirm.py         # ConfirmModal genérico (sim/não)
  body_view.py        # "ver corpo": texto limpo, sem parser de HTML

apolo/scheduler.py        # controle do systemd timer (stdlib), usado pela UI e pelo `setup`
apolo/config_writer.py    # escrita parcial e atômica do .env (stdlib)
apolo/rules/writer.py     # + list_entries / set_unsubscribe_acao / get_unsubscribe_acao
```

`apolo/scheduler.py` e `apolo/config_writer.py` são **stdlib** e ficam fora de
`gui/` de propósito: são lógica de escrita testável, sem Flet.

## O que falta (Fase 2, fora do escopo da migração inicial)

Cortado deliberadamente da Fase 1 pra manter o caminho principal pequeno e
verificável; nenhum deles tem fallback pra TUI antiga (apagada), ficam
indisponíveis até serem portados:

- Swipe (mesma fila em modo "cartas"), Sugestões de regra (baseado no
  histórico), Ruído (emails auto-enviados à lixeira, com restaurar).
- Formulário de conta IMAP genérica (Gmail já tem tela própria, "Configurar
  Gmail"): `apolo accounts add --provider imap` na CLI cobre onboarding
  dessas contas nesse meio-tempo.
- Painel mestre-detalhe de prévia ao vivo no Hub.
- Resolução de imagem inline por CID em "ver corpo" (`apolo/gui/body_view.py`
  hoje só mostra texto limpo).
