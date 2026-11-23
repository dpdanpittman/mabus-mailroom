"""Where secrets live: the OS keychain, or an encrypted file as a fallback.

Two kinds of secret are stored, each as one JSON value under a key:

* ``client``          the user's own OAuth client (client_id, client_secret)
* ``account:<alias>`` the refresh token and granted scopes for one account

A third backend, ``command``, keeps them in a password manager (Proton Pass,
1Password, Bitwarden, or any CLI) and moves them only over stdin/stdout.

Nothing here ever writes a secret to disk in plain text. The keychain backend
refuses keyring backends that are known to be insecure (the "fail" backend and
the plaintext backends from keyrings.alt). The file backend encrypts the whole
store with AES-256-GCM under a key derived from a passphrase with scrypt.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import shlex
import subprocess
import sys
from pathlib import Path
from typing import Callable, Protocol

from cryptography.exceptions import InvalidTag
from cryptography.hazmat.primitives.ciphers.aead import AESGCM
from cryptography.hazmat.primitives.kdf.scrypt import Scrypt

from .config import config_dir, write_private_file

SERVICE = "gmail-multi"
CLIENT_KEY = "client"


def account_key(alias: str) -> str:
    return f"account:{alias}"


class StoreError(Exception):
    pass


class TokenStore(Protocol):
    name: str

    def get(self, key: str) -> dict | None: ...

    def set(self, key: str, value: dict) -> None: ...

    def delete(self, key: str) -> None: ...


# --------------------------------------------------------------------------- keyring

_INSECURE_BACKEND_MARKERS = (
    "keyring.backends.fail",
    "keyring.backends.null",
    "keyrings.alt.file.PlaintextKeyring",
    "keyrings.alt.file_base",
)


def is_secure_backend(backend: object) -> bool:
    """True when the keyring backend keeps secrets out of plain-text files."""
    if backend is None:
        return False
    qual = f"{type(backend).__module__}.{type(backend).__qualname__}"
    if any(qual.startswith(m) or m in qual for m in _INSECURE_BACKEND_MARKERS):
        return False
    if "Plaintext" in qual:
        return False
    try:
        priority = float(getattr(backend, "priority", 1))
    except Exception:  # some backends raise when unavailable
        return False
    if priority <= 0:
        return False
    # ChainerBackend: secure only if it chains at least one secure backend.
    children = getattr(backend, "backends", None)
    if children is not None and type(backend).__name__ == "ChainerBackend":
        return any(is_secure_backend(b) for b in children)
    return True


class KeyringStore:
    name = "keyring"

    def __init__(self, backend: object | None = None, service: str = SERVICE):
        import keyring

        self._kr = backend if backend is not None else keyring.get_keyring()
        if not is_secure_backend(self._kr):
            raise StoreError(
                f"no secure OS keychain available (backend: {type(self._kr).__name__}); "
                "use the encrypted file store (--store file)"
            )
        self._service = service

    def get(self, key: str) -> dict | None:
        raw = self._kr.get_password(self._service, key)
        return json.loads(raw) if raw else None

    def set(self, key: str, value: dict) -> None:
        self._kr.set_password(self._service, key, json.dumps(value))

    def delete(self, key: str) -> None:
        try:
            self._kr.delete_password(self._service, key)
        except Exception as exc:  # keyring raises PasswordDeleteError if missing
            if "not found" not in str(exc).lower() and type(exc).__name__ != "PasswordDeleteError":
                raise


# --------------------------------------------------------------------------- encrypted file

PassphraseProvider = Callable[[bool], str]  # arg: True when creating a new store


def default_passphrase_provider(creating: bool) -> str:
    """Passphrase from a command, the environment, or an interactive prompt.

    ``GMAIL_MULTI_PASSPHRASE_CMD`` runs a command (no shell) and uses its stdout,
    e.g. ``pass show gmail-multi`` or ``op read op://Private/gmail-multi/password``.
    ``GMAIL_MULTI_PASSPHRASE`` is accepted but less safe (it ends up in a config file).
    """
    cmd = os.environ.get("GMAIL_MULTI_PASSPHRASE_CMD")
    if cmd:
        out = subprocess.run(shlex.split(cmd), capture_output=True, text=True, check=False)
        if out.returncode != 0 or not out.stdout.strip():
            raise StoreError("GMAIL_MULTI_PASSPHRASE_CMD failed or printed nothing")
        return out.stdout.rstrip("\n")
    env = os.environ.get("GMAIL_MULTI_PASSPHRASE")
    if env:
        return env
    if not sys.stdin.isatty():
        raise StoreError(
            "the encrypted token file needs a passphrase: set GMAIL_MULTI_PASSPHRASE_CMD "
            "(recommended) or GMAIL_MULTI_PASSPHRASE"
        )
    first = getpass.getpass("gmail-multi passphrase: ")
    if creating:
        if len(first) < 12:
            raise StoreError("use a passphrase of at least 12 characters")
        if getpass.getpass("repeat passphrase: ") != first:
            raise StoreError("passphrases do not match")
    return first


class EncryptedFileStore:
    """Whole-store AES-256-GCM encryption; key from scrypt(passphrase, salt)."""

    name = "file"
    VERSION = 1

    def __init__(
        self,
        path: Path | None = None,
        passphrase_provider: PassphraseProvider = default_passphrase_provider,
        scrypt_n: int = 2**16,
    ):
        self.path = path or config_dir() / "tokens.enc"
        self._provider = passphrase_provider
        self._scrypt_n = scrypt_n
        self._passphrase: str | None = None
        self._cache: dict | None = None

    @staticmethod
    def _derive(passphrase: str, salt: bytes, n: int, r: int, p: int) -> bytes:
        return Scrypt(salt=salt, length=32, n=n, r=r, p=p).derive(passphrase.encode("utf-8"))

    def _load(self) -> dict:
        if self._cache is not None:
            return self._cache
        if not self.path.exists():
            self._cache = {}
            return self._cache
        envelope = json.loads(self.path.read_text(encoding="utf-8"))
        if envelope.get("v") != self.VERSION:
            raise StoreError("unsupported token file version")
        if self._passphrase is None:
            self._passphrase = self._provider(False)
        kdf = envelope["kdf"]
        key = self._derive(
            self._passphrase, base64.b64decode(kdf["salt"]), kdf["n"], kdf["r"], kdf["p"]
        )
        try:
            plain = AESGCM(key).decrypt(
                base64.b64decode(envelope["nonce"]),
                base64.b64decode(envelope["ciphertext"]),
                b"gmail-multi-v1",
            )
        except InvalidTag:
            self._passphrase = None
            raise StoreError("wrong passphrase, or the token file was modified") from None
        self._cache = json.loads(plain)
        return self._cache

    def _save(self, data: dict) -> None:
        if self._passphrase is None:
            self._passphrase = self._provider(not self.path.exists())
        salt = os.urandom(16)
        nonce = os.urandom(12)
        n, r, p = self._scrypt_n, 8, 1
        key = self._derive(self._passphrase, salt, n, r, p)
        ct = AESGCM(key).encrypt(nonce, json.dumps(data).encode("utf-8"), b"gmail-multi-v1")
        envelope = {
            "v": self.VERSION,
            "kdf": {"name": "scrypt", "salt": base64.b64encode(salt).decode(), "n": n, "r": r, "p": p},
            "cipher": "AES-256-GCM",
            "nonce": base64.b64encode(nonce).decode(),
            "ciphertext": base64.b64encode(ct).decode(),
        }
        write_private_file(self.path, json.dumps(envelope).encode("utf-8"))
        self._cache = data

    def get(self, key: str) -> dict | None:
        value = self._load().get(key)
        return dict(value) if value is not None else None

    def set(self, key: str, value: dict) -> None:
        data = dict(self._load())
        data[key] = value
        self._save(data)

    def delete(self, key: str) -> None:
        data = dict(self._load())
        if key in data:
            del data[key]
            self._save(data)


# --------------------------------------------------------------------------- password-manager CLI

Runner = Callable[[list, "bytes | None", dict], tuple]  # (argv, stdin, env) -> (returncode, stdout, stderr)

_LOCKED_HINTS = ("locked", "not logged in", "not signed in", "not currently signed in", "session",
                 "unauthorized", "authenticate", "login", "log in", "sign in", "signin")
_NOT_FOUND_HINTS = ("not found", "isn't an item", "no item", "does not exist", "no such item")


def _default_runner(argv: list, stdin: bytes | None, env: dict) -> tuple:
    try:
        proc = subprocess.run(argv, input=stdin, stdin=None if stdin is not None else subprocess.DEVNULL,
                              capture_output=True, env=env, timeout=60, check=False)
    except FileNotFoundError:
        raise StoreError(f"password manager CLI {argv[0]!r} not found on PATH") from None
    except subprocess.TimeoutExpired:
        raise StoreError(f"{argv[0]} timed out after 60 s (is it waiting for an unlock prompt?)") from None
    return proc.returncode, proc.stdout, proc.stderr


class CommandStore:
    """Secrets kept in a password manager, reached through its CLI.

    Secret values only ever travel on the CLI's stdin (writes) or stdout (reads);
    argv carries the vault and item *names* only, and nothing is logged.
    Presets: ``proton-pass`` (pass-cli), ``1password`` (op), ``bitwarden`` (bw),
    and ``custom`` (your own get/set/delete commands with an ``{item}`` placeholder).
    """

    name = "command"
    PRESETS = ("proton-pass", "1password", "bitwarden", "custom")

    def __init__(self, settings: dict, runner: Runner | None = None):
        self.preset = settings.get("preset", "")
        if self.preset not in self.PRESETS:
            raise StoreError(f"command store needs a preset: one of {', '.join(self.PRESETS)}")
        self.vault = settings.get("vault") or "gmail-multi"
        self.prefix = settings.get("prefix") or "gmail-multi"
        self.settings = settings
        self._run_raw = runner or _default_runner
        extra = {str(k): str(v) for k, v in (settings.get("env") or {}).items()}
        self._env = {**os.environ, **extra}
        if self.preset == "proton-pass":
            # Agent sessions must give a reason for each access (shown in Proton's audit log).
            self._env.setdefault("PROTON_PASS_AGENT_REASON", "gmail-multi OAuth token access")
        if self.preset == "custom":
            for op in ("get", "set", "delete"):
                if "{item}" not in (settings.get(op) or ""):
                    raise StoreError(f"custom command store: the {op!r} command must contain {{item}}")

    # ---------------------------------------------------------------- helpers

    def item_name(self, key: str) -> str:
        name = f"{self.prefix}-{key.replace(':', '-')}"
        if not all(c.isalnum() or c in "-_." for c in name):
            raise StoreError("invalid item name")
        return name

    def _run(self, argv: list, stdin: bytes | None = None, secret: str | None = None, what: str = "") -> tuple:
        rc, out, err = self._run_raw(argv, stdin, self._env)
        if rc == 0:
            return rc, out, err
        text = (err or b"").decode("utf-8", "replace")
        if secret:
            text = text.replace(secret, "[secret removed]")
        low = text.lower()
        if any(h in low for h in _NOT_FOUND_HINTS):
            return rc, out, text
        tool = argv[0]
        if any(h in low for h in _LOCKED_HINTS):
            raise StoreError(
                f"{tool} could not {what}: the vault looks locked or signed out. Unlock or sign in "
                f"(pass-cli login / op signin / bw unlock + BW_SESSION) and try again. "
                f"Detail: {text.strip()[:200]}"
            )
        raise StoreError(f"{tool} could not {what} (exit {rc}): {text.strip()[:200]}")

    @staticmethod
    def _not_found(rc: int, err) -> bool:
        return rc != 0 and isinstance(err, str)

    # ---------------------------------------------------------------- interface

    def get(self, key: str) -> dict | None:
        item = self.item_name(key)
        raw = getattr(self, f"_get_{self.preset.replace('-', '_')}")(item)
        if raw is None or not raw.strip():
            return None
        try:
            return json.loads(raw)
        except ValueError:
            raise StoreError(f"item {item!r} does not hold gmail-multi data") from None

    def set(self, key: str, value: dict) -> None:
        item = self.item_name(key)
        secret = json.dumps(value, separators=(",", ":"))
        getattr(self, f"_set_{self.preset.replace('-', '_')}")(item, secret)

    def delete(self, key: str) -> None:
        getattr(self, f"_delete_{self.preset.replace('-', '_')}")(self.item_name(key))

    # ---------------------------------------------------------------- Proton Pass (pass-cli)

    def _get_proton_pass(self, item):
        rc, out, err = self._run(["pass-cli", "item", "view", "--vault-name", self.vault, "--item-title", item,
                                  "--field", "note"], what=f"read {item!r}")
        return None if self._not_found(rc, err) else out.decode("utf-8").strip()

    def _set_proton_pass(self, item, secret):
        # pass-cli cannot update a note from stdin, so replace: trash the old item, create anew.
        self._delete_proton_pass(item)
        template = json.dumps({"title": item, "note": secret}).encode()
        self._run(["pass-cli", "item", "create", "note", "--vault-name", self.vault, "--from-template", "-"],
                  stdin=template, secret=secret, what=f"save {item!r}")

    def _delete_proton_pass(self, item):
        self._run(["pass-cli", "item", "trash", "--vault-name", self.vault, "--item-title", item],
                  what=f"trash {item!r}")

    # ---------------------------------------------------------------- 1Password (op)

    def _get_1password(self, item):
        rc, out, err = self._run(["op", "item", "get", item, "--vault", self.vault, "--fields", "label=password",
                                  "--reveal", "--format", "json"], what=f"read {item!r}")
        if self._not_found(rc, err):
            return None
        try:
            return json.loads(out).get("value")
        except ValueError:
            raise StoreError("op returned unexpected output") from None

    def _set_1password(self, item, secret):
        self._delete_1password(item)
        template = json.dumps({"title": item, "category": "PASSWORD", "fields": [
            {"id": "password", "type": "CONCEALED", "purpose": "PASSWORD", "label": "password", "value": secret},
        ]}).encode()
        self._run(["op", "item", "create", "--vault", self.vault, "-"], stdin=template, secret=secret,
                  what=f"save {item!r}")

    def _delete_1password(self, item):
        self._run(["op", "item", "delete", item, "--vault", self.vault], what=f"delete {item!r}")

    # ---------------------------------------------------------------- Bitwarden (bw)

    def _bw_find(self, item) -> dict | None:
        _, out, _ = self._run(["bw", "list", "items", "--search", item], what=f"search for {item!r}")
        try:
            matches = [i for i in json.loads(out or b"[]") if i.get("name") == item]
        except ValueError:
            raise StoreError("bw returned unexpected output") from None
        if len(matches) > 1:
            raise StoreError(f"more than one Bitwarden item is named {item!r}; remove the extras")
        return matches[0] if matches else None

    def _get_bitwarden(self, item):
        found = self._bw_find(item)
        return found.get("notes") if found else None

    def _set_bitwarden(self, item, secret):
        found = self._bw_find(item)
        if found:
            found["notes"] = secret
            payload = base64.b64encode(json.dumps(found).encode())
            self._run(["bw", "edit", "item", found["id"]], stdin=payload, secret=secret, what=f"update {item!r}")
        else:
            body = {"type": 2, "name": item, "notes": secret, "secureNote": {"type": 0}}
            payload = base64.b64encode(json.dumps(body).encode())
            self._run(["bw", "create", "item"], stdin=payload, secret=secret, what=f"save {item!r}")

    def _delete_bitwarden(self, item):
        found = self._bw_find(item)
        if found:
            self._run(["bw", "delete", "item", found["id"]], what=f"delete {item!r}")

    # ---------------------------------------------------------------- custom

    def _custom_argv(self, op: str, item: str) -> list:
        return [part.replace("{item}", item).replace("{vault}", self.vault)
                for part in shlex.split(self.settings[op])]

    def _get_custom(self, item):
        rc, out, err = self._run(self._custom_argv("get", item), what=f"read {item!r}")
        return None if self._not_found(rc, err) else out.decode("utf-8").strip()

    def _set_custom(self, item, secret):
        self._run(self._custom_argv("set", item), stdin=secret.encode(), secret=secret, what=f"save {item!r}")

    def _delete_custom(self, item):
        self._run(self._custom_argv("delete", item), what=f"delete {item!r}")


# --------------------------------------------------------------------------- selection


def keyring_available() -> bool:
    try:
        KeyringStore()
        return True
    except Exception:
        return False


def open_store(kind: str, command_settings: dict | None = None) -> TokenStore:
    if kind == "keyring":
        return KeyringStore()
    if kind == "file":
        return EncryptedFileStore()
    if kind == "command":
        return CommandStore(command_settings or {})
    raise StoreError(f"unknown store {kind!r} (use 'keyring', 'file' or 'command')")


def resolve_store_kind(requested: str | None, configured: str) -> str:
    if requested and requested != "auto":
        if configured not in ("auto", requested):
            raise StoreError(
                f"this install already uses the {configured!r} store; "
                "remove all accounts before switching"
            )
        return requested
    if configured != "auto":
        return configured
    return "keyring" if keyring_available() else "file"
