"""Sending: per-account opt-in, read-only override, caps, and the human confirmation step."""

import base64
import email

import pytest
from fakes import MODE_DRAFTS, MODE_READONLY, MODE_SEND, FakeGmail, config_with, make_msg
from mcp import Client, types

from mabus_mailroom.content import Limits
from mabus_mailroom.server import build_server
from mabus_mailroom.service import MultiGmail, ToolError

MANY = ", ".join(f"p{i}@example.com" for i in range(21))


def setup(read_only=False, limits=None):
    fakes = {"personal": FakeGmail([make_msg("r1", 1)]),
             "work": FakeGmail([make_msg("m1", 1, thread_id="T1", message_id_header="<orig@example.com>")]),
             "club": FakeGmail()}
    cfg = config_with(personal=MODE_READONLY, work=MODE_SEND, club=MODE_DRAFTS)
    kwargs = {"limits": limits} if limits else {}
    return MultiGmail(cfg, fakes, read_only=read_only, **kwargs), fakes


# ------------------------------------------------------------------ service-level gating


@pytest.mark.asyncio
@pytest.mark.parametrize("alias", ["personal", "club"])
async def test_send_refused_unless_account_in_send_mode(alias):
    svc, fakes = setup()
    with pytest.raises(ToolError, match="not allowed to send"):
        await svc.send_message(alias, "carol@example.com", "s", "b")
    with pytest.raises(ToolError, match="not allowed to send"):
        await svc.send_draft(alias, "d1")
    assert fakes[alias].sent == [] and fakes[alias].sent_drafts == []


@pytest.mark.asyncio
async def test_send_refused_when_server_read_only():
    svc, fakes = setup(read_only=True)
    with pytest.raises(ToolError, match="read-only"):
        await svc.send_message("work", "carol@example.com", "s", "b")
    assert fakes["work"].sent == []


@pytest.mark.asyncio
async def test_send_mode_also_allows_drafts():
    svc, fakes = setup()
    await svc.create_draft("work", "carol@example.com", "s", "b")
    assert len(fakes["work"].drafts) == 1


@pytest.mark.asyncio
async def test_send_message_builds_and_sends_once():
    svc, fakes = setup()
    out = await svc.send_message("work", "bob@example.com", "Re: hi", "ok", reply_to_message_id="m1")
    assert out.startswith("SENT")
    assert len(fakes["work"].sent) == 1
    raw, thread = fakes["work"].sent[0]
    msg = email.message_from_bytes(base64.urlsafe_b64decode(raw))
    assert thread == "T1" and msg["In-Reply-To"] == "<orig@example.com>" and msg["To"] == "bob@example.com"


@pytest.mark.asyncio
async def test_recipient_cap():
    svc, fakes = setup()
    with pytest.raises(ToolError, match="too many recipients"):
        await svc.send_message("work", MANY, "s", "b")
    with pytest.raises(ToolError, match="too many recipients"):  # split across to/cc/bcc still counts
        await svc.send_message("work", MANY.split(", ", 1)[1], "s", "b", cc="x@example.com", bcc="y@example.com")
    with pytest.raises(ToolError, match="too many recipients"):  # drafts are capped too
        await svc.create_draft("work", MANY, "s", "b")
    assert fakes["work"].sent == [] and fakes["work"].drafts == []


@pytest.mark.asyncio
@pytest.mark.parametrize("field", ["to", "subject", "cc", "bcc"])
async def test_send_header_injection_refused(field):
    svc, fakes = setup()
    kwargs = {"to": "carol@example.com", "subject": "s", "body": "b"}
    kwargs[field] = "x@example.com\r\nBcc: evil@example.com"
    with pytest.raises(ToolError, match="single line"):
        await svc.send_message("work", **kwargs)
    assert fakes["work"].sent == []


@pytest.mark.asyncio
async def test_send_body_cap():
    svc, fakes = setup(limits=Limits(max_draft_body_chars=100))
    with pytest.raises(ToolError, match="at most 100"):
        await svc.send_message("work", "carol@example.com", "s", "x" * 101)
    assert fakes["work"].sent == []


@pytest.mark.asyncio
async def test_send_draft_rechecks_recipients():
    svc, fakes = setup()
    fakes["work"].draft_headers = {"ok": {"To": "carol@example.com", "Subject": "Hi"}, "big": {"To": MANY}}
    assert (await svc.send_draft("work", "ok")).startswith("SENT draft ok")
    with pytest.raises(ToolError, match="too many recipients"):
        await svc.send_draft("work", "big")
    assert fakes["work"].sent_drafts == ["ok"]


# ------------------------------------------------------------------ tool listing


@pytest.mark.asyncio
async def test_send_tools_only_listed_with_a_send_account():
    svc = MultiGmail(config_with(a=MODE_READONLY, b=MODE_DRAFTS), {"a": FakeGmail(), "b": FakeGmail()})
    names = {t.name for t in await build_server(svc).list_tools()}
    assert "send_message" not in names and "send_draft" not in names
    svc, _ = setup(read_only=True)
    names = {t.name for t in await build_server(svc).list_tools()}
    assert "send_message" not in names and "send_draft" not in names


@pytest.mark.asyncio
async def test_send_tools_described_and_flagged_destructive():
    svc, _ = setup()
    tools = {t.name: t for t in await build_server(svc).list_tools()}
    for name in ("send_message", "send_draft"):
        t = tools[name]
        assert "SEND" in t.description and "cannot be unsent" in t.description
        assert t.annotations.destructive_hint is True and t.annotations.read_only_hint is False
        assert "confirmation" not in t.input_schema["properties"]  # filled by the resolver, not the model


# ------------------------------------------------------------------ confirmation over MCP


def text(result):
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


def answering(action, send=True, seen=None):
    async def callback(context, params):
        if seen is not None:
            seen.append(params.message)
        content = {"send": send} if action == "accept" else None
        return types.ElicitResult(action=action, content=content)
    return callback


ARGS = {"account": "work", "to": "carol@example.com", "subject": "Lunch", "body": "Thursday at noon?"}


@pytest.mark.asyncio
async def test_confirmed_send_goes_out_and_human_saw_the_details():
    svc, fakes = setup()
    seen = []
    async with Client(build_server(svc), elicitation_callback=answering("accept", seen=seen)) as client:
        res = await client.call_tool("send_message", ARGS)
    assert not res.is_error and text(res).startswith("SENT")
    assert len(fakes["work"].sent) == 1
    prompt = seen[0]
    assert "SEND a real email" in prompt and "carol@example.com" in prompt and "Lunch" in prompt
    assert "Thursday at noon?" in prompt and "work@example.com" in prompt


@pytest.mark.asyncio
@pytest.mark.parametrize("action,send", [("decline", True), ("cancel", True), ("accept", False)])
async def test_declined_or_unticked_send_does_not_go_out(action, send):
    svc, fakes = setup()
    async with Client(build_server(svc), elicitation_callback=answering(action, send)) as client:
        res = await client.call_tool("send_message", ARGS)
    assert "Not sent" in text(res)
    assert fakes["work"].sent == []


@pytest.mark.asyncio
async def test_client_without_elicitation_fails_closed():
    svc, fakes = setup()
    async with Client(build_server(svc)) as client:  # no elicitation callback = capability not declared
        res = await client.call_tool("send_message", ARGS)
    assert res.is_error and "--no-confirm-send" in text(res)
    assert fakes["work"].sent == []


@pytest.mark.asyncio
async def test_no_confirm_send_mode_sends_without_asking():
    svc, fakes = setup()
    async with Client(build_server(svc, confirm_send=False)) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
        res = await client.call_tool("send_message", ARGS)
    assert "without its own confirmation" in tools["send_message"].description
    assert not res.is_error and len(fakes["work"].sent) == 1


@pytest.mark.asyncio
async def test_refused_send_never_reaches_the_human():
    svc, fakes = setup()
    seen = []
    async with Client(build_server(svc), elicitation_callback=answering("accept", seen=seen)) as client:
        res = await client.call_tool("send_message", {**ARGS, "account": "club"})
        res2 = await client.call_tool("send_message", {**ARGS, "to": MANY})
    assert res.is_error and "not allowed to send" in text(res)
    assert res2.is_error and "too many recipients" in text(res2)
    assert seen == [] and fakes["club"].sent == [] and fakes["work"].sent == []


@pytest.mark.asyncio
async def test_send_draft_confirmation_over_protocol():
    svc, fakes = setup()
    fakes["work"].draft_headers = {"d9": {"To": "carol@example.com", "Subject": "Report"}}
    seen = []
    async with Client(build_server(svc), elicitation_callback=answering("accept", seen=seen)) as client:
        res = await client.call_tool("send_draft", {"account": "work", "draft_id": "d9"})
    assert not res.is_error and fakes["work"].sent_drafts == ["d9"]
    assert "SEND existing draft d9" in seen[0] and "Report" in seen[0]
