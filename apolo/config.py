"""Configuração do Apolo — só stdlib.

Lê as credenciais do Bridge e os caminhos de estado de variáveis de ambiente,
com um parser mínimo de .env (KEY=VALUE) pra não depender de python-dotenv.
Como o oneshot roda sem TTY, nada de input() interativo aqui.
"""

import os
from dataclasses import dataclass, field
from pathlib import Path

# Procura o .env subindo a partir deste arquivo: apolo/config.py -> raiz do projeto.
_PROJECT_ROOT = Path(__file__).resolve().parent.parent


def _parse_env_file(path: Path) -> dict[str, str]:
    """Parser mínimo de .env: KEY=VALUE, ignora linhas vazias e comentários."""
    values: dict[str, str] = {}
    if not path.is_file():
        return values
    for raw in path.read_text(encoding="utf-8").splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or "=" not in line:
            continue
        key, _, val = line.partition("=")
        key = key.strip()
        val = val.strip().strip('"').strip("'")
        if key:
            values[key] = val
    return values


def _default_db_path() -> Path:
    """~/.local/share/apolo/apolo.db, respeitando XDG_DATA_HOME."""
    base = os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")
    return Path(base) / "apolo" / "apolo.db"


@dataclass(frozen=True)
class Config:
    imap_host: str = "127.0.0.1"
    imap_port: int = 1143
    username: str = ""
    password: str = ""
    # "STARTTLS" (padrão do Bridge em 1143) ou "PLAIN".
    imap_security: str = "STARTTLS"
    db_path: Path = field(default_factory=_default_db_path)
    # Pastas IMAP a vigiar; "INBOX" basta pro passo 1.
    folders: tuple[str, ...] = ("INBOX",)

    @classmethod
    def load(cls) -> "Config":
        """Env real do processo tem precedência sobre o .env do projeto."""
        env = _parse_env_file(_PROJECT_ROOT / ".env")

        def get(key: str, default: str = "") -> str:
            return os.environ.get(key, env.get(key, default))

        db_path_str = get("APOLO_DB_PATH")
        db_path = (
            Path(os.path.expanduser(db_path_str)) if db_path_str else _default_db_path()
        )

        folders_str = get("APOLO_FOLDERS", "INBOX")
        folders = tuple(f.strip() for f in folders_str.split(",") if f.strip())

        return cls(
            imap_host=get("APOLO_IMAP_HOST", "127.0.0.1"),
            imap_port=int(get("APOLO_IMAP_PORT", "1143")),
            username=get("APOLO_USERNAME"),
            password=get("APOLO_PASSWORD"),
            imap_security=get("APOLO_IMAP_SECURITY", "STARTTLS").upper(),
            db_path=db_path,
            folders=folders or ("INBOX",),
        )

    def require_credentials(self) -> None:
        """Falha cedo e claro se faltar usuário/senha do Bridge."""
        if not self.username or not self.password:
            raise RuntimeError(
                "Credenciais do Bridge ausentes. Defina APOLO_USERNAME e "
                "APOLO_PASSWORD no .env (veja .env.example)."
            )
