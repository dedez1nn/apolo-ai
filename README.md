# Apolo

Triador pessoal de emails. Roda em lote (não é daemon eterno), reduz ruído de
forma determinística e enfileira o resíduo pra revisão manual. Ferramenta de uso
pessoal, na própria máquina — lê o Proton Mail via **Proton Bridge** (IMAP local).

A arquitetura completa e os princípios de design estão em [`apolo.md`](apolo.md).

## Estado atual — passo 1 do roadmap

Implementado o **backbone**: fetch incremental por UID + esquema SQLite.

```
apolo/
  __init__.py
  config.py          # credenciais/Bridge via env + .env (parser stdlib)
  cli.py             # apolo run / apolo status
  fetch/imap.py      # conexão Bridge, busca incremental por UID (BODY.PEEK)
  storage/db.py      # SQLite: emails + acoes (log p/ undo) + meta
```

Sem dependências externas — tudo stdlib (`imaplib`, `sqlite3`, `email`, `ssl`).

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
python -m apolo.cli run      # uma passada: busca UIDs novos e grava como 'novo'
python -m apolo.cli status   # última execução e contadores por status
```

## Como funciona o fetch incremental

- Seleciona a pasta em modo **readonly** (`EXAMINE`) e usa **`BODY.PEEK`**, então
  a varredura nunca marca mensagens como lidas.
- Guarda o último UID visto por pasta (tabela `meta`) e busca só `UID último+1:*`.
  O quirk do IMAP de `n:*` sempre devolver a maior mensagem é tratado filtrando
  `uid > último_visto`.
- Guarda o `UIDVALIDITY` por pasta. Se o Bridge resetar os UIDs daquela pasta, a
  validade muda e o Apolo ressincroniza só aquela pasta.

## Próximos passos (roadmap)

2. Motor de regras: allowlist/blocklist/`List-Unsubscribe` (TOML editável à mão).
3. TUI de revisão + `apolo block`/`apolo allow`.
4. Camada Ollama pro resíduo.
5. `notify-send` + systemd timer.
6. Promoção de regra pra automático + `apolo undo`.
