# Apolo

Triador pessoal de emails. Roda em lote (não é daemon eterno), reduz ruído de
forma determinística e enfileira o resíduo pra revisão manual. Ferramenta de uso
pessoal, na própria máquina — lê o Proton Mail via **Proton Bridge** (IMAP local).

A arquitetura completa e os princípios de design estão em [`apolo.md`](apolo.md).

## Estado atual — passos 1, 1.5 e 2 do roadmap

Implementado o **backbone** (fetch incremental + SQLite), a **limpeza de corpo
HTML/CSS** e o **motor de regras determinístico**.

```
apolo/
  __init__.py
  config.py            # credenciais/Bridge via env + .env (parser stdlib)
  cli.py               # apolo run / apolo status
  clean.py             # HTML/CSS -> texto limpo (passo 1.5)
  fetch/imap.py        # conexão Bridge, busca incremental por UID (BODY.PEEK)
  storage/db.py        # SQLite: emails + acoes (log p/ undo) + meta
  rules/engine.py      # cascata de precedência (passo 2)
  rules/config.toml    # regras editáveis à mão
```

Sem dependências externas — tudo stdlib (`imaplib`, `sqlite3`, `email`, `ssl`,
`html.parser`, `tomllib`).

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

## Uso

```bash
python -m apolo.cli run      # busca UIDs novos, classifica pela cascata e grava
python -m apolo.cli status   # última execução, contadores e ações sugeridas
```

## Motor de regras (passo 2)

A cada email novo a cascata avalia de cima pra baixo e a **primeira regra que
casar decide** (`apolo/rules/engine.py`), tudo sem IA:

1. **allowlist** (remetente/domínio confiável) → mantém, nunca é tocado.
2. **blocklist** (ruído conhecido) → sugere lixeira.
3. **`List-Unsubscribe`** → newsletter/marketing, ação conforme o config.
4. **palavras-chave** no assunto/remetente → ação por grupo.
5. *(IA — passo 4, ainda não)*
6. **default** → sem confiança, vai pra fila de revisão.

Domínio casa subdomínio (`loja-exemplo.com.br` pega `promo.loja-exemplo.com.br`). As regras
ficam num TOML editável à mão — `apolo/rules/config.toml` (ou aponte outro com
`APOLO_RULES_PATH`). "Adicionar uma fonte" = adicionar uma linha lá.

**Tudo começa em modo sugestão.** No passo 2 nada é apagado: `manter` é decisão
terminal e o resto entra na fila de revisão (`aguardando`) com a ação sugerida.
A execução automática só chega quando uma regra for promovida (passo 6).

## Como funciona o fetch incremental

- Seleciona a pasta em modo **readonly** (`EXAMINE`) e usa **`BODY.PEEK`**, então
  a varredura nunca marca mensagens como lidas.
- Guarda o último UID visto por pasta (tabela `meta`) e busca só `UID último+1:*`.
  O quirk do IMAP de `n:*` sempre devolver a maior mensagem é tratado filtrando
  `uid > último_visto`.
- Guarda o `UIDVALIDITY` por pasta. Se o Bridge resetar os UIDs daquela pasta, a
  validade muda e o Apolo ressincroniza só aquela pasta.

## Próximos passos (roadmap)

3. TUI de revisão + `apolo block`/`apolo allow`.
4. Camada Ollama pro resíduo.
5. `notify-send` + systemd timer.
6. Promoção de regra pra automático + `apolo undo`.
