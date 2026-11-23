"""Account routing, permission gating and fan-out search.

This layer knows nothing about MCP; the server module is a thin wrapper over it,
and the tests drive it with fake Gmail clients.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Protocol

from .config import ALL, Account, Config
from .content import DEFAULT_LIMITS, Limits, header_map, message_full, message_summary, wrap_untrusted


class ToolError(Exception):
    """A problem the model can fix (bad alias, not permitted, bad input)."""


class GmailLike(Protocol):
    async def list_message_ids(self, query: str, max_results: int) -> list[str]: ...
    async def get_message(self, message_id: str, fmt: str = "full") -> dict: ...
    async def get_thread(self, thread_id: str) -> dict: ...
    async def list_labels(self) -> list[dict]: ...
    async def create_draft(self, raw_b64url: str, thread_id: str | None = None) -> dict: ...


_ID_RE = re.compile(r"^[A-Za-z0-9_-]{1,128}$")


def _check_id(value: str, what: str) -> str:
    if not isinstance(value, str) or not _ID_RE.match(value):
        raise ToolError(f"{what} must be the id string returned by search or get_thread")
    return value


class RateLimiter:
    """Sliding one-minute window over tool calls (MCP spec: servers rate-limit tools)."""

    def __init__(self, per_minute: int = 120):
        self.per_minute = per_minute
        self._calls: list[float] = []

    def check(self) -> None:
        now = time.monotonic()
        self._calls = [t for t in self._calls if now - t < 60]
        if len(self._calls) >= self.per_minute:
            raise ToolError(f"rate limit: more than {self.per_minute} gmail-multi calls in a minute; wait and retry")
        self._calls.append(now)


class MultiGmail:
    def __init__(self, config: Config, clients: dict[str, GmailLike], limits: Limits = DEFAULT_LIMITS,
                 concurrency: int = 8, rate_limiter: RateLimiter | None = None, read_only: bool = False):
        self.config = config
        self.clients = clients
        self.limits = limits
        self.read_only = read_only
        self._sem = asyncio.Semaphore(concurrency)
        self._rate = rate_limiter or RateLimiter()

    # ------------------------------------------------------------ routing

    def account(self, alias: str, *, allow_all: bool = False) -> Account:
        if alias == ALL:
            if allow_all:
                raise AssertionError("caller handles 'all'")
            raise ToolError("'all' is only accepted by search; name one account alias")
        acc = self.config.accounts.get(alias)
        if acc is None or alias not in self.clients:
            known = ", ".join(sorted(self.config.accounts)) or "(none configured)"
            raise ToolError(f"unknown account {alias!r}; known aliases: {known}")
        return acc

    async def _call(self, coro):
        async with self._sem:
            return await coro

    # ------------------------------------------------------------ tools

    def list_accounts(self) -> list[dict]:
        self._rate.check()
        return [
            {"alias": a.alias, "email": a.email, "mode": a.mode,
             "can_draft": a.can_draft and not self.read_only}
            for a in sorted(self.config.accounts.values(), key=lambda a: a.alias)
        ]

    async def _search_one(self, alias: str, query: str, max_results: int) -> list[dict]:
        client = self.clients[alias]
        ids = await self._call(client.list_message_ids(query, max_results))
        msgs = await asyncio.gather(*(self._call(client.get_message(i, "metadata")) for i in ids))
        return [message_summary(alias, m, self.limits) for m in msgs]

    async def search(self, query: str, account: str, max_results: int = 20) -> str:
        self._rate.check()
        if not isinstance(query, str) or len(query) > 1000:
            raise ToolError("query must be a Gmail search string of at most 1000 characters")
        max_results = max(1, min(int(max_results), self.limits.max_search_results))
        aliases = sorted(self.config.accounts) if account == ALL else [self.account(account).alias]
        if not aliases:
            raise ToolError("no accounts configured; run `gmail-multi add <alias>`")
        results = await asyncio.gather(
            *(self._search_one(a, query, max_results) for a in aliases), return_exceptions=True
        )
        merged: list[dict] = []
        errors: list[dict] = []
        for alias, res in zip(aliases, results):
            if isinstance(res, BaseException):
                errors.append({"account": alias, "error": _short_error(res)})
            else:
                merged.extend(res)
        merged.sort(key=lambda m: (m["internal_date"], m["account"], m["id"] or ""), reverse=True)
        data = {
            "query": query,
            "accounts": aliases,
            "returned": min(len(merged), max_results),
            "messages": merged[:max_results],
            "errors": errors,
        }
        return wrap_untrusted("search", data)

    async def get_message(self, account: str, message_id: str) -> str:
        self._rate.check()
        acc = self.account(account)
        _check_id(message_id, "message_id")
        msg = await self._call(self.clients[acc.alias].get_message(message_id, "full"))
        view, _ = message_full(acc.alias, msg, self.limits.response_chars, self.limits)
        return wrap_untrusted("message", view)

    async def get_thread(self, account: str, thread_id: str) -> str:
        self._rate.check()
        acc = self.account(account)
        _check_id(thread_id, "thread_id")
        thread = await self._call(self.clients[acc.alias].get_thread(thread_id))
        budget = self.limits.response_chars
        views = []
        for msg in thread.get("messages") or []:
            view, used = message_full(acc.alias, msg, budget, self.limits)
            budget -= used
            views.append(view)
        data = {"account": acc.alias, "thread_id": thread.get("id"), "message_count": len(views), "messages": views}
        return wrap_untrusted("thread", data)

    async def list_labels(self, account: str) -> str:
        self._rate.check()
        acc = self.account(account)
        labels = await self._call(self.clients[acc.alias].list_labels())
        data = {
            "account": acc.alias,
            "labels": sorted(
                ({"id": l.get("id"), "name": (l.get("name") or "")[:200], "type": l.get("type")} for l in labels),
                key=lambda l: (l["type"] != "system", l["name"].lower()),
            ),
        }
        # Label names are user/third-party controlled too (filters can create them).
        return wrap_untrusted("labels", data)

    async def create_draft(self, account: str, to: str, subject: str, body: str, cc: str = "", bcc: str = "",
                           reply_to_message_id: str = "") -> str:
        self._rate.check()
        acc = self.account(account)
        if self.read_only:
            raise ToolError("this server was started with --read-only; drafts are disabled")
        if not acc.can_draft:
            raise ToolError(
                f"account {acc.alias!r} is read-only. To allow drafts, the owner runs "
                f"`gmail-multi add {acc.alias} --drafts` (re-authorizes with the gmail.compose scope)."
            )
        for name, value in (("to", to), ("cc", cc), ("bcc", bcc), ("subject", subject)):
            if not isinstance(value, str) or "\r" in value or "\n" in value:
                raise ToolError(f"{name} must be a single line")
            if len(value) > 2000:
                raise ToolError(f"{name} is too long")
        if not isinstance(body, str) or len(body) > self.limits.max_draft_body_chars:
            raise ToolError(f"body must be text of at most {self.limits.max_draft_body_chars} characters")
        recipients = [addr for _, addr in getaddresses([f for f in (to, cc, bcc) if f.strip()]) if addr]
        if not recipients or any("@" not in r for r in recipients):
            raise ToolError("give at least one valid recipient address in to, cc or bcc")

        client = self.clients[acc.alias]
        msg = EmailMessage()
        msg["To"] = to
        if cc:
            msg["Cc"] = cc
        if bcc:
            msg["Bcc"] = bcc
        msg["Subject"] = subject
        thread_id = None
        if reply_to_message_id:
            _check_id(reply_to_message_id, "reply_to_message_id")
            original = await self._call(client.get_message(reply_to_message_id, "metadata"))
            headers = header_map(original.get("payload") or {})
            mid = headers.get("message-id")
            if mid and "\n" not in mid and "\r" not in mid:
                msg["In-Reply-To"] = mid
                refs = headers.get("references", "")
                msg["References"] = f"{refs} {mid}".strip() if "\n" not in refs and "\r" not in refs else mid
            thread_id = original.get("threadId")
        msg.set_content(body)
        raw = base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii")
        draft = await self._call(client.create_draft(raw, thread_id))
        return (
            f"Draft created in account {acc.alias!r} (draft id {draft.get('id')}). "
            "It has NOT been sent; the owner reviews and sends it from Gmail."
        )


def _short_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:300]
