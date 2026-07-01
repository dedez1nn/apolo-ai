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
import sys
import time
from email.header import decode_header
from email.message import Message

from apolo.fetch import FetchedEmail, FolderResult  # noqa: F401


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

    def __init__(
        self,
        host: str,
        port: int,
        security: str = "STARTTLS",
        timeout: float = 30.0,
        connect_wait: float = 60.0,
    ):
        self.host = host
        self.port = port
        self.security = security.upper()
        # Sem timeout, um Bridge travado/rate-limited pendura login/copy/expunge
        # pra sempre — e o terminal "congela". Com timeout, falha limpa.
        self.timeout = timeout
        # Quanto tempo (s) esperar o Bridge aceitar conexão antes de desistir.
        # O Bridge sobe alguns segundos depois do login/boot, então o serviço
        # agendado e a TUI tentam conectar antes da porta 1143 abrir e levam
        # "Connection refused". Em vez de abortar, reentamos até o Bridge subir.
        self.connect_wait = connect_wait
        self._imap: imaplib.IMAP4 | None = None

    def _connect(self) -> imaplib.IMAP4:
        """Conecta na porta IMAP, reentando enquanto o Bridge ainda não subiu.

        Reenta só em recusa/timeout de conexão (Bridge fora); outros erros sobem
        na hora. Backoff de 1s→5s, desistindo após `connect_wait` segundos com
        uma mensagem clara em vez de um traceback de ConnectionRefusedError.
        """
        deadline = time.monotonic() + self.connect_wait
        delay = 1.0
        avisou = False
        while True:
            try:
                return imaplib.IMAP4(self.host, self.port, timeout=self.timeout)
            except (ConnectionRefusedError, TimeoutError, OSError) as e:
                if time.monotonic() >= deadline:
                    raise ConnectionRefusedError(
                        f"Proton Bridge não respondeu em {self.host}:{self.port} "
                        f"após {self.connect_wait:.0f}s — ele está rodando?"
                    ) from e
                if not avisou:
                    print(
                        f"aguardando o Proton Bridge em {self.host}:{self.port}…",
                        file=sys.stderr,
                    )
                    avisou = True
                time.sleep(delay)
                delay = min(delay * 1.5, 5.0)

    def __enter__(self) -> "BridgeClient":
        self._imap = self._connect()
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

    def copy_to(self, pasta: str, uid: int, destino: str) -> None:
        """Seleciona a pasta (rw) e copia o UID pra `destino`, marcando \\Deleted.

        O Bridge não anuncia MOVE, então a remoção é COPY + \\Deleted; o EXPUNGE
        fica pra expunge() (uma vez por pasta). Mover pra Trash é reversível.
        """
        assert self._imap is not None
        typ, _ = self._imap.select(pasta, readonly=False)
        if typ != "OK":
            raise RuntimeError(f"não consegui selecionar {pasta!r} pra escrita")
        typ, _ = self._imap.uid("COPY", str(uid), destino)
        if typ != "OK":
            raise RuntimeError(f"COPY do UID {uid} pra {destino!r} falhou")
        typ, _ = self._imap.uid("STORE", str(uid), "+FLAGS", "(\\Deleted)")
        if typ != "OK":
            raise RuntimeError(f"STORE \\Deleted no UID {uid} falhou")

    def expunge(self, pasta: str) -> None:
        """EXPUNGE na pasta — efetiva a remoção dos marcados \\Deleted."""
        assert self._imap is not None
        typ, _ = self._imap.select(pasta, readonly=False)
        if typ != "OK":
            raise RuntimeError(f"não consegui selecionar {pasta!r} pra expunge")
        self._imap.expunge()

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

    def list_uids(self, pasta: str, limit: int) -> tuple[list[int], int]:
        """Lista até `limit` UIDs mais recentes da pasta (uma única busca IMAP)
        + a UIDVALIDITY atual, ignorando o ponteiro incremental (last_uid) —
        descobre o que um sync anterior limitado deixou de fora.

        Não busca headers aqui: o chamador filtra contra o banco e usa
        `fetch_header` só pros UIDs genuinamente novos (ver apolo.sync), assim
        o progresso aparece ao vivo em vez de um lote silencioso.
        """
        assert self._imap is not None
        uidvalidity = self._select_readonly(pasta)

        typ, data = self._imap.uid("search", None, "ALL")
        if typ != "OK":
            raise RuntimeError(f"falha no UID SEARCH (sync completo) em {pasta!r}")

        raw_ids = data[0].split() if data and data[0] else []
        all_uids = sorted(int(x) for x in raw_ids)
        uids = all_uids[-limit:] if limit > 0 else all_uids
        return uids, uidvalidity

    def fetch_header(self, uid: int) -> FetchedEmail | None:
        """Busca o header de um único UID (a pasta já deve estar selecionada,
        via `list_uids` ou `fetch_new` na mesma sessão)."""
        return self._fetch_headers(uid)

    def uids_presentes(self, pasta: str, uids: list[int]) -> set[int]:
        """Reconciliação: quais desses UIDs ainda existem na pasta agora.

        Os que sumiram saíram por fora do Apolo (lixeira/movido/apagado direto
        no Proton). Um único UID SEARCH cobre o lote inteiro — sem round-trip
        por mensagem.
        """
        if not uids:
            return set()
        assert self._imap is not None
        self._select_readonly(pasta)
        seq = ",".join(str(u) for u in uids)
        typ, data = self._imap.uid("search", None, f"UID {seq}")
        if typ != "OK":
            raise RuntimeError(f"falha no UID SEARCH (reconciliação) em {pasta!r}")
        raw_ids = data[0].split() if data and data[0] else []
        return {int(x) for x in raw_ids}

    def fetch_message_from(self, pasta: str, uid: int) -> Message | None:
        """Seleciona a pasta (readonly) e busca a mensagem inteira (não marca lido).

        Conveniência pra buscar um UID avulso fora de um fetch_new — ex.: a UI
        puxando o corpo de um email só pra extrair o código de confirmação.
        """
        self._select_readonly(pasta)
        return self.fetch_message(uid)

    def fetch_message(self, uid: int) -> Message | None:
        """Busca a mensagem inteira via BODY.PEEK[] (não marca lido).

        Só pro resíduo que a IA precisa ler (passo 4). Combine com
        apolo.clean.message_to_text pra obter o texto limpo. Requer a pasta já
        selecionada (chame depois de fetch_new na mesma sessão).
        """
        assert self._imap is not None
        typ, data = self._imap.uid("fetch", str(uid), "(BODY.PEEK[])")
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            return None
        return email.message_from_bytes(data[0][1])

    def _fetch_headers(self, uid: int) -> FetchedEmail | None:
        """Busca só os headers de interesse via BODY.PEEK (não marca lido)."""
        assert self._imap is not None
        typ, data = self._imap.uid(
            "fetch",
            str(uid),
            "(BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID LIST-UNSUBSCRIBE)])",
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
            # str() porque headers com codificação atípica podem vir como Header.
            list_unsubscribe=str(msg.get("List-Unsubscribe") or ""),
        )
