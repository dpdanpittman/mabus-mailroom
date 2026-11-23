"""Fake Gmail clients and helpers. No test talks to Google."""

from __future__ import annotations

import base64

from gmail_multi.config import MODE_DRAFTS, MODE_READONLY, MODE_SCOPES, Account, Config


def b64(text: str) -> str:
    return base64.urlsafe_b64encode(text.encode()).decode().rstrip("=")


def make_msg(mid: str, internal_date: int, subject: str = "hi", sender: str = "bob@example.com",
             body: str = "hello", html: str | None = None, thread_id: str | None = None,
             message_id_header: str | None = None) -> dict:
    headers = [
        {"name": "From", "value": sender},
        {"name": "To", "value": "alice@example.com"},
        {"name": "Subject", "value": subject},
        {"name": "Date", "value": "Mon, 2 Nov 2026 10:00:00 +0000"},
    ]
    if message_id_header:
        headers.append({"name": "Message-ID", "value": message_id_header})
    if html is not None:
        payload = {"mimeType": "text/html", "headers": headers, "body": {"data": b64(html)}}
    else:
        payload = {"mimeType": "text/plain", "headers": headers, "body": {"data": b64(body)}}
    return {"id": mid, "threadId": thread_id or f"t-{mid}", "internalDate": str(internal_date),
            "snippet": body[:100], "labelIds": ["INBOX"], "payload": payload}


class FakeGmail:
    def __init__(self, messages: list[dict] | None = None, fail: Exception | None = None, labels=None):
        self.messages = {m["id"]: m for m in messages or []}
        self.fail = fail
        self.labels = labels or [{"id": "INBOX", "name": "INBOX", "type": "system"}]
        self.calls: list[tuple] = []
        self.drafts: list[tuple[str, str | None]] = []

    async def list_message_ids(self, query, max_results):
        self.calls.append(("list", query, max_results))
        if self.fail:
            raise self.fail
        return list(self.messages)[:max_results]

    async def get_message(self, message_id, fmt="full"):
        self.calls.append(("get", message_id, fmt))
        return self.messages[message_id]

    async def get_thread(self, thread_id):
        self.calls.append(("thread", thread_id))
        msgs = [m for m in self.messages.values() if m["threadId"] == thread_id]
        return {"id": thread_id, "messages": msgs}

    async def list_labels(self):
        self.calls.append(("labels",))
        return self.labels

    async def create_draft(self, raw, thread_id=None):
        self.calls.append(("draft", thread_id))
        self.drafts.append((raw, thread_id))
        return {"id": f"d{len(self.drafts)}"}


def config_with(**modes: str) -> Config:
    accounts = {}
    for alias, mode in modes.items():
        accounts[alias] = Account(alias=alias, email=f"{alias}@example.com", mode=mode, scopes=list(MODE_SCOPES[mode]))
    return Config(accounts=accounts)


__all__ = ["FakeGmail", "make_msg", "config_with", "MODE_DRAFTS", "MODE_READONLY", "b64"]
