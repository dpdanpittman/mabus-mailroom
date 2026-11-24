"""End-to-end through the MCP protocol with an in-process client (no network)."""

import pytest
from conftest import unwrap
from fakes import MODE_DRAFTS, MODE_READONLY, FakeGmail, config_with, make_msg
from mcp import Client

from mabus_mailroom.server import build_server
from mabus_mailroom.service import MultiGmail


def server():
    svc = MultiGmail(
        config_with(home=MODE_READONLY, work=MODE_DRAFTS),
        {"home": FakeGmail([make_msg("h1", 2)]), "work": FakeGmail([make_msg("w1", 1)])},
    )
    return build_server(svc)


def text(result):
    return "".join(c.text for c in result.content if getattr(c, "type", "") == "text")


@pytest.mark.asyncio
async def test_tools_listed_with_annotations():
    async with Client(server()) as client:
        tools = {t.name: t for t in (await client.list_tools()).tools}
    assert set(tools) == {"list_accounts", "search", "get_message", "get_thread", "list_labels", "get_attachment",
                          "create_draft"}  # no send tools: no account is in send mode
    assert tools["search"].annotations.read_only_hint is True
    assert tools["create_draft"].annotations.read_only_hint is False
    assert "account" in tools["get_message"].input_schema["required"]


@pytest.mark.asyncio
async def test_search_all_over_protocol():
    async with Client(server()) as client:
        res = await client.call_tool("search", {"query": "x", "account": "all"})
    assert not res.is_error
    _, data = unwrap(text(res))
    assert [m["id"] for m in data["messages"]] == ["h1", "w1"]


@pytest.mark.asyncio
async def test_refusals_are_tool_errors_the_model_can_read():
    async with Client(server()) as client:
        res = await client.call_tool("create_draft", {"account": "home", "to": "c@example.com", "subject": "s", "body": "b"})
        assert res.is_error and "read-only" in text(res)
        res = await client.call_tool("get_message", {"account": "nope", "message_id": "x"})
        assert res.is_error and "known aliases" in text(res)
        res = await client.call_tool("list_accounts", {})
        assert not res.is_error and "work@example.com (drafts, can draft)" in text(res)
