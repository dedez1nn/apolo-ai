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
import time
import urllib.error
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path

from apolo.fetch import FetchedEmail, FolderResult

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

    def authorize(self) -> None:
        """Device flow OAuth2. Imprime URL + código; bloqueia até o usuário autorizar."""
        data = urllib.parse.urlencode({
            "client_id": self.client_id,
            "scope": _SCOPE,
        }).encode()
        resp = self._http_post(_DEVICE_CODE_URL, data,
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
        device_code = resp["device_code"]
        user_code = resp["user_code"]
        verification_url = resp["verification_url"]
        expires_in = int(resp.get("expires_in", 1800))
        interval = int(resp.get("interval", 5))

        print(f"\nAutorizando conta Gmail '{self.name}':")
        print(f"  1. Acesse:  {verification_url}")
        print(f"  2. Código:  {user_code}\n")

        deadline = time.time() + expires_in
        while time.time() < deadline:
            time.sleep(interval)
            try:
                token = self._poll_device_token(device_code)
                self._save_token(token)
                print(f"✓ Conta '{self.name}' autorizada.")
                return
            except _AuthPending:
                continue
            except _SlowDown as e:
                interval = e.interval

        raise RuntimeError("Autorização expirou — tente novamente.")

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
        resp = self._http_post(_TOKEN_URL, data,
                               headers={"Content-Type": "application/x-www-form-urlencoded"})
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
            resp = self._api("GET", f"/users/me/messages/{gmail_id}", token=token, params={
                "format": "metadata",
                "metadataHeaders": "From,Subject,Date,Message-ID,List-Unsubscribe",
            })
        except urllib.error.HTTPError:
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
        )

    def fetch_message(self, gmail_id: str) -> str:
        """Retorna trecho de texto do corpo (para a IA). Falha silenciosa → ''."""
        token = self._ensure_token()
        try:
            resp = self._api("GET", f"/users/me/messages/{gmail_id}", token=token,
                             params={"format": "full"})
        except urllib.error.HTTPError:
            return ""
        return _extract_text(resp.get("payload", {}))[:2000]

    # ----- ações -----

    def trash_message(self, gmail_id: str) -> None:
        """Move a mensagem para a lixeira via API."""
        token = self._ensure_token()
        self._api("POST", f"/users/me/messages/{gmail_id}/trash", token=token)

    # ----- HTTP helpers -----

    def _api(self, method: str, path: str, *, token: str,
             params: dict | None = None, body: dict | None = None) -> dict:
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
        with urllib.request.urlopen(req, timeout=self.timeout) as resp:
            return json.loads(resp.read().decode("utf-8"))

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
