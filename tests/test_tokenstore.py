import json
import os
import stat

import pytest

from gmail_multi.config import Account, Config
from gmail_multi.tokenstore import (CLIENT_KEY, EncryptedFileStore, KeyringStore, StoreError, account_key,
                                    is_secure_backend, resolve_store_kind)

SECRET_RT = "1//0gFAKE-refresh-token-abcdefghijklmnopqrstuvwxyz"
SECRET_CS = "GOCSPX-fake-client-secret-123456"


def fixed(passphrase):
    return lambda creating: passphrase


def all_bytes_under(path):
    blob = b""
    for root, _dirs, files in os.walk(path):
        for f in files:
            with open(os.path.join(root, f), "rb") as fh:
                blob += fh.read()
    return blob


def test_encrypted_file_never_contains_plaintext(isolated_home):
    store = EncryptedFileStore(passphrase_provider=fixed("correct horse battery"), scrypt_n=2**12)
    store.set(CLIENT_KEY, {"client_id": "123.apps.googleusercontent.com", "client_secret": SECRET_CS})
    store.set(account_key("work"), {"refresh_token": SECRET_RT, "scopes": []})
    raw = store.path.read_bytes()
    assert SECRET_RT.encode() not in raw and SECRET_CS.encode() not in raw
    assert b"refresh_token" not in raw
    envelope = json.loads(raw)
    assert envelope["cipher"] == "AES-256-GCM" and envelope["kdf"]["name"] == "scrypt"
    # and nothing else in the config dir holds it either
    Config(accounts={"work": Account("work", "alice@example.com")}, path=isolated_home / "config.json").save()
    blob = all_bytes_under(isolated_home)
    assert SECRET_RT.encode() not in blob and SECRET_CS.encode() not in blob


@pytest.mark.skipif(os.name != "posix", reason="POSIX modes")
def test_files_are_private(isolated_home):
    store = EncryptedFileStore(passphrase_provider=fixed("correct horse battery"), scrypt_n=2**12)
    store.set("k", {"v": 1})
    Config(path=isolated_home / "config.json").save()
    for p in (store.path, isolated_home / "config.json"):
        assert stat.S_IMODE(os.stat(p).st_mode) == 0o600
    assert stat.S_IMODE(os.stat(isolated_home).st_mode) == 0o700
    # no temp files left behind
    assert sorted(os.listdir(isolated_home)) == ["config.json", "tokens.enc"]


def test_roundtrip_and_delete(isolated_home):
    s1 = EncryptedFileStore(passphrase_provider=fixed("correct horse battery"), scrypt_n=2**12)
    s1.set(account_key("a"), {"refresh_token": SECRET_RT})
    s2 = EncryptedFileStore(passphrase_provider=fixed("correct horse battery"))
    assert s2.get(account_key("a"))["refresh_token"] == SECRET_RT
    s2.delete(account_key("a"))
    s3 = EncryptedFileStore(passphrase_provider=fixed("correct horse battery"))
    assert s3.get(account_key("a")) is None


def test_wrong_passphrase_and_tampering_fail(isolated_home):
    s1 = EncryptedFileStore(passphrase_provider=fixed("correct horse battery"), scrypt_n=2**12)
    s1.set("k", {"refresh_token": SECRET_RT})
    with pytest.raises(StoreError, match="wrong passphrase"):
        EncryptedFileStore(passphrase_provider=fixed("wrong horse battery")).get("k")
    env = json.loads(s1.path.read_text())
    ct = bytearray(env["ciphertext"].encode())
    ct[10] = ord("A") if ct[10] != ord("A") else ord("B")
    env["ciphertext"] = ct.decode()
    s1.path.write_text(json.dumps(env))
    with pytest.raises(StoreError):
        EncryptedFileStore(passphrase_provider=fixed("correct horse battery")).get("k")


def test_noninteractive_without_passphrase_fails_closed(isolated_home, monkeypatch):
    import io
    monkeypatch.setattr("sys.stdin", io.StringIO())
    store = EncryptedFileStore(scrypt_n=2**12)
    with pytest.raises(StoreError, match="passphrase"):
        store.set("k", {"v": 1})
    assert not store.path.exists()


def test_passphrase_command(isolated_home, monkeypatch):
    monkeypatch.setenv("GMAIL_MULTI_PASSPHRASE_CMD", "printf 'from-a-password-manager'")
    s1 = EncryptedFileStore(scrypt_n=2**12)
    s1.set("k", {"v": 1})
    s2 = EncryptedFileStore(passphrase_provider=fixed("from-a-password-manager"))
    assert s2.get("k") == {"v": 1}


# ---------------------------------------------------------------- keyring


class MemoryKeyring:
    """Stands in for a real OS keychain backend."""
    priority = 5

    def __init__(self):
        self.data = {}

    def get_password(self, service, key):
        return self.data.get((service, key))

    def set_password(self, service, key, value):
        self.data[(service, key)] = value

    def delete_password(self, service, key):
        self.data.pop((service, key), None)


class PlaintextKeyring(MemoryKeyring):
    pass


def test_keyring_store_roundtrip_and_no_disk_writes(isolated_home):
    kr = MemoryKeyring()
    store = KeyringStore(backend=kr)
    store.set(account_key("work"), {"refresh_token": SECRET_RT})
    assert store.get(account_key("work"))["refresh_token"] == SECRET_RT
    assert not isolated_home.exists() or SECRET_RT.encode() not in all_bytes_under(isolated_home)
    store.delete(account_key("work"))
    assert store.get(account_key("work")) is None


def test_insecure_keyring_backends_refused():
    import keyring.backends.fail

    assert not is_secure_backend(keyring.backends.fail.Keyring())
    assert not is_secure_backend(PlaintextKeyring())
    assert is_secure_backend(MemoryKeyring())
    with pytest.raises(StoreError, match="no secure OS keychain"):
        KeyringStore(backend=keyring.backends.fail.Keyring())


def test_store_kind_cannot_silently_switch():
    assert resolve_store_kind("file", "auto") == "file"
    assert resolve_store_kind(None, "keyring") == "keyring"
    with pytest.raises(StoreError):
        resolve_store_kind("file", "keyring")
