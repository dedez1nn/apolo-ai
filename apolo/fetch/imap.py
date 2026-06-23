"""Camada de fetch — IMAP via Proton Bridge, incremental por UID.

Princípios:
  - BODY.PEEK: nunca marca \\Seen. No passo 1 buscamos só os HEADERS
    (FROM/SUBJECT/DATE/MESSAGE-ID); o corpo só vem depois, se a IA precisar.
  - Incremental: pede UID maior que o último visto e guarda o ponteiro.
  - UIDVALIDITY: se o Bridge resetar os UIDs de uma pasta, a validade muda
    e ressincronizamos só aquela pasta. Caso contrário, UID é estável.
"""

import email
import imaplib
import ssl
from dataclasses import dataclass
from email.header import decode_header


@dataclass(frozen=True)
class FetchedEmail:
    """Metadados de cabeçalho de um email novo (sem corpo)."""

    uid: int
    message_id: str | None
    remetente: str
    assunto: str
    data: str


@dataclass(frozen=True)
class FolderResult:
    """Resultado da varredura incremental de uma pasta."""

    pasta: str
    uidvalidity: int
    resynced: bool
    novos: list[FetchedEmail]
    ultimo_uid: int  # maior UID visto agora (vira o novo ponteiro)


def _decode_str(value: str | None) -> str:
    """Decodifica headers MIME (=?utf-8?...?=) pra texto legível."""
    if value is None:
        return ""
    parts = decode_header(value)
    out: list[str] = []
    for part, charset in parts:
        if isinstance(part, bytes):
            out.append(part.decode(charset or "utf-8", errors="replace"))
        else:
            out.append(part)
    return "".join(out)


class BridgeClient:
    """Conexão com o Proton Bridge. Use como context manager."""

    def __init__(self, host: str, port: int, security: str = "STARTTLS"):
        self.host = host
        self.port = port
        self.security = security.upper()
        self._imap: imaplib.IMAP4 | None = None

    def __enter__(self) -> "BridgeClient":
        self._imap = imaplib.IMAP4(self.host, self.port)
        if self.security == "STARTTLS":
            # O Bridge usa um cert self-signed em loopback; não dá pra verificar
            # contra uma CA e não faz sentido em 127.0.0.1. Conexão local.
            ctx = ssl.create_default_context()
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
            self._imap.starttls(ssl_context=ctx)
        return self

    def __exit__(self, *exc) -> None:
        if self._imap is not None:
            try:
                self._imap.logout()
            except Exception:
                pass
            self._imap = None

    def login(self, username: str, password: str) -> None:
        assert self._imap is not None
        self._imap.login(username, password)

    def _select_readonly(self, pasta: str) -> int:
        """Seleciona a pasta em modo readonly e devolve o UIDVALIDITY."""
        assert self._imap is not None
        # readonly=True (EXAMINE) é cinto-e-suspensório junto do BODY.PEEK:
        # garante que nada é marcado como lido nesta varredura.
        typ, _ = self._imap.select(pasta, readonly=True)
        if typ != "OK":
            raise RuntimeError(f"não consegui selecionar a pasta {pasta!r}")
        # response() devolve (NOME_DA_RESPOSTA, [dados]) — o 1º item é o nome,
        # não um status "OK". Basta checar que veio dado.
        _, data = self._imap.response("UIDVALIDITY")
        if not data or data[0] is None:
            raise RuntimeError(f"sem UIDVALIDITY pra pasta {pasta!r}")
        return int(data[0])

    def fetch_new(self, pasta: str, known_uidvalidity: int | None, last_uid: int) -> FolderResult:
        """Busca os UIDs novos de uma pasta desde o último ponteiro.

        Se o UIDVALIDITY mudou (ou não tínhamos um), sinaliza resync e trata
        last_uid como 0, pegando tudo do zero.
        """
        assert self._imap is not None
        uidvalidity = self._select_readonly(pasta)

        resynced = known_uidvalidity is None or known_uidvalidity != uidvalidity
        search_from = 0 if resynced else last_uid

        # 'UID n:*' SEMPRE retorna ao menos a maior mensagem, mesmo sem nada novo,
        # porque '*' casa o maior UID. Por isso filtramos uid > search_from depois.
        typ, data = self._imap.uid("search", None, f"UID {search_from + 1}:*")
        if typ != "OK":
            raise RuntimeError(f"falha no UID SEARCH em {pasta!r}")

        raw_ids = data[0].split() if data and data[0] else []
        candidate_uids = sorted(int(x) for x in raw_ids)
        new_uids = [u for u in candidate_uids if u > search_from]

        novos = [self._fetch_headers(uid) for uid in new_uids]
        novos = [m for m in novos if m is not None]

        max_uid = max((m.uid for m in novos), default=search_from)
        return FolderResult(
            pasta=pasta,
            uidvalidity=uidvalidity,
            resynced=resynced,
            novos=novos,
            ultimo_uid=max_uid,
        )

    def _fetch_headers(self, uid: int) -> FetchedEmail | None:
        """Busca só os headers de interesse via BODY.PEEK (não marca lido)."""
        assert self._imap is not None
        typ, data = self._imap.uid(
            "fetch",
            str(uid),
            "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID)])",
        )
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None

        raw_headers = data[0][1]
        msg = email.message_from_bytes(raw_headers)
        return FetchedEmail(
            uid=uid,
            message_id=(msg.get("Message-ID") or None),
            remetente=_decode_str(msg.get("From")),
            assunto=_decode_str(msg.get("Subject")),
            data=msg.get("Date", ""),
        )
