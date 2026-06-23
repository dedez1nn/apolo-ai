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
from pathlib import Path

# Estados válidos do ciclo de vida (apolo.md):
# novo -> classificado -> (auto: executado | revisão: aguardando -> despachado)
STATUS_NOVO = "novo"
STATUS_CLASSIFICADO = "classificado"
STATUS_EXECUTADO = "executado"
STATUS_AGUARDANDO = "aguardando"
STATUS_DESPACHADO = "despachado"

_SCHEMA = """
CREATE TABLE IF NOT EXISTS emails (
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
    PRIMARY KEY (pasta, uidvalidity, uid)
);

CREATE INDEX IF NOT EXISTS idx_emails_status ON emails (status);

CREATE TABLE IF NOT EXISTS acoes (
    id            INTEGER PRIMARY KEY AUTOINCREMENT,
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
"""


def _now() -> str:
    """ISO 8601 em UTC — ordenável e sem ambiguidade de fuso."""
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


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
    ) -> bool:
        """Insere um email novo. Idempotente: ignora se o UID já existe.

        Retorna True se inseriu de fato, False se já estava lá.
        """
        with self._tx() as conn:
            cur = conn.execute(
                """
                INSERT OR IGNORE INTO emails
                    (pasta, uidvalidity, uid, message_id, remetente,
                     assunto, data, status, processado_em)
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    pasta,
                    uidvalidity,
                    uid,
                    message_id,
                    remetente,
                    assunto,
                    data,
                    status,
                    _now(),
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

    # ----- log de ações (sustenta o undo, passo 6) -----
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
