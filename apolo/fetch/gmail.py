"""Cliente Gmail API — OAuth2 device flow, só stdlib.

Fluxo de autorização (primeira vez):
  apolo accounts add gmail  →  mostra URL + código  →  usuário autoriza no
  browser  →  refresh token salvo em {token_path}.

Fetch incremental via History API: o historyId fica armazenado como
ultimo_uid na tabela meta (conta=gmail:{name}, uidvalidity=1). Na primeira
varredura busca as últimas 50 mensagens do INBOX.

UID numérico: hash SHA-1 truncado (48 bits) do Gmail message ID — estável,
sem colisões práticas em caixa pessoal.
"""

from __future__ import annotations

import hashlib
import json
import logging
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apolo.fetch import FetchedEmail, FolderResult

logger = logging.getLogger("apolo.fetch.gmail")

_BASE = "https://gmail.googleapis.com/gmail/v1"
_DEVICE_CODE_URL = "https://oauth2.googleapis.com/device/code"
_TOKEN_URL = "https://oauth2.googleapis.com/token"
_SCOPE = "https://www.googleapis.com/auth/gmail.modify"


def _uid_from_gmail_id(gmail_id: str) -> int:
    """UID inteiro estável derivado do Gmail message ID (48-bit hash)."""
    return int(hashlib.sha1(gmail_id.encode()).hexdigest()[:12], 16)


class _AuthPending(Exception):
    pass


class _SlowDown(Exception):
    def __init__(self, interval: int):
        self.interval = interval


@dataclass
class _Token:
    access_token: str
    refresh_token: str
    expires_at: datetime

    def is_expired(self) -> bool:
        return datetime.now(timezone.utc) >= self.expires_at - timedelta(minutes=5)

    def to_dict(self) -> dict:
        return {
            "access_token": self.access_token,
            "refresh_token": self.refresh_token,
            "expires_at": self.expires_at.isoformat(),
        }

    @classmethod
    def from_dict(cls, d: dict) -> "_Token":
        return cls(
            access_token=d["access_token"],
            refresh_token=d["refresh_token"],
            expires_at=datetime.fromisoformat(d["expires_at"]),
        )


class GmailClient:
    """Cliente Gmail API. Não é context manager — stateless entre chamadas."""

    def __init__(
        self,
        name: str,
        client_id: str,
        client_secret: str,
        token_path: Path,
        folders: tuple[str, ...] = ("INBOX",),
        timeout: int = 30,
    ):
        self.name = name
        self.client_id = client_id
        self.client_secret = client_secret
        self.token_path = Path(token_path)
        self.folders = folders
        self.timeout = timeout
        self._token: _Token | None = None

    # ----- autorização -----

    def is_authorized(self) -> bool:
        return self.token_path.is_file()

    def check_token(self) -> str | None:
        """Valida a credencial AGORA (toca a rede). None se ok, senão o motivo.

        Renovar um token expirado é onde `invalid_grant` (revogado/expirado)
        aparece; se o access token local ainda não venceu, a chamada leve a
        /users/me/profile pega revogação do outro lado. Problemas de rede ou
        do servidor não condenam o token — aí devolve None (benefício da
        dúvida: o sync tenta e reporta o erro real).
        """
        if not self.is_authorized():
            return "conta não autorizada"
        try:
            token = self._ensure_token()
            self._api("GET", "/users/me/profile", token=token)
        except RuntimeError as e:
            return str(e)
        except urllib.error.HTTPError as e:
            if e.code in (401, 403):
                return f"credencial recusada pelo Gmail (HTTP {e.code})"
            return None
        except (urllib.error.URLError, OSError, ValueError):
            return None
        return None

    def authorize(self, on_url=None) -> None:
        """Authorization code flow com loopback redirect (RFC 8252).

        Sobe um servidor HTTP temporário em 127.0.0.1 para capturar o código
        de autorização. Requer OAuth client do tipo "Desktop app" no Google Cloud.

        Se on_url for fornecido, chama on_url(auth_url) em vez de imprimir —
        útil para a TUI exibir a URL na interface.
        """
        import http.server
        import socket

        # porta livre no loopback
        with socket.socket() as s:
            s.bind(("127.0.0.1", 0))
            port = s.getsockname()[1]

        redirect_uri = f"http://127.0.0.1:{port}"

        params = urllib.parse.urlencode({
            "client_id": self.client_id,
            "redirect_uri": redirect_uri,
            "response_type": "code",
            "scope": _SCOPE,
            "access_type": "offline",
            "prompt": "consent",
        })
        auth_url = f"https://accounts.google.com/o/oauth2/v2/auth?{params}"

        if on_url is not None:
            on_url(auth_url)
        else:
            print(f"\nAutorizando conta Gmail '{self.name}':")
            print(f"  Abra no browser: {auth_url}\n")

        code_holder: list[str] = []

        class _Handler(http.server.BaseHTTPRequestHandler):
            def do_GET(self):
                parsed = urllib.parse.urlparse(self.path)
                qs = urllib.parse.parse_qs(parsed.query)
                if "code" in qs:
                    code_holder.append(qs["code"][0])
                self.send_response(200)
                self.send_header("Content-Type", "text/html; charset=utf-8")
                self.end_headers()
                self.wfile.write(
                    b"<h2>Apolo: autorizado! Pode fechar esta aba.</h2>"
                )

            def log_message(self, *args):
                pass

        server = http.server.HTTPServer(("127.0.0.1", port), _Handler)
        server.timeout = 300  # 5 minutos para o usuário autorizar
        while not code_holder:
            server.handle_request()
        server.server_close()

        data = urllib.parse.urlencode({
            "code": code_holder[0],
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "redirect_uri": redirect_uri,
            "grant_type": "authorization_code",
        }).encode()
        resp = self._http_post(_TOKEN_URL, data,
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
        token = self._token_from_response(resp)
        self._save_token(token)

    def _poll_device_token(self, device_code: str) -> _Token:
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "device_code": device_code,
            "grant_type": "urn:ietf:params:oauth2:grant_type:device_code",
        }).encode()
        try:
            resp = self._http_post(_TOKEN_URL, data,
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        except urllib.error.HTTPError as e:
            body = json.loads(e.read())
            err = body.get("error", "")
            if err == "authorization_pending":
                raise _AuthPending()
            if err == "slow_down":
                raise _SlowDown(int(body.get("interval", 10)))
            raise
        return self._token_from_response(resp)

    def _token_from_response(self, resp: dict) -> _Token:
        expires_at = datetime.now(timezone.utc) + timedelta(seconds=int(resp["expires_in"]))
        return _Token(
            access_token=resp["access_token"],
            refresh_token=resp.get("refresh_token", ""),
            expires_at=expires_at,
        )

    def _refresh(self, token: _Token) -> _Token:
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "client_secret": self.client_secret,
            "refresh_token": token.refresh_token,
            "grant_type": "refresh_token",
        }).encode()
        try:
            resp = self._http_post(_TOKEN_URL, data,
                                   headers={"Content-Type": "application/x-www-form-urlencoded"})
        except urllib.error.HTTPError as e:
            try:
                err = json.loads(e.read()).get("error", "")
            except (ValueError, OSError):
                err = ""
            if err in ("invalid_grant", "invalid_token"):
                raise RuntimeError(
                    f"Conta '{self.name}': token expirado ou revogado ({err}). "
                    f"Reautorize: apolo accounts add --name {self.name}"
                ) from e
            raise
        return _Token(
            access_token=resp["access_token"],
            refresh_token=resp.get("refresh_token") or token.refresh_token,
            expires_at=datetime.now(timezone.utc) + timedelta(seconds=int(resp["expires_in"])),
        )

    def _save_token(self, token: _Token) -> None:
        self.token_path.parent.mkdir(parents=True, exist_ok=True)
        self.token_path.write_text(json.dumps(token.to_dict(), indent=2), encoding="utf-8")
        self.token_path.chmod(0o600)
        self._token = token

    def _load_token(self) -> _Token | None:
        if not self.token_path.is_file():
            return None
        try:
            return _Token.from_dict(json.loads(self.token_path.read_text(encoding="utf-8")))
        except (KeyError, ValueError):
            return None

    def _ensure_token(self) -> str:
        if self._token is None:
            self._token = self._load_token()
        if self._token is None:
            raise RuntimeError(
                f"Conta '{self.name}' não autorizada. "
                f"Execute: apolo accounts add --name {self.name}"
            )
        if self._token.is_expired():
            self._token = self._refresh(self._token)
            self._save_token(self._token)
        return self._token.access_token

    # ----- fetch -----

    def fetch_new(self, pasta: str, last_history_id: int) -> FolderResult:
        token = self._ensure_token()
        if last_history_id == 0:
            return self._first_sync(pasta, token)
        return self._incremental_sync(pasta, last_history_id, token)

    def _first_sync(self, pasta: str, token: str) -> FolderResult:
        profile = self._api("GET", "/users/me/profile", token=token)
        history_id = int(profile["historyId"])

        label = "INBOX" if pasta.upper() == "INBOX" else pasta
        resp = self._api("GET", "/users/me/messages", token=token,
                         params={"labelIds": label, "maxResults": "50"})
        messages = resp.get("messages", [])

        novos = [m for gid in (m["id"] for m in messages)
                 if (m := self._fetch_headers(gid, token)) is not None]

        return FolderResult(
            pasta=pasta, uidvalidity=1, resynced=True,
            novos=novos, ultimo_uid=history_id,
        )

    def _incremental_sync(self, pasta: str, last_history_id: int, token: str) -> FolderResult:
        label = "INBOX" if pasta.upper() == "INBOX" else pasta
        try:
            resp = self._api("GET", "/users/me/history", token=token, params={
                "startHistoryId": str(last_history_id),
                "labelId": label,
                "historyTypes": "messageAdded",
            })
        except urllib.error.HTTPError as e:
            if e.code == 404:
                # historyId expirou (>30 dias sem sync) — ressincroniza
                return self._first_sync(pasta, token)
            raise

        new_history_id = int(resp.get("historyId", last_history_id))
        gmail_ids: list[str] = []
        for record in resp.get("history", []):
            for added in record.get("messagesAdded", []):
                msg = added.get("message", {})
                if label in msg.get("labelIds", []):
                    gmail_ids.append(msg["id"])

        novos = [m for gid in gmail_ids
                 if (m := self._fetch_headers(gid, token)) is not None]

        return FolderResult(
            pasta=pasta, uidvalidity=1, resynced=False,
            novos=novos, ultimo_uid=new_history_id,
        )

    def _fetch_headers(self, gmail_id: str, token: str) -> FetchedEmail | None:
        try:
            resp = self._api("GET", f"/users/me/messages/{gmail_id}", token=token,
                             params=[
                                 ("format", "metadata"),
                                 ("metadataHeaders", "From"),
                                 ("metadataHeaders", "Subject"),
                                 ("metadataHeaders", "Date"),
                                 ("metadataHeaders", "Message-ID"),
                                 ("metadataHeaders", "List-Unsubscribe"),
                             ])
        except urllib.error.HTTPError as e:
            logger.warning("fetch_header de %s falhou (HTTP %s): %s", gmail_id, e.code, e)
            return None

        headers = {
            h["name"].lower(): h["value"]
            for h in resp.get("payload", {}).get("headers", [])
        }
        return FetchedEmail(
            uid=_uid_from_gmail_id(gmail_id),
            message_id=headers.get("message-id"),
            remetente=headers.get("from", ""),
            assunto=headers.get("subject", ""),
            data=headers.get("date", ""),
            list_unsubscribe=headers.get("list-unsubscribe", ""),
            provider_id=gmail_id,
            # labelIds vem de graça no recurso da mensagem (não é header) —
            # STARRED é como o Gmail marca "favorito"; sem UNREAD é "lido".
            favorito="STARRED" in resp.get("labelIds", []),
            lido="UNREAD" not in resp.get("labelIds", []),
        )

    def list_ids(self, pasta: str, limit: int) -> tuple[list[str], int]:
        """Lista até `limit` IDs de mensagem mais recentes da pasta, paginando
        via messages.list + nextPageToken — sem o cap de 50 do `_first_sync`,
        que é a causa de "não aparecem todos os emails" no Gmail.

        Poucas chamadas no total (uma por página de até 500), bem mais rápido
        que buscar o header de cada mensagem. Devolve (ids, historyId atual);
        o chamador busca o header sob demanda com `fetch_header`, só pros IDs
        que ainda não existem no banco (ver apolo.sync) — assim o progresso
        aparece ao vivo em vez de um lote silencioso.
        """
        token = self._ensure_token()
        profile = self._api("GET", "/users/me/profile", token=token)
        history_id = int(profile["historyId"])

        label = "INBOX" if pasta.upper() == "INBOX" else pasta
        gmail_ids: list[str] = []
        page_token = None
        while limit <= 0 or len(gmail_ids) < limit:
            page_size = min(500, limit - len(gmail_ids)) if limit > 0 else 500
            params: dict = {"labelIds": label, "maxResults": str(page_size)}
            if page_token:
                params["pageToken"] = page_token
            resp = self._api("GET", "/users/me/messages", token=token, params=params)
            gmail_ids.extend(m["id"] for m in resp.get("messages", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break

        return gmail_ids, history_id

    def is_starred(self, gmail_id: str) -> bool:
        """Checagem de STARRED de uma única mensagem, sem custo de headers/corpo
        (`format=minimal`). Usado na hora H do despacho — ver `starred_ids` pra
        reconferência em lote de vários e-mails pendentes de uma pasta."""
        token = self._ensure_token()
        try:
            resp = self._api("GET", f"/users/me/messages/{gmail_id}", token=token, params={"format": "minimal"})
        except urllib.error.HTTPError:
            return False
        return "STARRED" in resp.get("labelIds", [])

    def starred_ids(self, pasta: str) -> set[str]:
        """IDs atualmente com o label STARRED nessa pasta/label — paginado,
        sem custo por mensagem (ao contrário de checar `format=minimal` uma
        por uma). Usado pra reconferir favoritos de e-mails que o Apolo já
        tinha sincronizado (o favorito só é capturado no fetch original —
        ver `_fetch_headers`).
        """
        token = self._ensure_token()
        label = "INBOX" if pasta.upper() == "INBOX" else pasta
        ids: set[str] = set()
        page_token = None
        while True:
            params: list[tuple[str, str]] = [
                ("labelIds", label), ("labelIds", "STARRED"), ("maxResults", "500"),
            ]
            if page_token:
                params.append(("pageToken", page_token))
            resp = self._api("GET", "/users/me/messages", token=token, params=params)
            ids.update(m["id"] for m in resp.get("messages", []))
            page_token = resp.get("nextPageToken")
            if not page_token:
                break
        return ids

    def uid_for(self, gmail_id: str) -> int:
        """UID determinístico de um ID do Gmail — sem chamada de rede, pra
        filtrar contra o banco antes de gastar uma requisição buscando header."""
        return _uid_from_gmail_id(gmail_id)

    def fetch_header(self, gmail_id: str) -> FetchedEmail | None:
        """Busca o header de uma única mensagem (uma requisição)."""
        token = self._ensure_token()
        return self._fetch_headers(gmail_id, token)

    def uids_presentes(self, gmail_ids: list[str], label: str = "INBOX") -> set[str]:
        """Reconciliação: quais desses IDs ainda têm o label (ex.: INBOX) agora.

        Os que sumiram saíram por fora do Apolo (lixeira/arquivado/apagado
        direto no Gmail) — inclui IDs apagados de vez (404). format=minimal
        evita puxar headers/corpo, só os labelIds.
        """
        token = self._ensure_token()
        presentes: set[str] = set()
        for gid in gmail_ids:
            try:
                resp = self._api("GET", f"/users/me/messages/{gid}", token=token,
                                 params={"format": "minimal"})
            except urllib.error.HTTPError as e:
                if e.code in (400, 404):
                    logger.warning("reconciliação gmail: id %s inválido/ausente (HTTP %s) — tratando como removido.",
                                   gid, e.code)
                    continue
                raise
            if label in resp.get("labelIds", []):
                presentes.add(gid)
        return presentes

    def fetch_message(self, gmail_id: str) -> str:
        """Retorna trecho de texto do corpo (para a IA). Falha silenciosa → ''."""
        token = self._ensure_token()
        try:
            resp = self._api("GET", f"/users/me/messages/{gmail_id}", token=token,
                             params={"format": "full"})
        except urllib.error.HTTPError as e:
            logger.warning("fetch_message de %s falhou (HTTP %s): %s", gmail_id, e.code, e)
            return ""
        return _extract_text(resp.get("payload", {}))[:2000]

    def fetch_raw(self, gmail_id: str):
        """Busca a mensagem RFC822 crua (format=raw) como email.message.Message.

        Diferente de fetch_message (que só pega o text/plain truncado pra IA),
        isto devolve a mensagem inteira — assim a UI roda apolo.clean.message_to_text
        e enxerga também o corpo HTML, onde códigos/links costumam estar.
        """
        import base64
        import email

        token = self._ensure_token()
        try:
            resp = self._api("GET", f"/users/me/messages/{gmail_id}", token=token,
                             params={"format": "raw"})
        except urllib.error.HTTPError:
            return None
        raw = resp.get("raw")
        if not raw:
            return None
        return email.message_from_bytes(base64.urlsafe_b64decode(raw))

    # ----- ações -----

    def trash_message(self, gmail_id: str) -> None:
        """Move a mensagem para a lixeira via API (um item só — ver trash_messages_batch)."""
        token = self._ensure_token()
        self._api("POST", f"/users/me/messages/{gmail_id}/trash", token=token)

    def unstar_messages_batch(self, gmail_ids: list[str], *, chunk_size: int = 1000) -> None:
        """Remove o label STARRED (desfavorita) de várias mensagens numa
        chamada só (batchModify). Usado quando o dono confirma excluir um
        email favoritado pela fila (ver
        `apolo.actions.dispatch_lixeira_gmail`): sem isso, a checagem de
        STARRED bem na hora do despacho barraria a exclusão mesmo com a
        confirmação explícita.
        """
        if not gmail_ids:
            return
        token = self._ensure_token()
        for i in range(0, len(gmail_ids), chunk_size):
            chunk = gmail_ids[i : i + chunk_size]
            self._api(
                "POST", "/users/me/messages/batchModify", token=token,
                body={"ids": chunk, "removeLabelIds": ["STARRED"]},
            )

    def untrash_message(self, gmail_id: str) -> None:
        """Tira da lixeira via API dedicada (inverso de trash_message) — usado
        pra restaurar um email que o auto-envio mandou pra lixeira sozinho."""
        token = self._ensure_token()
        self._api("POST", f"/users/me/messages/{gmail_id}/untrash", token=token)

    def trash_messages_batch(self, gmail_ids: list[str], *, chunk_size: int = 1000) -> None:
        """Move várias mensagens pra lixeira numa chamada só (batchModify).

        Bem mais barato que `trash_message` por item: 1 POST cobre até 1000
        IDs, contra 1 POST por email — menos round-trips e menos chance de
        estourar o rate limit da API (quota é por requisição também, não só
        por unidade). `batchModify` responde 204 sem corpo.

        Ao contrário do endpoint dedicado `/trash` (usado em `trash_message`),
        `batchModify` só aplica os labels que você pedir — sem remover INBOX
        por conta própria. Sem o `removeLabelIds` aqui, a mensagem fica com
        TRASH *e* INBOX ao mesmo tempo: some da vista mas ainda casa com o
        filtro `labelIds=INBOX` do `list_ids`, e volta pra fila no próximo
        sync (loop de itens da lixeira reaparecendo pra revisão).
        """
        if not gmail_ids:
            return
        token = self._ensure_token()
        for i in range(0, len(gmail_ids), chunk_size):
            chunk = gmail_ids[i : i + chunk_size]
            self._api(
                "POST", "/users/me/messages/batchModify", token=token,
                body={"ids": chunk, "addLabelIds": ["TRASH"], "removeLabelIds": ["INBOX", "UNREAD"]},
            )

    # ----- HTTP helpers -----

    def _api(self, method: str, path: str, *, token: str,
             params: dict | list | None = None, body: dict | None = None,
             max_retries: int = 5) -> dict:
        """Chama a API; em 429 (rate limit) espera e retenta em vez de estourar.

        Usa o `Retry-After` da resposta quando vem; senão backoff exponencial
        (1s, 2s, 4s, ... até 30s). Esgotados os retries, propaga o erro.
        """
        url = _BASE + path
        if params:
            url += "?" + urllib.parse.urlencode(params)
        data = json.dumps(body).encode() if body else None
        req = urllib.request.Request(
            url, data=data, method=method,
            headers={
                "Authorization": f"Bearer {token}",
                "Content-Type": "application/json",
            },
        )
        delay = 1.0
        for tentativa in range(1, max_retries + 1):
            try:
                with urllib.request.urlopen(req, timeout=self.timeout) as resp:
                    raw = resp.read()
                return json.loads(raw.decode("utf-8")) if raw else {}
            except urllib.error.HTTPError as e:
                if e.code != 429 or tentativa == max_retries:
                    if e.code == 429:
                        logger.warning("Gmail API rate limit (429) em %s — desisti após %d tentativa(s).",
                                       path, tentativa)
                    raise
                retry_after = e.headers.get("Retry-After") if e.headers else None
                espera = float(retry_after) if retry_after else delay
                logger.warning("Gmail API rate limit (429) em %s — tentativa %d/%d, esperando %.0fs.",
                               path, tentativa, max_retries, espera)
                time.sleep(espera)
                delay = min(delay * 2, 30.0)
        raise AssertionError("inalcançável")  # loop sempre retorna ou levanta

    def _http_post(self, url: str, data: bytes, *, headers: dict) -> dict:
        req = urllib.request.Request(url, data=data, method="POST", headers=headers)
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))


# ----- extração de texto do payload -----

def _extract_text(payload: dict) -> str:
    """Extrai texto simples do payload Gmail (recursivo em multipart)."""
    mime = payload.get("mimeType", "")
    if mime == "text/plain":
        import base64
        data = payload.get("body", {}).get("data", "")
        try:
            return base64.urlsafe_b64decode(data + "==").decode("utf-8", errors="replace")
        except Exception:
            return ""
    if mime.startswith("multipart/"):
        for part in payload.get("parts", []):
            text = _extract_text(part)
            if text:
                return text
    return ""
