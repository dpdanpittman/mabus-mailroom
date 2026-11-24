import pytest
from conftest import unwrap
from fakes import MODE_READONLY, FakeGmail, config_with, make_msg

from mabus_mailroom.gmail import GmailError
from mabus_mailroom.service import MultiGmail, RateLimiter, ToolError


def two_accounts():
    work = FakeGmail([make_msg("w1", 1000, "work old"), make_msg("w2", 3000, "work new")])
    home = FakeGmail([make_msg("h1", 2000, "home mid"), make_msg("h2", 4000, "home newest")])
    cfg = config_with(work=MODE_READONLY, home=MODE_READONLY)
    return MultiGmail(cfg, {"work": work, "home": home}), work, home


@pytest.mark.asyncio
async def test_routes_to_named_account_only():
    svc, work, home = two_accounts()
    kind, data = unwrap(await svc.get_message("home", "h1"))
    assert kind == "message"
    assert data["account"] == "home" and data["subject"] == "home mid"
    assert home.calls == [("get", "h1", "full")]
    assert work.calls == []


@pytest.mark.asyncio
async def test_unknown_alias_lists_known_ones():
    svc, *_ = two_accounts()
    with pytest.raises(ToolError) as exc:
        await svc.get_message("bank", "x1")
    assert "home" in str(exc.value) and "work" in str(exc.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("call", [
    lambda s: s.get_message("all", "w1"),
    lambda s: s.get_thread("all", "t-w1"),
    lambda s: s.list_labels("all"),
])
async def test_all_is_only_for_search(call):
    svc, work, home = two_accounts()
    with pytest.raises(ToolError, match="only accepted by search"):
        await call(svc)
    assert work.calls == [] and home.calls == []


@pytest.mark.asyncio
async def test_fan_out_search_merges_newest_first_and_caps():
    svc, work, home = two_accounts()
    _, data = unwrap(await svc.search("in:inbox", "all", max_results=3))
    assert data["accounts"] == ["home", "work"]
    assert [(m["account"], m["id"]) for m in data["messages"]] == [("home", "h2"), ("work", "w2"), ("home", "h1")]
    assert data["returned"] == 3
    assert data["errors"] == []
    # each account searched once with the same query
    assert ("list", "in:inbox", 3) in work.calls and ("list", "in:inbox", 3) in home.calls


@pytest.mark.asyncio
async def test_fan_out_reports_one_failing_account_and_keeps_the_rest():
    work = FakeGmail([make_msg("w1", 1000)])
    broken = FakeGmail(fail=GmailError(403, "Gmail API has not been used in project"))
    svc = MultiGmail(config_with(work=MODE_READONLY, broken=MODE_READONLY), {"work": work, "broken": broken})
    _, data = unwrap(await svc.search("x", "all"))
    assert [m["id"] for m in data["messages"]] == ["w1"]
    assert data["errors"][0]["account"] == "broken"
    assert "403" in data["errors"][0]["error"]


@pytest.mark.asyncio
async def test_single_account_search_does_not_touch_others():
    svc, work, home = two_accounts()
    _, data = unwrap(await svc.search("x", "work"))
    assert {m["account"] for m in data["messages"]} == {"work"}
    assert home.calls == []


@pytest.mark.asyncio
async def test_max_results_clamped_to_limit():
    svc, work, _ = two_accounts()
    await svc.search("x", "work", max_results=10_000)
    assert ("list", "x", 50) in work.calls


@pytest.mark.asyncio
async def test_rejects_malformed_ids():
    svc, work, _ = two_accounts()
    with pytest.raises(ToolError):
        await svc.get_message("work", "../../etc/passwd")
    assert work.calls == []


@pytest.mark.asyncio
async def test_list_labels_and_accounts():
    svc, *_ = two_accounts()
    _, data = unwrap(await svc.list_labels("work"))
    assert data["labels"][0]["name"] == "INBOX"
    accounts = svc.list_accounts()
    assert [a["alias"] for a in accounts] == ["home", "work"]
    assert all(a["can_draft"] is False for a in accounts)


def test_rate_limiter():
    rl = RateLimiter(per_minute=2)
    rl.check()
    rl.check()
    with pytest.raises(ToolError, match="rate limit"):
        rl.check()
