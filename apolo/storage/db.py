"""Estado do Apolo em SQLite — a fonte da verdade.

Três tabelas:
  - emails: o ciclo de vida de cada mensagem (UID -> status).
  - acoes:  log append-only de ações, com payload pra reverter (undo).
  - meta:   último UID visto e UIDVALIDITY por pasta (busca incremental).

UID só é único dentro de (pasta, uidvalidity); por isso a PK é composta.
As regras NÃO vivem aqui — ficam num TOML editável à mão (passo 2).
"""

import sqlite3
from contextlib import contextmanager
from datetime import datetime, timezone
from email.utils import parsedate_to_datetime
from pathlib import Path

# Estados válidos do ciclo de vida (apolo.md):
# novo -> classificado -> (auto: executado | revisão: aguardando -> despachado)
# removido: saiu da pasta de origem por fora do Apolo (lixeira/arquivado
# direto no Gmail/Proton) — descoberto na reconciliação da próxima varredura.
STATUS_NOVO = "novo"
STATUS_CLASSIFICADO = "classificado"
STATUS_EXECUTADO = "executado"
STATUS_AGUARDANDO = "aguardando"
STATUS_DESPACHADO = "despachado"
STATUS_REMOVIDO = "removido"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
    conta          TEXT    NOT NULL DEFAULT 'proton',
    pasta          TEXT    NOT NULL,
    uidvalidity    INTEGER NOT NULL,
    uid            INTEGER NOT NULL,
    message_id     TEXT,
    remetente      TEXT,
    assunto        TEXT,
    data           TEXT,
    status         TEXT    NOT NULL DEFAULT 'novo',
    categoria      TEXT,
    acao_sugerida  TEXT,
    acao_aplicada  TEXT,
    regra_casada   TEXT,
    processado_em  TEXT,
    provider_id    TEXT,
    origem_despacho TEXT,
    favorito       INTEGER NOT NULL DEFAULT 0,
    PRIMARY KEY (pasta, uidvalidity, uid)
);

CREATE INDEX IF NOT EXISTS idx_emails_status ON emails (status);

CREATE TABLE IF NOT EXISTS acoes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
    conta         TEXT    NOT NULL DEFAULT 'proton',
    pasta         TEXT    NOT NULL,
    uidvalidity   INTEGER NOT NULL,
    uid           INTEGER NOT NULL,
    acao          TEXT    NOT NULL,
    timestamp     TEXT    NOT NULL,
    dado_reverter TEXT
);

CREATE TABLE IF NOT EXISTS meta (
    pasta       TEXT    PRIMARY KEY,
    uidvalidity INTEGER NOT NULL,
    ultimo_uid  INTEGER NOT NULL DEFAULT 0
);

CREATE TABLE IF NOT EXISTS sugestoes_ignoradas (
    chave      TEXT PRIMARY KEY,
    criado_em  TEXT NOT NULL
);
"""


def _migrate(conn: sqlite3.Connection) -> None:
    """Migrações incrementais — idempotentes, safe pra dados existentes."""
    cols_emails = {r[1] for r in conn.execute("PRAGMA table_info(emails)").fetchall()}
    if "conta" not in cols_emails:
        conn.execute("ALTER TABLE emails ADD COLUMN conta TEXT NOT NULL DEFAULT 'proton'")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_emails_conta ON emails (conta)")
    if "provider_id" not in cols_emails:
        conn.execute("ALTER TABLE emails ADD COLUMN provider_id TEXT")
    if "origem_despacho" not in cols_emails:
        conn.execute("ALTER TABLE emails ADD COLUMN origem_despacho TEXT")
    if "favorito" not in cols_emails:
        conn.execute("ALTER TABLE emails ADD COLUMN favorito INTEGER NOT NULL DEFAULT 0")

    cols_acoes = {r[1] for r in conn.execute("PRAGMA table_info(acoes)").fetchall()}
    if "conta" not in cols_acoes:
        conn.execute("ALTER TABLE acoes ADD COLUMN conta TEXT NOT NULL DEFAULT 'proton'")

    conn.commit()


def _now() -> str:
    """ISO 8601 em UTC — ordenável e sem ambiguidade de fuso."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


_DATA_MIN = datetime.min.replace(tzinfo=timezone.utc)


def _data_ordenavel(raw: str | None) -> datetime:
    """Header Date (RFC 2822) -> datetime comparável. Não dá pra ordenar por
    string (meses por nome), nem confiar em UID como proxy de data — UID é
    ordem de descoberta, não a data que o remetente colocou no header (varia
    com resync, full-scan, atraso de entrega etc). Sem header ou ilegível,
    cai pro mínimo (fica no fim da lista "mais recentes primeiro").
    """
    if not raw:
        return _DATA_MIN
    try:
        dt = parsedate_to_datetime(raw)
    except (TypeError, ValueError, IndexError):
        return _DATA_MIN
    if dt is None:
        return _DATA_MIN
    if dt.tzinfo is None:
        dt = dt.replace(tzinfo=timezone.utc)
    return dt


class Storage:
    """Camada fina sobre o SQLite. Use como context manager."""

    def __init__(self, db_path: Path):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self.conn = sqlite3.connect(self.db_path)
        self.conn.row_factory = sqlite3.Row
        # WAL deixa leitura/escrita mais tranquilas; foreign_keys por higiene.
        self.conn.execute("PRAGMA journal_mode=WAL")
        self.conn.execute("PRAGMA foreign_keys=ON")
        self.conn.executescript(_SCHEMA)
        self.conn.commit()
        _migrate(self.conn)

    # ----- ciclo de vida do context manager -----
    def __enter__(self) -> "Storage":
        return self

    def __exit__(self, *exc) -> None:
        self.close()

    def close(self) -> None:
        self.conn.close()

    @contextmanager
    def _tx(self):
        try:
            yield self.conn
            self.conn.commit()
        except Exception:
            self.conn.rollback()
            raise

    # ----- meta: ponteiro incremental por pasta -----
    def get_folder_meta(self, pasta: str) -> tuple[int, int] | None:
        """Retorna (uidvalidity, ultimo_uid) ou None se a pasta é nova."""
        row = self.conn.execute(
            "SELECT uidvalidity, ultimo_uid FROM meta WHERE pasta = ?", (pasta,)
        ).fetchone()
        if row is None:
            return None
        return row["uidvalidity"], row["ultimo_uid"]

    def set_folder_meta(self, pasta: str, uidvalidity: int, ultimo_uid: int) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO meta (pasta, uidvalidity, ultimo_uid)
                VALUES (?, ?, ?)
                ON CONFLICT(pasta) DO UPDATE SET
                    uidvalidity = excluded.uidvalidity,
                    ultimo_uid  = excluded.ultimo_uid
                """,
                (pasta, uidvalidity, ultimo_uid),
            )

    def decided_message_ids(self, pasta: str) -> dict[str, tuple[str, str | None]]:
        """message_id -> (acao_aplicada, origem_despacho) dos emails JÁ
        despachados nesta pasta.

        Usado pra preservar decisões através de um resync: quando o Bridge troca
        o UIDVALIDITY, os UIDs mudam mas o message_id não. Um email que o dono já
        mandou pra lixeira / manteve não deve voltar pra fila só por isso — e se
        foi um auto-envio, a origem é preservada também (senão ele some da tela
        "Emails de ruído" só por causa do resync). Capture ANTES de reset_folder
        (que apaga as linhas).
        """
        rows = self.conn.execute(
            """
            SELECT message_id, acao_aplicada, origem_despacho FROM emails
             WHERE pasta = ? AND status = ? AND message_id IS NOT NULL
            """,
            (pasta, STATUS_DESPACHADO),
        ).fetchall()
        return {
            r["message_id"].strip(): (r["acao_aplicada"] or "", r["origem_despacho"])
            for r in rows
            if (r["message_id"] or "").strip()
        }

    def reset_folder(self, pasta: str) -> None:
        """UIDVALIDITY mudou: descarta o estado daquela pasta pra ressincronizar."""
        with self._tx() as conn:
            conn.execute("DELETE FROM emails WHERE pasta = ?", (pasta,))
            conn.execute("DELETE FROM meta WHERE pasta = ?", (pasta,))

    # ----- emails -----
    def email_exists(self, pasta: str, uidvalidity: int, uid: int) -> bool:
        row = self.conn.execute(
            "SELECT 1 FROM emails WHERE pasta=? AND uidvalidity=? AND uid=?",
            (pasta, uidvalidity, uid),
        ).fetchone()
        return row is not None

    def insert_email(
        self,
        *,
        pasta: str,
        uidvalidity: int,
        uid: int,
        message_id: str | None,
        remetente: str | None,
        assunto: str | None,
        data: str | None,
        status: str = STATUS_NOVO,
        conta: str = "proton",
        provider_id: str | None = None,
        favorito: bool = False,
    ) -> bool:
        """Insere um email novo. Idempotente: ignora se o UID já existe.

        Retorna True se inseriu de fato, False se já estava lá.
        """
        with self._tx() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO emails
                    (conta, pasta, uidvalidity, uid, message_id, remetente,
                     assunto, data, status, processado_em, provider_id, favorito)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    conta,
                    pasta,
                    uidvalidity,
                    uid,
                    message_id,
                    remetente,
                    assunto,
                    data,
                    status,
                    _now(),
                    provider_id,
                    int(favorito),
                ),
            )
            return cur.rowcount > 0

    def classify_email(
        self,
        *,
        pasta: str,
        uidvalidity: int,
        uid: int,
        status: str,
        categoria: str,
        acao_sugerida: str,
        regra_casada: str,
    ) -> None:
        """Grava o resultado da cascata de regras num email já existente."""
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE emails
                   SET status = ?, categoria = ?, acao_sugerida = ?,
                       regra_casada = ?, processado_em = ?
                 WHERE pasta = ? AND uidvalidity = ? AND uid = ?
                """,
                (
                    status,
                    categoria,
                    acao_sugerida,
                    regra_casada,
                    _now(),
                    pasta,
                    uidvalidity,
                    uid,
                ),
            )

    def status_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT status, COUNT(*) AS n FROM emails GROUP BY status"
        ).fetchall()
        return {row["status"]: row["n"] for row in rows}

    def acao_counts(self) -> dict[str, int]:
        rows = self.conn.execute(
            "SELECT acao_sugerida, COUNT(*) AS n FROM emails "
            "WHERE acao_sugerida IS NOT NULL GROUP BY acao_sugerida"
        ).fetchall()
        return {row["acao_sugerida"]: row["n"] for row in rows}

    def last_processed_at(self) -> str | None:
        row = self.conn.execute(
            "SELECT MAX(processado_em) AS m FROM emails"
        ).fetchone()
        return row["m"] if row else None

    # ----- sugestões (histórico de despacho -> candidatas a regra) -----
    def dispatched_rows(self) -> list[sqlite3.Row]:
        """Matéria-prima do motor de sugestões (apolo.suggest): tudo que já foi
        despachado, com a ação que o dono efetivamente aplicou.
        """
        return self.conn.execute(
            """
            SELECT remetente, assunto, acao_aplicada, processado_em
              FROM emails
             WHERE status = ? AND acao_aplicada IS NOT NULL
            """,
            (STATUS_DESPACHADO,),
        ).fetchall()

    def sugestoes_ignoradas(self) -> set[str]:
        rows = self.conn.execute("SELECT chave FROM sugestoes_ignoradas").fetchall()
        return {r["chave"] for r in rows}

    def ignorar_sugestao(self, chave: str) -> None:
        with self._tx() as conn:
            conn.execute(
                "INSERT OR IGNORE INTO sugestoes_ignoradas (chave, criado_em) VALUES (?, ?)",
                (chave, _now()),
            )

    # ----- fila de revisão (TUI) -----
    def fetch_queue(self) -> list[sqlite3.Row]:
        """Emails aguardando revisão, mais recentes primeiro pela data real do
        header (não por UID/conta/pasta — UID não é proxy confiável de data,
        e ordenar por conta antes misturava mal contas diferentes na fila).
        """
        rows = self.conn.execute(
            """
            SELECT conta, pasta, uidvalidity, uid, message_id, remetente, assunto,
                   data, categoria, acao_sugerida, regra_casada, provider_id, favorito
              FROM emails
             WHERE status = ?
            """,
            (STATUS_AGUARDANDO,),
        ).fetchall()
        return sorted(rows, key=lambda r: _data_ordenavel(r["data"]), reverse=True)

    def count_queue(self) -> int:
        row = self.conn.execute(
            "SELECT COUNT(*) AS n FROM emails WHERE status = ?", (STATUS_AGUARDANDO,)
        ).fetchone()
        return row["n"] if row else 0

    def mark_dispatched(
        self, *, pasta: str, uidvalidity: int, uid: int, acao_aplicada: str, origem: str | None = None
    ) -> None:
        """Email saiu da fila: registra a ação efetivamente aplicada.

        `origem` marca "auto" quando foi o auto-envio (cascata determinística,
        sem passar pela fila — ver `apolo.actions.dispatch_lixeira_imap`/
        `dispatch_lixeira_gmail`) — é o que distingue um despacho automático de
        um manual (mesmo `acao_aplicada`) pra tela "Emails de ruído". None pro
        despacho manual (fila normal).
        """
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE emails
                   SET status = ?, acao_aplicada = ?, processado_em = ?, origem_despacho = ?
                 WHERE pasta = ? AND uidvalidity = ? AND uid = ?
                """,
                (STATUS_DESPACHADO, acao_aplicada, _now(), origem, pasta, uidvalidity, uid),
            )

    # ----- "emails de ruído" (auto-envio pra lixeira sem passar pela fila) -----
    def trashed_rows(self) -> list[sqlite3.Row]:
        """Despachados como lixeira PELO AUTO-ENVIO (`origem_despacho='auto'`
        — não inclui lixeira despachada manualmente pela fila normal), mais
        perto de expirar primeiro (`processado_em` ascendente) — pra tela
        "Emails de ruído".
        """
        rows = self.conn.execute(
            """
            SELECT conta, pasta, uidvalidity, uid, message_id, remetente, assunto,
                   data, categoria, acao_sugerida, acao_aplicada, regra_casada,
                   processado_em, provider_id
              FROM emails
             WHERE status = ? AND acao_aplicada = ? AND origem_despacho = ?
            """,
            (STATUS_DESPACHADO, "lixeira", "auto"),
        ).fetchall()
        return sorted(rows, key=lambda r: r["processado_em"] or "", reverse=False)

    def mark_restaurado(self, *, pasta: str, uidvalidity: int, uid: int) -> None:
        """Email tirado da lixeira (ver `apolo.actions.restaurar_email`): volta
        pra 'aguardando' — reaparece na fila normal pro dono decidir de novo,
        sem risco de cair de novo no auto-envio (a próxima sync ignora UIDs já
        existentes no banco).
        """
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE emails
                   SET status = ?, acao_aplicada = NULL, processado_em = NULL, origem_despacho = NULL
                 WHERE pasta = ? AND uidvalidity = ? AND uid = ?
                """,
                (STATUS_AGUARDANDO, pasta, uidvalidity, uid),
            )

    def stuck_default_rows(self) -> list[sqlite3.Row]:
        """Pendentes cuja cascata caiu no 'default' mas nunca passaram pela IA.

        Cobre o caso em que o Ollama estava fora do ar (ou a IA desligada) na
        passada em que o email chegou: só o lote recém-buscado daquela vez
        passa pela IA (ver cmd_run), então esses UIDs antigos nunca são
        revisitados a menos que alguém escaneie por `regra_casada = 'default'`
        de novo — é isso que esta consulta faz.
        """
        return self.conn.execute(
            """
            SELECT conta, pasta, uidvalidity, uid, remetente, assunto, provider_id
              FROM emails
             WHERE status = ? AND regra_casada = 'default'
            """,
            (STATUS_AGUARDANDO,),
        ).fetchall()

    # ----- reconciliação (passo: a pasta real pode ter mudado por fora) -----
    def pending_rows(self, pasta: str) -> list[sqlite3.Row]:
        """Emails dessa pasta ainda 'vivos' (nem despachados, nem já marcados como
        removidos) — candidatos a checar se ainda existem na pasta de origem.
        """
        return self.conn.execute(
            """
            SELECT uid, uidvalidity, provider_id, favorito FROM emails
             WHERE pasta = ? AND status NOT IN (?, ?)
            """,
            (pasta, STATUS_DESPACHADO, STATUS_REMOVIDO),
        ).fetchall()

    def update_favorito(self, *, pasta: str, uidvalidity: int, uid: int, favorito: bool) -> None:
        """Corrige o `favorito` de um email já sincronizado — pego só no insert
        original, então quem favorita depois precisa dessa reconferência (ver
        `apolo.sync.refresh_favoritos_imap`/`refresh_favoritos_gmail`)."""
        with self._tx() as conn:
            conn.execute(
                "UPDATE emails SET favorito = ? WHERE pasta = ? AND uidvalidity = ? AND uid = ?",
                (int(favorito), pasta, uidvalidity, uid),
            )

    def mark_removed(self, *, pasta: str, uidvalidity: int, uid: int) -> None:
        """Email saiu da pasta de origem por fora do Apolo (lixeira/arquivado
        direto no Gmail/Proton): sai da fila sem virar 'despachado' (o Apolo não
        fez nada — só percebeu que sumiu).
        """
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE emails
                   SET status = ?, acao_aplicada = ?, processado_em = ?
                 WHERE pasta = ? AND uidvalidity = ? AND uid = ?
                """,
                (STATUS_REMOVIDO, "removido_externo", _now(), pasta, uidvalidity, uid),
            )

    # ----- log de ações (sustenta o undo, passo 6) -----
    def emails_sem_remetente(self, conta_prefix: str) -> list[sqlite3.Row]:
        """Retorna emails de contas Gmail com remetente vazio e provider_id preenchido."""
        return self.conn.execute(
            """
            SELECT pasta, uidvalidity, uid, provider_id
              FROM emails
             WHERE conta LIKE ?
               AND provider_id IS NOT NULL
               AND (remetente IS NULL OR remetente = '')
            """,
            (conta_prefix + "%",),
        ).fetchall()

    def update_email_headers(
        self,
        *,
        pasta: str,
        uidvalidity: int,
        uid: int,
        remetente: str,
        assunto: str,
        data: str,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                UPDATE emails
                   SET remetente = ?, assunto = ?, data = ?
                 WHERE pasta = ? AND uidvalidity = ? AND uid = ?
                """,
                (remetente, assunto, data, pasta, uidvalidity, uid),
            )

    def log_action(
        self,
        *,
        pasta: str,
        uidvalidity: int,
        uid: int,
        acao: str,
        dado_reverter: str | None = None,
    ) -> None:
        with self._tx() as conn:
            conn.execute(
                """
                INSERT INTO acoes (pasta, uidvalidity, uid, acao, timestamp, dado_reverter)
                VALUES (?, ?, ?, ?, ?, ?)
                """,
                (pasta, uidvalidity, uid, acao, _now(), dado_reverter),
            )
