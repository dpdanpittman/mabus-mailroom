import os
import sys
from pathlib import Path

import pytest
from mcp import Client, StdioServerParameters

from gmail_multi import cli
from gmail_multi.config import MODE_READONLY, Account, Config
from gmail_multi.tokenstore import CLIENT_KEY, account_key


class DictStore:
    name = "keyring"

    def __init__(self):
        self.data = {}

    def get(self, k):
        return self.data.get(k)

    def set(self, k, v):
        self.data[k] = v

    def delete(self, k):
        self.data.pop(k, None)


@pytest.fixture
def setup(monkeypatch):
    store = DictStore()
    revoked = []

    async def fake_revoke(http, token):
        revoked.append(token)
        return True

    monkeypatch.setattr(cli, "open_store", lambda kind: store)
    monkeypatch.setattr(cli, "revoke_token", fake_revoke)
    cfg = Config.load()
    cfg.store = "keyring"
    for alias, email in (("work", "alice@example.com"), ("work2", "alice@example.com"), ("home", "bob@example.com")):
        cfg.accounts[alias] = Account(alias, email, MODE_READONLY)
        store.set(account_key(alias), {"refresh_token": f"rt-{alias}"})
    store.set(CLIENT_KEY, {"client_id": "x.apps.googleusercontent.com", "client_secret": None})
    cfg.save()
    return store, revoked


def test_remove_revokes_and_deletes(setup, capsys):
    store, revoked = setup
    assert cli.main(["remove", "home"]) == 0
    assert revoked == ["rt-home"]
    assert store.get(account_key("home")) is None
    assert "home" not in Config.load().accounts


def test_remove_skips_revoke_when_address_shared(setup, capsys):
    store, revoked = setup
    assert cli.main(["remove", "work"]) == 0
    assert revoked == []  # would have killed work2's grant too
    assert "work2" in capsys.readouterr().out
    assert store.get(account_key("work2")) == {"refresh_token": "rt-work2"}


def test_keep_grant_and_last_account_clears_client(setup):
    store, revoked = setup
    for alias in ("work", "work2", "home"):
        assert cli.main(["remove", alias, "--keep-grant"]) == 0
    assert revoked == []
    assert store.data == {}


def test_bad_alias_rejected(capsys):
    assert cli.main(["remove", "All"]) == 1
    assert cli.main(["remove", "all"]) == 1


@pytest.mark.asyncio
async def test_serve_over_real_stdio_with_no_accounts(isolated_home):
    src = str(Path(__file__).resolve().parent.parent / "src")
    params = StdioServerParameters(
        command=sys.executable, args=["-m", "gmail_multi", "serve"],
        env={**os.environ, "PYTHONPATH": src, "GMAIL_MULTI_HOME": str(isolated_home)},
    )
    async with Client(params) as client:
        names = {t.name for t in (await client.list_tools()).tools}
        res = await client.call_tool("list_accounts", {})
    assert "create_draft" not in names and "search" in names
    assert "No accounts configured" in res.content[0].text
