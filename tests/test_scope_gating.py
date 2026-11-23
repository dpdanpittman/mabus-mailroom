import base64
import email

import pytest
from fakes import MODE_DRAFTS, MODE_READONLY, FakeGmail, config_with, make_msg

from gmail_multi.config import SCOPE_READONLY
from gmail_multi.server import build_server
from gmail_multi.service import MultiGmail, ToolError


def setup(read_only=False):
    ro = FakeGmail([make_msg("r1", 1)])
    rw = FakeGmail([make_msg("m1", 1, thread_id="T1", message_id_header="<orig@example.com>")])
    svc = MultiGmail(config_with(personal=MODE_READONLY, work=MODE_DRAFTS), {"personal": ro, "work": rw}, read_only=read_only)
    return svc, ro, rw


@pytest.mark.asyncio
async def test_draft_refused_on_read_only_account():
    svc, ro, _ = setup()
    with pytest.raises(ToolError, match="read-only"):
        await svc.create_draft("personal", "carol@example.com", "s", "b")
    assert ro.drafts == [] and ro.calls == []


@pytest.mark.asyncio
async def test_draft_refused_when_compose_scope_missing_even_if_mode_says_drafts():
    svc, _, rw = setup()
    svc.config.accounts["work"].scopes = [SCOPE_READONLY]  # user declined compose
    with pytest.raises(ToolError):
        await svc.create_draft("work", "carol@example.com", "s", "b")
    assert rw.drafts == []


@pytest.mark.asyncio
async def test_draft_refused_when_server_read_only():
    svc, _, rw = setup(read_only=True)
    with pytest.raises(ToolError, match="read-only"):
        await svc.create_draft("work", "carol@example.com", "s", "b")
    assert rw.drafts == []


@pytest.mark.asyncio
async def test_draft_created_on_drafts_account_and_not_sent():
    svc, _, rw = setup()
    out = await svc.create_draft("work", "Carol <carol@example.com>", "Lunch", "Thursday?", cc="dave@example.com")
    assert "NOT been sent" in out
    raw, thread = rw.drafts[0]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert msg["To"] == "Carol <carol@example.com>" and msg["Cc"] == "dave@example.com"
    assert msg["Subject"] == "Lunch" and "Thursday?" in msg.get_payload()
    assert thread is None


@pytest.mark.asyncio
async def test_reply_draft_threads_correctly():
    svc, _, rw = setup()
    await svc.create_draft("work", "bob@example.com", "Re: hi", "ok", reply_to_message_id="m1")
    raw, thread = rw.drafts[0]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert thread == "T1"
    assert msg["In-Reply-To"] == "<orig@example.com>"


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["to", "subject", "cc", "bcc"])
async def test_header_injection_refused(field):
    svc, _, rw = setup()
    kwargs = {"to": "carol@example.com", "subject": "s", "body": "b"}
    kwargs[field] = "x@example.com\nBcc: evil@example.com"
    with pytest.raises(ToolError, match="single line"):
        await svc.create_draft("work", **kwargs)
    assert rw.drafts == []


@pytest.mark.asyncio
async def test_draft_needs_a_recipient_and_bounded_body():
    svc, _, rw = setup()
    with pytest.raises(ToolError):
        await svc.create_draft("work", "", "s", "b")
    with pytest.raises(ToolError):
        await svc.create_draft("work", "carol@example.com", "s", "x" * 200_000)
    assert rw.drafts == []


@pytest.mark.asyncio
async def test_create_draft_tool_absent_when_no_account_opted_in():
    svc = MultiGmail(config_with(a=MODE_READONLY, b=MODE_READONLY), {"a": FakeGmail(), "b": FakeGmail()})
    names = {t.name for t in await build_server(svc).list_tools()}
    assert "create_draft" not in names
    assert {"list_accounts", "search", "get_message", "get_thread", "list_labels"} <= names


@pytest.mark.asyncio
async def test_create_draft_tool_absent_in_read_only_server_mode():
    svc, *_ = setup(read_only=True)
    names = {t.name for t in await build_server(svc).list_tools()}
    assert "create_draft" not in names
