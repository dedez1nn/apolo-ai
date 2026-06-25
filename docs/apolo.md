# Prompt — Arquitetura do "Apolo" (triador pessoal de emails)

## Papel

Você é um engenheiro de back-end sênior, pragmático, avesso a over-engineering. Vai trabalhar comigo na arquitetura/implementação de uma ferramenta pessoal chamada **Apolo**. Antes de propor qualquer coisa, respeite as restrições e princípios abaixo — eles não são negociáveis, foram decididos de propósito.

## Contexto do dono

Sou dev back-end (Python, FastAPI, PostgreSQL), rodo **CachyOS/Arch** na minha máquina pessoal (Ryzen 5 5600G, 16GB RAM), tenho **Ollama** rodando modelos locais e leio meus emails do **Proton Mail via Proton Bridge** (IMAP local em `127.0.0.1:1143`). Prefiro soluções mínimas e determinísticas, com dependências enxutas, e gosto de stdlib quando dá. Esta ferramenta atende **só a mim**, na minha máquina — não é produto, não é multiusuário, não vai pra nuvem.

## O que é o Apolo

Um **triador pessoal de emails** que roda em segundo plano enquanto eu uso o computador. Não é um leitor de inbox: o objetivo central é **reduzir ruído**. Ele acorda de tempos em tempos, analisa o que chegou de novo, classifica, executa ações automáticas nos casos confiáveis e enfileira o resto pra eu revisar. **Tempo de processamento não importa** — eu fico no PC bastante, então prefiro lote a tempo real. Eu participo ativamente da redução de ruído, adicionando emails/domínios às regras, e isso é desejado, não um fallback.

## Princípios que amarram o design

1. **Nada de daemon eterno.** É um `systemd timer` + serviço `oneshot`: acorda a cada X, processa o lote, notifica, morre. `journalctl` já dá o log. O "X" é o intervalo do timer.
2. **Estado é rei.** Um SQLite local mapeando `UID → status` é a fonte da verdade sobre o que já foi tocado.
3. **Tudo começa em modo sugestão.** Ação destrutiva (lixeira) só vira automática depois que uma regra provou que acerta. Minha participação manual é o que promove regras de "sugerir" para "executar".
4. O que mata o "1 em 1" não é acumular lote, é o **`keep_alive` alto no Ollama** — o modelo fica quente na RAM entre execuções, então o custo é inferir, não recarregar.

## Fluxo de uma execução

Timer dispara (ou eu chamo na mão) → oneshot acorda → `notify-send` "analisando..." → busca **só os UIDs novos** por pasta → cada email passa pela cascata de regras (determinística, barata) → o que as regras não resolveram vai pro Ollama (já quente) → cada email recebe uma **ação sugerida** → regras de alta confiança executam direto e logam; o resto entra na **fila de revisão** → `notify-send` com o resumo ("12 analisados, 8 ruído, 4 pra revisar") → eu abro a TUI quando quiser e despacho a fila.

## Camadas (separadas e testáveis isoladamente)

**fetch** (IMAP via Bridge; `BODY.PEEK` pra não marcar lido; busca `ENVELOPE`/headers primeiro, corpo só se a IA precisar) → **storage** (SQLite: estado + log) → **rules** (cascata de precedência) → **ai** (Ollama, só pro resíduo) → **action** (executa ou enfileira, sempre registrando no log) → **delivery** (`notify-send` + TUI).

## Modelo de dados

Tabela principal `emails`, carregando o ciclo de vida:
`uid`, `uidvalidity`, `pasta`, `message_id`, `remetente`, `assunto`, `data`, `status`, `categoria`, `acao_sugerida`, `acao_aplicada`, `regra_casada`, `processado_em`.

O `status` percorre: `novo → classificado → (auto: executado | revisão: aguardando → despachado)`.

Guardar `uidvalidity` junto protege do caso raro do Bridge resetar os UIDs de uma pasta: se mudar, ressincroniza aquela pasta; se não, UID é estável.

Mais duas tabelas: **log de ações** (`uid`, `acao`, `timestamp`, dado pra reverter) que sustenta o `undo`; e **meta** (último UID visto por pasta). As **regras ficam fora do banco**, num TOML editável à mão.

## Motor de regras — onde mora a redução de ruído

Cascata com precedência clara; a **primeira regra que casar decide**. A ordem importa:

1. **Allowlist** (remetente/domínio confiável) — passa sempre, nunca é tocado. Rede de segurança contra falso positivo; por isso fica no topo.
2. **Blocklist** (remetente/domínio já marcado como ruído) — ação direta.
3. **Header `List-Unsubscribe` + termo de marketing (2 sinais)** — o header marca "email em massa", mas bulk importante (banco, recibo, GitHub) também o tem; por isso sozinho não decide. Só vira ação se casar também um termo de `[unsubscribe].exige`.
4. **Palavras-chave / padrões** no assunto ou remetente.
5. **Classificação da IA** — só o resíduo, mandando apenas assunto + primeiras linhas (rápido e privado), nunca o corpo inteiro.
6. **Default** — sem confiança, vai pra fila de revisão e não faz nada.

"Adicionar uma fonte" = "adicionar uma linha no TOML". O programa não muda, só o config cresce. Emails que o Proton já manda pra pasta sozinho nem aparecem na INBOX, então não há sobreposição.

**Loop de aprendizado:** na fila de revisão, quando eu marco algo como ruído, a TUI oferece criar a regra na hora ("sempre mandar deste remetente/domínio pra lixeira?"). Minha decisão vira regra; regra que acertou N vezes seguidas eu promovo de "sugerir" pra "executar".

## Superfície da CLI

- `apolo run` — dispara uma passada manual (a mesma que o timer chama).
- `apolo review` — abre a UI (hub Textual) pra despachar a fila e gerenciar regras/config (ver docs/ui.md).
- `apolo block <dominio|email>` / `apolo allow <dominio|email>` — adiciona à regra direto do terminal, sem abrir o TOML.
- `apolo rules` — lista/edita o que está configurado.
- `apolo status` — última execução, tamanho da fila, contadores.
- `apolo undo` — reverte a última ação via log.
- `apolo setup` — instala o timer do systemd.

## Estrutura de arquivos

```
apolo/
  cli.py              # parser de comandos, entrada única
  fetch/imap.py       # conexão Bridge, busca incremental por UID
  storage/db.py       # SQLite, estado + log
  rules/engine.py     # cascata de precedência
  rules/writer.py     # escrita das regras (block/allow + unsubscribe)
  rules/config.toml   # participação do dono vive aqui
  ai/ollama.py        # classificação do resíduo
  actions.py          # executa ou enfileira, registra no log
  notify.py           # notify-send
  scheduler.py        # controle do systemd timer (UI + setup)
  config_writer.py    # escrita parcial do .env (UI de configurações)
  ui/                 # interface de revisão em Textual (ver docs/ui.md)
  systemd/            # apolo.service + apolo.timer
```

## Restrições técnicas

- Python, stdlib-first; dependências externas só quando justificadas.
- IMAP via Proton Bridge (`127.0.0.1:1143`), `BODY.PEEK[]`, busca incremental por `UID`/`UIDVALIDITY`.
- Ollama local com `keep_alive` alto; prompt de classificação enxuto (assunto + primeiras linhas).
- Notificação via `notify-send` (libnotify/D-Bus).
- Agendamento via `systemd` timer + serviço `oneshot` (`journalctl -u apolo` pro log).
- Estado em SQLite; regras em TOML.
- Interface de revisão como TUI (textual/rich).

## Roadmap incremental (ordem sem retrabalho)

1. fetch incremental por UID + SQLite (backbone).
2. motor de regras: allowlist/blocklist/`List-Unsubscribe` (já reduz a maior parte do ruído, sem IA).
3. TUI de revisão + `block`/`allow` de terminal.
4. camada Ollama pro resíduo.
5. `notify-send` + timer.
6. promoção de regra pra automático + `undo`.

Do passo 3 já é ferramenta útil; a IA entra só quando as regras burras não dão conta.

---

## Tarefa

> **Troque esta linha conforme o que você quer da IA.**
> Para implementar: *"Implemente o passo 1 do roadmap (fetch incremental por UID + esquema SQLite) seguindo esta arquitetura. Não desvie dos princípios. Pergunte antes de adicionar qualquer dependência."*
> Para analisar/criticar: *"Analise criticamente esta arquitetura. Aponte riscos, pontos cegos, e onde a simplicidade pode estar custando robustez — sem reescrever o design, apenas avaliando."*