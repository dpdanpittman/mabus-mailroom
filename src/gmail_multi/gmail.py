"""A deliberately small Gmail REST client (only the calls this server needs).

Calls used, with the narrowest scope each accepts:

* users.getProfile, messages.list/get, threads.get, labels.list: gmail.readonly
* messages.attachments.get: gmail.readonly
* drafts.create, drafts.get, drafts.send, messages.send: gmail.compose
"""

from __future__ import annotations

from typing import Awaitable, Callable

import httpx

API = "https://gmail.googleapis.com/gmail/v1/users/me"
_METADATA_HEADERS = ["From", "To", "Subject", "Date"]


class GmailError(Exception):
    def __init__(self, status: int, message: str):
        super().__init__(f"Gmail API {status}: {message}")
        self.status = status


TokenGetter = Callable[[bool], Awaitable[str]]


class GmailClient:
    def __init__(self, http: httpx.AsyncClient, get_token: TokenGetter):
        self._http = http
        self._get_token = get_token

    async def _request(self, method: str, path: str, *, params=None, json=None, retry_auth: bool = True,
                       max_bytes: int | None = None) -> dict:
        token = await self._get_token(False)
        resp = await self._http.request(method, f"{API}{path}", params=params, json=json, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code == 401 and retry_auth:
            # The request was rejected before doing anything; safe to repeat once.
            token = await self._get_token(True)
            resp = await self._http.request(method, f"{API}{path}", params=params, json=json, headers={"Authorization": f"Bearer {token}"})
        if resp.status_code >= 400:
            try:
                msg = resp.json().get("error", {}).get("message", resp.reason_phrase)
            except ValueError:
                msg = resp.reason_phrase
            raise GmailError(resp.status_code, str(msg)[:300])
        if max_bytes is not None and len(resp.content) > max_bytes:
            raise GmailError(413, f"response larger than the {max_bytes}-byte cap")
        return resp.json() if resp.content else {}

    async def profile(self) -> dict:
        return await self._request("GET", "/profile")

    async def list_message_ids(self, query: str, max_results: int) -> list[str]:
        data = await self._request("GET", "/messages", params={"q": query, "maxResults": max_results})
        return [m["id"] for m in data.get("messages") or []]

    async def get_message(self, message_id: str, fmt: str = "full") -> dict:
        params: list[tuple[str, str]] = [("format", fmt)]
        if fmt == "metadata":
            params += [("metadataHeaders", h) for h in _METADATA_HEADERS + ["Message-ID", "References"]]
        return await self._request("GET", f"/messages/{message_id}", params=params)

    async def get_thread(self, thread_id: str) -> dict:
        return await self._request("GET", f"/threads/{thread_id}", params={"format": "full"})

    async def list_labels(self) -> list[dict]:
        data = await self._request("GET", "/labels")
        return data.get("labels") or []

    async def create_draft(self, raw_b64url: str, thread_id: str | None = None) -> dict:
        message: dict = {"raw": raw_b64url}
        if thread_id:
            message["threadId"] = thread_id
        # Never retried: a mutation runs at most once per tool call.
        return await self._request("POST", "/drafts", json={"message": message}, retry_auth=False)

    async def get_draft(self, draft_id: str) -> dict:
        params = [("format", "metadata")] + [("metadataHeaders", h) for h in ("To", "Cc", "Bcc", "Subject")]
        return await self._request("GET", f"/drafts/{draft_id}", params=params)

    async def get_attachment(self, message_id: str, attachment_id: str, max_bytes: int) -> dict:
        # base64url adds a third; allow for it plus JSON framing.
        return await self._request("GET", f"/messages/{message_id}/attachments/{attachment_id}",
                                   max_bytes=max_bytes * 4 // 3 + 4096)

    async def send_message(self, raw_b64url: str, thread_id: str | None = None) -> dict:
        message: dict = {"raw": raw_b64url}
        if thread_id:
            message["threadId"] = thread_id
        # Never retried: sending runs at most once per tool call.
        return await self._request("POST", "/messages/send", json=message, retry_auth=False)

    async def send_draft(self, draft_id: str) -> dict:
        return await self._request("POST", "/drafts/send", json={"id": draft_id}, retry_auth=False)
