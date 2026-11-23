"""Where secrets live: the OS keychain, or an encrypted file as a fallback.

Two kinds of secret are stored, each as one JSON value under a key:

* ``client``          the user's own OAuth client (client_id, client_secret)
* ``account:<alias>`` the refresh token and granted scopes for one account

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


# --------------------------------------------------------------------------- selection


def keyring_available() -> bool:
    try:
        KeyringStore()
        return True
    except Exception:
        return False


def open_store(kind: str) -> TokenStore:
    if kind == "keyring":
        return KeyringStore()
    if kind == "file":
        return EncryptedFileStore()
    raise StoreError(f"unknown store {kind!r} (use 'keyring' or 'file')")


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
