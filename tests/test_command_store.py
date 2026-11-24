"""The password-manager ("command") token store. Fake CLIs only; no real vault is touched."""

import base64
import json
import sys
import textwrap
from types import SimpleNamespace

import pytest

from mabus_mailroom import cli
from mabus_mailroom.config import Config
from mabus_mailroom.tokenstore import CommandStore, StoreError, account_key

SECRET = {"refresh_token": "1//0gFAKE-refresh-SECRET-abcdef", "scopes": []}
SECRET_TEXT = "1//0gFAKE-refresh-SECRET-abcdef"


class FakeVault:
    """Imitates pass-cli / op / bw well enough to check what we send them."""

    def __init__(self, preset, locked=False):
        self.preset = preset
        self.locked = locked
        self.items: dict[str, str] = {}
        self.bw: dict[str, dict] = {}
        self.log: list[tuple[list, bytes | None, dict]] = []

    def __call__(self, argv, stdin, env):
        self.log.append((list(argv), stdin, env))
        if self.locked:
            return 1, b"", b"Error: vault is locked, please unlock (session expired)"
        return getattr(self, self.preset.replace("-", "_"))(argv, stdin)

    def proton_pass(self, argv, stdin):
        if argv[1:3] == ["item", "view"]:
            title = argv[argv.index("--item-title") + 1]
            if title not in self.items:
                return 1, b"", b"Error: item not found"
            return 0, (self.items[title] + "\n").encode(), b""
        if argv[1:4] == ["item", "create", "note"]:
            assert argv[-2:] == ["--from-template", "-"]
            tpl = json.loads(stdin)
            self.items[tpl["title"]] = tpl["note"]
            return 0, b"created", b""
        if argv[1:3] == ["item", "trash"]:
            title = argv[argv.index("--item-title") + 1]
            if self.items.pop(title, None) is None:
                return 1, b"", b"Error: item not found"
            return 0, b"", b""
        raise AssertionError(argv)

    def _1password(self, argv, stdin):
        if argv[1:3] == ["item", "get"]:
            if argv[3] not in self.items:
                return 1, b"", f'[ERROR] "{argv[3]}" isn\'t an item in the "mabus-mailroom" vault.'.encode()
            return 0, json.dumps({"id": "password", "value": self.items[argv[3]]}).encode(), b""
        if argv[1:3] == ["item", "create"]:
            assert argv[-1] == "-"
            tpl = json.loads(stdin)
            assert tpl["category"] == "PASSWORD"
            self.items[tpl["title"]] = tpl["fields"][0]["value"]
            return 0, b"{}", b""
        if argv[1:3] == ["item", "delete"]:
            if self.items.pop(argv[3], None) is None:
                return 1, b"", b'[ERROR] "x" isn\'t an item'
            return 0, b"", b""
        raise AssertionError(argv)

    def bitwarden(self, argv, stdin):
        if argv[1:3] == ["list", "items"]:
            term = argv[argv.index("--search") + 1]
            return 0, json.dumps([i for i in self.bw.values() if term in i["name"]]).encode(), b""
        if argv[1:3] == ["create", "item"]:
            item = json.loads(base64.b64decode(stdin))
            item["id"] = f"id{len(self.bw) + 1}"
            self.bw[item["id"]] = item
            return 0, b"{}", b""
        if argv[1:3] == ["edit", "item"]:
            self.bw[argv[3]] = json.loads(base64.b64decode(stdin))
            return 0, b"{}", b""
        if argv[1:3] == ["delete", "item"]:
            self.bw.pop(argv[3])
            return 0, b"", b""
        raise AssertionError(argv)

    # FakeVault method name for "1password" preset
    def __getattr__(self, name):
        if name == "1password":
            return self._1password
        raise AttributeError(name)


def assert_secret_never_in_argv_or_added_env(vault, base_env_keys):
    for argv, _stdin, env in vault.log:
        assert all(SECRET_TEXT not in a for a in argv), argv
        for k, v in env.items():
            if k not in base_env_keys:
                assert SECRET_TEXT not in v


@pytest.mark.parametrize("preset", ["proton-pass", "1password", "bitwarden"])
def test_roundtrip_secret_only_on_stdin(preset):
    import os
    vault = FakeVault(preset)
    store = CommandStore({"preset": preset, "vault": "mabus-mailroom"}, runner=vault)
    assert store.get(account_key("work")) is None  # missing item is not an error
    store.set(account_key("work"), SECRET)
    store.set(account_key("work"), SECRET)  # replacing works and leaves one item
    assert store.get(account_key("work")) == SECRET
    assert_secret_never_in_argv_or_added_env(vault, set(os.environ))
    assert any(stdin and (SECRET_TEXT.encode() in stdin or SECRET_TEXT.encode() in base64.b64decode(stdin + b"=="))
               for _a, stdin, _e in vault.log if stdin)
    store.delete(account_key("work"))
    assert store.get(account_key("work")) is None
    store.delete(account_key("work"))  # deleting twice is fine


def test_bitwarden_exact_name_match_and_duplicates():
    vault = FakeVault("bitwarden")
    store = CommandStore({"preset": "bitwarden"}, runner=vault)
    store.set(account_key("work2"), {"refresh_token": "other"})
    assert store.get(account_key("work")) is None  # 'work' is a prefix of 'work2' but not a match
    vault.bw["dup"] = dict(next(iter(vault.bw.values())), id="dup")
    with pytest.raises(StoreError, match="more than one"):
        store.get(account_key("work2"))


def test_proton_pass_sets_agent_reason_without_overriding_user_env(monkeypatch):
    vault = FakeVault("proton-pass")
    CommandStore({"preset": "proton-pass"}, runner=vault).get("client")
    assert vault.log[0][2]["PROTON_PASS_AGENT_REASON"]
    monkeypatch.setenv("PROTON_PASS_AGENT_REASON", "mine")
    vault2 = FakeVault("proton-pass")
    CommandStore({"preset": "proton-pass", "env": {"PROTON_PASS_KEY_PROVIDER": "fs"}}, runner=vault2).get("client")
    assert vault2.log[0][2]["PROTON_PASS_AGENT_REASON"] == "mine"
    assert vault2.log[0][2]["PROTON_PASS_KEY_PROVIDER"] == "fs"


@pytest.mark.parametrize("preset", ["proton-pass", "1password", "bitwarden"])
def test_locked_vault_is_a_clean_error_without_the_secret(preset):
    vault = FakeVault(preset, locked=True)
    store = CommandStore({"preset": preset}, runner=vault)
    with pytest.raises(StoreError, match="locked or signed out") as exc:
        store.set(account_key("work"), SECRET)
    assert SECRET_TEXT not in str(exc.value)
    with pytest.raises(StoreError, match="locked or signed out"):
        store.get(account_key("work"))


def test_secret_echoed_in_stderr_is_scrubbed():
    def runner(argv, stdin, env):
        return 2, b"", b"weird failure while saving " + stdin
    store = CommandStore({"preset": "custom", "get": "x get {item}", "set": "x set {item}", "delete": "x rm {item}"},
                         runner=runner)
    with pytest.raises(StoreError) as exc:
        store.set(account_key("work"), SECRET)
    assert SECRET_TEXT not in str(exc.value) and "[secret removed]" in str(exc.value)


def test_missing_cli_and_bad_settings():
    with pytest.raises(StoreError, match="preset"):
        CommandStore({})
    with pytest.raises(StoreError, match="must contain"):
        CommandStore({"preset": "custom", "get": "x", "set": "x {item}", "delete": "x {item}"})
    store = CommandStore({"preset": "custom", "get": "definitely-not-a-cli-xyz {item}",
                          "set": "definitely-not-a-cli-xyz {item}", "delete": "definitely-not-a-cli-xyz {item}"})
    with pytest.raises(StoreError, match="not found on PATH"):
        store.get("client")


FAKE_CLI = textwrap.dedent("""
    import json, os, sys
    from pathlib import Path
    root = Path(os.environ["FAKE_VAULT_DIR"])
    with open(root / "argv.log", "a") as log:
        log.write(json.dumps(sys.argv[1:]) + "\\n")
    op, item = sys.argv[1], sys.argv[2]
    path = root / (item + ".secret")
    if op == "get":
        if path.exists():
            sys.stdout.write(path.read_text())
    elif op == "set":
        path.write_text(sys.stdin.read())
    elif op == "rm":
        path.unlink(missing_ok=True)
""")


def test_custom_preset_end_to_end_with_a_real_subprocess(tmp_path, monkeypatch):
    script = tmp_path / "fakepm.py"
    script.write_text(FAKE_CLI)
    vault_dir = tmp_path / "vault"
    vault_dir.mkdir()
    monkeypatch.setenv("FAKE_VAULT_DIR", str(vault_dir))
    base = f"{sys.executable} -I {script}"
    store = CommandStore({"preset": "custom", "get": f"{base} get {{item}}", "set": f"{base} set {{item}}",
                          "delete": f"{base} rm {{item}}"})
    assert store.get(account_key("work")) is None
    store.set(account_key("work"), SECRET)
    assert store.get(account_key("work")) == SECRET
    store.delete(account_key("work"))
    assert store.get(account_key("work")) is None
    log = (vault_dir / "argv.log").read_text()
    assert SECRET_TEXT not in log and "mabus-mailroom-account-work" in log


def test_cli_command_store_settings(isolated_home):
    args = SimpleNamespace(store_preset="proton-pass", store_vault="Mail tokens", store_env=["PROTON_PASS_KEY_PROVIDER=fs"],
                           store_get=None, store_set=None, store_delete=None)
    cfg = Config.load()
    store = cli._open_store(cfg, "command", args)
    assert isinstance(store, CommandStore) and store.vault == "Mail tokens"
    assert cfg.store == "command"
    assert cfg.command_store == {"preset": "proton-pass", "vault": "Mail tokens",
                                 "env": {"PROTON_PASS_KEY_PROVIDER": "fs"}}
    with pytest.raises(StoreError, match="secrets in your shell"):
        cli._command_settings(SimpleNamespace(**{**vars(args), "store_env": ["BW_SESSION=abc"]}))
    with pytest.raises(StoreError, match="--store-preset"):
        cli._open_store(Config.load(), "command", None)


@pytest.mark.asyncio
async def test_locked_store_marks_account_unavailable_but_others_work():
    import httpx

    from mabus_mailroom.config import MODE_READONLY, Account
    from mabus_mailroom.server import build_service
    from mabus_mailroom.service import ToolError

    class HalfLocked:
        name = "command"

        def get(self, key):
            if key == "account:locked":
                raise StoreError("pass-cli could not read: the vault looks locked or signed out")
            if key == "client":
                return {"client_id": "x.apps.googleusercontent.com", "client_secret": None}
            return {"refresh_token": "rt"}

    cfg = Config(accounts={"ok": Account("ok", "a@example.com", MODE_READONLY),
                           "locked": Account("locked", "b@example.com", MODE_READONLY)})
    async with httpx.AsyncClient() as http:
        svc = build_service(cfg, HalfLocked(), http)
    assert "ok" in svc.clients and "locked" not in svc.clients
    with pytest.raises(ToolError, match="unavailable: token store unavailable"):
        await svc.get_message("locked", "m1")


@pytest.mark.asyncio
async def test_search_all_reports_unavailable_account():
    from conftest import unwrap
    from fakes import FakeGmail, config_with, make_msg

    from mabus_mailroom.service import MultiGmail

    svc = MultiGmail(config_with(ok="readonly", locked="readonly"), {"ok": FakeGmail([make_msg("m1", 5)])},
                     unavailable={"locked": "token store unavailable (vault locked)"})
    _, data = unwrap(await svc.search("x", "all"))
    assert data["accounts"] == ["ok"] and [m["id"] for m in data["messages"]] == ["m1"]
    assert data["errors"] == [{"account": "locked", "error": "unavailable: token store unavailable (vault locked)"}]
