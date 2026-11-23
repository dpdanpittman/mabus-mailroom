"""Non-secret configuration: which accounts exist and what each may do.

Secrets (the OAuth client and refresh tokens) never live here; they go to the
token store (OS keychain or an encrypted file). This file holds aliases, the
address each alias belongs to, and its mode.
"""

from __future__ import annotations

import json
import os
import re
import sys
from dataclasses import asdict, dataclass, field
from pathlib import Path

SCOPE_READONLY = "https://www.googleapis.com/auth/gmail.readonly"
SCOPE_COMPOSE = "https://www.googleapis.com/auth/gmail.compose"

MODE_READONLY = "readonly"
MODE_DRAFTS = "drafts"
MODE_SEND = "send"
MODES = (MODE_READONLY, MODE_DRAFTS, MODE_SEND)

# gmail.compose covers drafts.create, drafts.send and messages.send, so "send" needs
# no extra scope; the difference between drafts and send is enforced by this server.
MODE_SCOPES = {
    MODE_READONLY: [SCOPE_READONLY],
    MODE_DRAFTS: [SCOPE_READONLY, SCOPE_COMPOSE],
    MODE_SEND: [SCOPE_READONLY, SCOPE_COMPOSE],
}

ALL = "all"
_ALIAS_RE = re.compile(r"^[a-z0-9][a-z0-9_-]{0,31}$")


class ConfigError(Exception):
    pass


def validate_alias(alias: str) -> str:
    if not isinstance(alias, str) or not _ALIAS_RE.match(alias):
        raise ConfigError(
            "alias must be 1-32 chars of lowercase letters, digits, '-' or '_', "
            "starting with a letter or digit"
        )
    if alias == ALL:
        raise ConfigError("'all' is reserved (it means every account in search)")
    return alias


def config_dir() -> Path:
    env = os.environ.get("GMAIL_MULTI_HOME")
    if env:
        return Path(env).expanduser()
    if sys.platform == "win32":
        base = os.environ.get("APPDATA") or str(Path.home() / "AppData" / "Roaming")
        return Path(base) / "gmail-multi"
    base = os.environ.get("XDG_CONFIG_HOME") or str(Path.home() / ".config")
    return Path(base) / "gmail-multi"


def ensure_private_dir(path: Path) -> None:
    path.mkdir(parents=True, exist_ok=True)
    if os.name == "posix":
        os.chmod(path, 0o700)


def write_private_file(path: Path, data: bytes) -> None:
    """Atomically write ``data`` to ``path`` with mode 600 from the first byte."""
    ensure_private_dir(path.parent)
    tmp = path.with_name(f".{path.name}.{os.getpid()}.tmp")
    flags = os.O_WRONLY | os.O_CREAT | os.O_TRUNC
    if hasattr(os, "O_NOFOLLOW"):
        flags |= os.O_NOFOLLOW
    fd = os.open(tmp, flags, 0o600)
    try:
        with os.fdopen(fd, "wb") as fh:
            fh.write(data)
            fh.flush()
            os.fsync(fh.fileno())
        if os.name == "posix":
            os.chmod(tmp, 0o600)
        os.replace(tmp, path)
    except BaseException:
        try:
            os.unlink(tmp)
        except FileNotFoundError:
            pass
        raise


@dataclass
class Account:
    alias: str
    email: str
    mode: str = MODE_READONLY
    scopes: list[str] = field(default_factory=lambda: [SCOPE_READONLY])

    @property
    def can_draft(self) -> bool:
        return self.mode in (MODE_DRAFTS, MODE_SEND) and SCOPE_COMPOSE in self.scopes

    @property
    def can_send(self) -> bool:
        return self.mode == MODE_SEND and SCOPE_COMPOSE in self.scopes


@dataclass
class Config:
    store: str = "auto"  # "keyring" | "file" | "command" | "auto" (decided on first save)
    accounts: dict[str, Account] = field(default_factory=dict)
    command_store: dict = field(default_factory=dict)  # settings for the "command" store (no secrets)
    path: Path | None = None

    @classmethod
    def load(cls, path: Path | None = None) -> "Config":
        path = path or config_dir() / "config.json"
        if not path.exists():
            return cls(path=path)
        raw = json.loads(path.read_text(encoding="utf-8"))
        accounts = {}
        for alias, a in (raw.get("accounts") or {}).items():
            validate_alias(alias)
            mode = a.get("mode", MODE_READONLY)
            if mode not in MODES:
                raise ConfigError(f"account {alias}: unknown mode {mode!r}")
            accounts[alias] = Account(
                alias=alias,
                email=a.get("email", ""),
                mode=mode,
                scopes=list(a.get("scopes") or MODE_SCOPES[mode]),
            )
        return cls(store=raw.get("store", "auto"), accounts=accounts,
                   command_store=dict(raw.get("command_store") or {}), path=path)

    def save(self) -> None:
        assert self.path is not None
        payload = {
            "version": 1,
            "store": self.store,
            **({"command_store": self.command_store} if self.command_store else {}),
            "accounts": {
                alias: {k: v for k, v in asdict(acc).items() if k != "alias"}
                for alias, acc in sorted(self.accounts.items())
            },
        }
        write_private_file(self.path, (json.dumps(payload, indent=2) + "\n").encode())
