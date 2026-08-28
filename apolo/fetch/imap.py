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
import logging
import ssl
import sys
import time
from email.header import decode_header
from email.message import Message

from apolo.fetch import FetchedEmail, FolderResult  # noqa: F401

logger = logging.getLogger("apolo.fetch.imap")

# Falhas seguidas de fetch por UID antes de desistir do resto da pasta nesta
# passada — sintoma de rate limit ou conexão morta no meio do lote. Parar aqui
# em vez de estourar a exceção evita perder também os UIDs já buscados com
# sucesso: o ponteiro (last_uid) só avança até o que deu certo, e o restante
# é retomado automaticamente no próximo ciclo do timer.
MAX_FALHAS_SEGUIDAS = 5


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
    """Conexão IMAP. Use como context manager.

    Apesar do nome (nasceu só pro Proton Bridge), serve pra qualquer servidor
    IMAP: `security="STARTTLS"` (Bridge, loopback) ou `security="SSL"` (TLS
    direto, ex.: outlook.office365.com:993) — ver `_build_ssl_context`.
    """

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

    def _is_loopback(self) -> bool:
        return self.host in ("127.0.0.1", "localhost", "::1")

    def _build_ssl_context(self) -> ssl.SSLContext:
        """Verificação real por padrão. Só é desligada contra o Bridge local
        (loopback): cert self-signed, sem CA pra validar e sem sentido fazê-lo
        em 127.0.0.1. Contra um servidor de verdade (ex.: Outlook), o
        certificado é validado normalmente."""
        ctx = ssl.create_default_context()
        if self._is_loopback():
            ctx.check_hostname = False
            ctx.verify_mode = ssl.CERT_NONE
        return ctx

    def _connect(self) -> imaplib.IMAP4:
        """Conecta na porta IMAP, reentando enquanto o Bridge ainda não subiu.

        Reenta só em recusa/timeout de conexão (Bridge fora); outros erros sobem
        na hora. Backoff de 1s→5s, desistindo após `connect_wait` segundos com
        uma mensagem clara em vez de um traceback de ConnectionRefusedError.
        """
        deadline = time.monotonic() + self.connect_wait
        delay = 1.0
        avisou = False
        tentativas = 0
        while True:
            tentativas += 1
            try:
                if self.security == "SSL":
                    # TLS direto (ex.: porta 993 do Outlook) — sem STARTTLS
                    # pós-conexão, a sessão já nasce cifrada.
                    imap = imaplib.IMAP4_SSL(
                        self.host, self.port, timeout=self.timeout, ssl_context=self._build_ssl_context()
                    )
                else:
                    imap = imaplib.IMAP4(self.host, self.port, timeout=self.timeout)
                if tentativas > 1:
                    logger.info("Bridge conectou em %s:%s após %d tentativa(s).", self.host, self.port, tentativas)
                return imap
            except (ConnectionRefusedError, TimeoutError, OSError) as e:
                if time.monotonic() >= deadline:
                    logger.error(
                        "servidor IMAP não respondeu em %s:%s após %.0fs (%d tentativa(s)): %s",
                        self.host, self.port, self.connect_wait, tentativas, e,
                    )
                    raise ConnectionRefusedError(
                        f"servidor IMAP não respondeu em {self.host}:{self.port} "
                        f"após {self.connect_wait:.0f}s"
                    ) from e
                logger.debug("tentativa %d de conexão ao servidor IMAP falhou: %s", tentativas, e)
                if not avisou:
                    print(
                        f"aguardando o servidor IMAP em {self.host}:{self.port}…",
                        file=sys.stderr,
                    )
                    avisou = True
                time.sleep(delay)
                delay = min(delay * 1.5, 5.0)

    def __enter__(self) -> "BridgeClient":
        self._imap = self._connect()
        if self.security == "STARTTLS":
            self._imap.starttls(ssl_context=self._build_ssl_context())
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
        try:
            self._imap.login(username, password)
        except Exception:
            logger.exception("login IMAP falhou para %r", username)
            raise
        logger.info("login IMAP ok (%s).", username)

    def copy_to_bulk(self, pasta: str, uids: list[int], destino: str, *, chunk_size: int = 300) -> list[int]:
        """Copia vários UIDs de uma vez pra `destino`, marcando \\Deleted.

        Um único SELECT pra pasta inteira, depois 1 COPY + 1 STORE por chunk
        (sequence-set tipo "12,34,56") em vez de 3 comandos por email — pra
        500 UIDs isso é ~4 comandos em vez de 1500. `chunk_size` evita mandar
        uma linha de comando gigante de uma vez só.

        `UID COPY`/`STORE` com sequence-set é tudo-ou-nada: se um UID do chunk
        já não existir (obsoleto, movido por fora), o chunk inteiro falha. Por
        isso, um chunk que falhar é tentado de novo item a item, pra não perder
        o lote todo por causa de um UID só. Devolve os UIDs que efetivamente
        deram certo (copiados + marcados \\Deleted).
        """
        assert self._imap is not None
        if not uids:
            return []
        typ, _ = self._imap.select(pasta, readonly=False)
        if typ != "OK":
            raise RuntimeError(f"não consegui selecionar {pasta!r} pra escrita")

        ok: list[int] = []
        for i in range(0, len(uids), chunk_size):
            chunk = uids[i : i + chunk_size]
            try:
                self._copy_store(chunk, destino)
                ok.extend(chunk)
            except Exception as e:
                logger.warning(
                    "lote de %d UID(s) falhou em %s (%s) — tentando item a item.",
                    len(chunk), pasta, e,
                )
                for uid in chunk:
                    try:
                        self._copy_store([uid], destino)
                        ok.append(uid)
                    except Exception as e2:
                        logger.warning("pulei %s UID %d (lixeira): %s", pasta, uid, e2)
        return ok

    def _copy_store(self, uids: list[int], destino: str) -> None:
        """COPY + STORE \\Deleted pra um sequence-set de UIDs (a pasta já deve
        estar selecionada em rw). Levanta se qualquer um dos dois falhar."""
        assert self._imap is not None
        seq = ",".join(str(u) for u in uids)
        typ, _ = self._imap.uid("COPY", seq, destino)
        if typ != "OK":
            raise RuntimeError(f"COPY do(s) UID(s) {seq} pra {destino!r} falhou")
        typ, _ = self._imap.uid("STORE", seq, "+FLAGS", "(\\Deleted)")
        if typ != "OK":
            raise RuntimeError(f"STORE \\Deleted no(s) UID(s) {seq} falhou")

    def unflag(self, pasta: str, uids: list[int]) -> None:
        """Remove \\Flagged (desfavorita) de um lote de UIDs.

        Usado quando o dono confirma excluir um email favoritado pela fila
        (ver `apolo.actions.dispatch_lixeira_imap`): sem isso, a checagem de
        proteção em `flagged_uids` barraria a exclusão mesmo com a
        confirmação explícita.
        """
        assert self._imap is not None
        if not uids:
            return
        typ, _ = self._imap.select(pasta, readonly=False)
        if typ != "OK":
            raise RuntimeError(f"não consegui selecionar {pasta!r} pra escrita")
        seq = ",".join(str(u) for u in uids)
        typ, _ = self._imap.uid("STORE", seq, "-FLAGS", "(\\Flagged)")
        if typ != "OK":
            raise RuntimeError(f"STORE -Flagged no(s) UID(s) {seq} falhou")

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
        logger.info("[%s] %d UID(s) novo(s) a buscar (search_from=%d, resynced=%s).",
                    pasta, len(new_uids), search_from, resynced)

        novos, falhou_cedo = self._fetch_headers_batch(pasta, new_uids)

        # Se parou cedo (falhas seguidas), ultimo_uid só reflete o que foi
        # buscado com sucesso ANTES da sequência de falhas — os UIDs
        # restantes (inclusive os que falharam) ficam pra tentar de novo no
        # próximo ciclo, em vez de serem dados como vistos.
        max_uid = max((m.uid for m in novos), default=search_from)
        if falhou_cedo:
            logger.warning(
                "[%s] parando cedo após falhas seguidas — retomando destes UIDs no próximo ciclo.",
                pasta,
            )
        return FolderResult(
            pasta=pasta,
            uidvalidity=uidvalidity,
            resynced=resynced,
            novos=novos,
            ultimo_uid=max_uid,
        )

    def _fetch_headers_batch(self, pasta: str, uids: list[int]) -> tuple[list[FetchedEmail], bool]:
        """Busca o header de cada UID, tolerando falhas pontuais.

        Uma exceção no meio do lote (ex.: rate limit do Bridge/API por trás
        dele) não pode derrubar os headers já buscados com sucesso — por
        isso cada UID é isolado num try/except. Se as falhas se repetirem
        (`MAX_FALHAS_SEGUIDAS` seguidas), a conexão provavelmente está morta
        ou sendo limitada; paramos ali e devolvemos só o que já deu certo.
        """
        novos: list[FetchedEmail] = []
        falhas_seguidas = 0
        for uid in uids:
            try:
                m = self._fetch_headers(uid)
            except Exception as e:
                falhas_seguidas += 1
                logger.warning("[%s] falha ao buscar header do UID %d (%d seguida(s)): %s",
                               pasta, uid, falhas_seguidas, e)
                if falhas_seguidas >= MAX_FALHAS_SEGUIDAS:
                    return novos, True
                continue
            falhas_seguidas = 0
            if m is None:
                logger.debug("[%s] UID %d sem resposta OK do FETCH — ignorado.", pasta, uid)
                continue
            novos.append(m)
        return novos, False

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
        logger.info("[%s] sync completo: %d UID(s) na pasta, %d considerado(s) (limit=%d).",
                    pasta, len(all_uids), len(uids), limit)
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

    def flagged_uids(self, pasta: str) -> set[int]:
        """UIDs atualmente com \\Flagged (favoritado) na pasta — um único UID
        SEARCH que cobre a pasta inteira, sem custo por mensagem. Usado pra
        reconferir favoritos de e-mails que o Apolo já tinha sincronizado (o
        favorito só é capturado no FETCH original — ver `_fetch_headers`).
        """
        assert self._imap is not None
        self._select_readonly(pasta)
        typ, data = self._imap.uid("search", None, "FLAGGED")
        if typ != "OK":
            raise RuntimeError(f"falha no UID SEARCH FLAGGED em {pasta!r}")
        raw_ids = data[0].split() if data and data[0] else []
        return {int(x) for x in raw_ids}

    def restore_from_trash(self, trash_folder: str, message_id: str | None, pasta_origem: str) -> bool:
        """Restaura da lixeira pra `pasta_origem`, buscando pelo Message-ID.

        `copy_to_bulk` não guarda o UID que a mensagem ganhou na pasta de
        destino (só o UID de origem, já expurgado) — então a única forma de
        achar de volta é buscar pelo header. Devolve False se não achar
        (Message-ID ausente, ambíguo ou a mensagem já foi apagada de vez);
        usado por `apolo.actions.restaurar_email` pra desfazer um auto-envio.
        """
        assert self._imap is not None
        if not message_id or not message_id.strip():
            return False
        typ, _ = self._imap.select(trash_folder, readonly=False)
        if typ != "OK":
            raise RuntimeError(f"não consegui selecionar {trash_folder!r}")
        criterio = f'(HEADER "Message-ID" "{message_id.strip()}")'
        typ, data = self._imap.uid("SEARCH", None, criterio)
        if typ != "OK" or not data or not data[0]:
            return False
        uids = [int(x) for x in data[0].split()]
        uid = uids[-1]  # o mais recente, se por acaso houver mais de um resultado
        ok = self.copy_to_bulk(trash_folder, [uid], pasta_origem)
        if uid not in ok:
            return False
        self.expunge(trash_folder)
        return True

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
            logger.warning("FETCH do corpo do UID %d falhou (typ=%s).", uid, typ)
            return None
        return email.message_from_bytes(data[0][1])

    def _fetch_headers(self, uid: int) -> FetchedEmail | None:
        """Busca os headers de interesse + FLAGS via BODY.PEEK (não marca lido).

        FLAGS entra no mesmo FETCH pra não gastar um round-trip a mais só pra
        saber se a mensagem está com \\Flagged (favoritada no Proton/Gmail).
        """
        assert self._imap is not None
        typ, data = self._imap.uid(
            "fetch",
            str(uid),
            "(FLAGS BODY.PEEK[HEADER.FIELDS (FROM SUBJECT DATE MESSAGE-ID LIST-UNSUBSCRIBE)])",
        )
        if typ != "OK" or not data or not isinstance(data[0], tuple):
            logger.warning("FETCH do header do UID %d falhou (typ=%s).", uid, typ)
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
            favorito=b"\\Flagged" in data[0][0],
            lido=b"\\Seen" in data[0][0],
        )
