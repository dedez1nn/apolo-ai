"""Configuração do Apolo — só stdlib.

Lê as credenciais do Bridge e os caminhos de estado de variáveis de ambiente,
com um parser mínimo de .env (KEY=VALUE) pra não depender de python-dotenv.
Como o oneshot roda sem TTY, nada de input() interativo aqui.

Contas adicionais (Gmail, Outlook) ficam em accounts.toml; veja AccountConfig.
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


def _xdg_data() -> str:
    return os.environ.get("XDG_DATA_HOME") or os.path.expanduser("~/.local/share")


def _default_db_path() -> Path:
    """~/.local/share/apolo/apolo.db, respeitando XDG_DATA_HOME."""
    return Path(_xdg_data()) / "apolo" / "apolo.db"


def _default_rules_path() -> Path:
    """As regras vivem junto do pacote, em apolo/rules/config.toml."""
    return Path(__file__).resolve().parent / "rules" / "config.toml"


def _default_accounts_path() -> Path:
    return Path(_xdg_data()) / "apolo" / "accounts.toml"


def _default_tokens_dir() -> Path:
    return Path(_xdg_data()) / "apolo" / "tokens"


@dataclass(frozen=True)
class AccountConfig:
    """Uma conta externa (Gmail, futuramente Outlook)."""

    name: str                          # identificador livre: "pessoal", "work"…
    provider: str                      # "gmail"
    client_id: str
    client_secret: str
    folders: tuple[str, ...] = ("INBOX",)


def load_accounts(accounts_path: Path | None = None) -> list[AccountConfig]:
    """Lê accounts.toml; retorna [] se o arquivo não existir."""
    import tomllib

    path = accounts_path or _default_accounts_path()
    if not path.is_file():
        return []
    with path.open("rb") as f:
        data = tomllib.load(f)
    accounts: list[AccountConfig] = []
    for entry in data.get("accounts", []):
        try:
            accounts.append(AccountConfig(
                name=entry["name"],
                provider=entry["provider"],
                client_id=entry.get("client_id", ""),
                client_secret=entry.get("client_secret", ""),
                folders=tuple(entry.get("folders", ["INBOX"])),
            ))
        except KeyError:
            pass
    return accounts


@dataclass(frozen=True)
class Config:
    imap_host: str = "127.0.0.1"
    imap_port: int = 1143
    username: str = ""
    password: str = ""
    # "STARTTLS" (padrão do Bridge em 1143) ou "PLAIN".
    imap_security: str = "STARTTLS"
    db_path: Path = field(default_factory=_default_db_path)
    rules_path: Path = field(default_factory=_default_rules_path)
    # Pastas IMAP a vigiar; "INBOX" basta pro passo 1.
    folders: tuple[str, ...] = ("INBOX",)
    # Pasta de lixeira do Proton (atributo \Trash no Bridge).
    trash_folder: str = "Trash"
    # IA (Ollama) — classifica só o resíduo que as regras não resolveram.
    ai_enabled: bool = True
    ollama_url: str = "http://127.0.0.1:11434"
    ollama_model: str = "llama3.2"
    ollama_keep_alive: str = "30m"
    accounts_path: Path = field(default_factory=_default_accounts_path)
    tokens_dir: Path = field(default_factory=_default_tokens_dir)

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

        rules_path_str = get("APOLO_RULES_PATH")
        rules_path = (
            Path(os.path.expanduser(rules_path_str))
            if rules_path_str
            else _default_rules_path()
        )

        folders_str = get("APOLO_FOLDERS", "INBOX")
        folders = tuple(f.strip() for f in folders_str.split(",") if f.strip())

        accounts_path_str = get("APOLO_ACCOUNTS_PATH")
        accounts_path = (
            Path(os.path.expanduser(accounts_path_str)) if accounts_path_str else _default_accounts_path()
        )
        tokens_dir_str = get("APOLO_TOKENS_DIR")
        tokens_dir = (
            Path(os.path.expanduser(tokens_dir_str)) if tokens_dir_str else _default_tokens_dir()
        )

        # Senha: env real (override explícito) > keyring do SO (fonte gerida pela
        # UI, troca toda sessão) > .env (legado). O keyring vence o .env de
        # propósito — uma senha velha no .env não deve mascarar a nova do keyring.
        from apolo import secrets

        password = os.environ.get("APOLO_PASSWORD") or secrets.lookup_password() or env.get(
            "APOLO_PASSWORD", ""
        )

        return cls(
            imap_host=get("APOLO_IMAP_HOST", "127.0.0.1"),
            imap_port=int(get("APOLO_IMAP_PORT", "1143")),
            username=get("APOLO_USERNAME"),
            password=password,
            imap_security=get("APOLO_IMAP_SECURITY", "STARTTLS").upper(),
            db_path=db_path,
            rules_path=rules_path,
            folders=folders or ("INBOX",),
            trash_folder=get("APOLO_TRASH_FOLDER", "Trash"),
            ai_enabled=get("APOLO_AI_ENABLED", "true").lower() in ("1", "true", "yes", "sim"),
            ollama_url=get("APOLO_OLLAMA_URL") or get("OLLAMA_HOST") or "http://127.0.0.1:11434",
            ollama_model=get("APOLO_OLLAMA_MODEL", "llama3.2"),
            ollama_keep_alive=get("APOLO_OLLAMA_KEEP_ALIVE", "30m"),
            accounts_path=accounts_path,
            tokens_dir=tokens_dir,
        )

    def require_credentials(self) -> None:
        """Falha cedo e claro se faltar usuário/senha do Bridge."""
        if not self.username or not self.password:
            raise RuntimeError(
                "Credenciais do Bridge ausentes. Defina o usuário no .env "
                "(APOLO_USERNAME) e a senha pela tela de Configurações da UI "
                "(guardada no keyring do SO)."
            )
