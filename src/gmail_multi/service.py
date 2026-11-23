"""Account routing, permission gating and fan-out search.

This layer holds every rule; the server module is a thin MCP wrapper over it
(it only borrows the SDK's ToolError base class), and the tests drive it with
fake Gmail clients.
"""

from __future__ import annotations

import asyncio
import base64
import re
import time
from email.message import EmailMessage
from email.utils import getaddresses
from typing import Protocol

from mcp.server.mcpserver.exceptions import ToolError as _MCPToolError

from .config import ALL, Account, Config
from .content import (DEFAULT_LIMITS, Limits, attachment_kind, cap_field, clean_text, decode_text_bytes,
                      find_part, header_map, html_to_text, message_full, message_summary, pdf_available,
                      pdf_to_text, truncate, wrap_untrusted)


class ToolError(_MCPToolError):
    """A problem the model can fix (bad alias, not permitted, bad input).

    Subclasses the MCP SDK's ToolError so that, raised from a tool or a resolver,
    it becomes an ``isError`` result the model can read.
    """


class GmailLike(Protocol):
    async def list_message_ids(self, query: str, max_results: int) -> list[str]: ...
    async def get_message(self, message_id: str, fmt: str = "full") -> dict: ...
    async def get_thread(self, thread_id: str) -> dict: ...
    async def list_labels(self) -> list[dict]: ...
    async def create_draft(self, raw_b64url: str, thread_id: str | None = None) -> dict: ...
    async def get_draft(self, draft_id: str) -> dict: ...
    async def send_message(self, raw_b64url: str, thread_id: str | None = None) -> dict: ...
    async def send_draft(self, draft_id: str) -> dict: ...
    async def get_attachment(self, message_id: str, attachment_id: str, max_bytes: int) -> dict: ...


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
                 concurrency: int = 8, rate_limiter: RateLimiter | None = None, read_only: bool = False,
                 unavailable: dict[str, str] | None = None):
        self.config = config
        self.unavailable = unavailable or {}
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
        if acc is not None and alias in self.unavailable:
            raise ToolError(f"account {alias!r} is unavailable: {self.unavailable[alias]}")
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
             "can_draft": a.can_draft and not self.read_only,
             "can_send": a.can_send and not self.read_only}
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
        errors: list[dict] = []
        if account == ALL:
            aliases = [a for a in sorted(self.config.accounts) if a in self.clients]
            errors += [{"account": a, "error": f"unavailable: {r}"} for a, r in sorted(self.unavailable.items())]
            if not aliases and not errors:
                raise ToolError("no accounts configured; run `gmail-multi add <alias>`")
        else:
            aliases = [self.account(account).alias]
        results = await asyncio.gather(
            *(self._search_one(a, query, max_results) for a in aliases), return_exceptions=True
        )
        merged: list[dict] = []
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

    # ------------------------------------------------------------ outgoing mail

    def _validate_outgoing(self, to: str, subject: str, body: str, cc: str, bcc: str) -> list[str]:
        for name, value in (("to", to), ("cc", cc), ("bcc", bcc), ("subject", subject)):
            if not isinstance(value, str) or "\r" in value or "\n" in value:
                raise ToolError(f"{name} must be a single line")
            if len(value) > 2000:
                raise ToolError(f"{name} is too long")
        if not isinstance(body, str) or len(body) > self.limits.max_draft_body_chars:
            raise ToolError(f"body must be text of at most {self.limits.max_draft_body_chars} characters")
        return self._check_recipients([to, cc, bcc])

    def _check_recipients(self, fields: list[str]) -> list[str]:
        recipients = [addr for _, addr in getaddresses([f for f in fields if f and f.strip()]) if addr]
        if not recipients or any("@" not in r for r in recipients):
            raise ToolError("give at least one valid recipient address in to, cc or bcc")
        if len(recipients) > self.limits.max_recipients:
            raise ToolError(f"too many recipients ({len(recipients)}); the limit is {self.limits.max_recipients}")
        return recipients

    async def _build(self, client: GmailLike, to, subject, body, cc, bcc, reply_to_message_id) -> tuple[str, str | None]:
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
        return base64.urlsafe_b64encode(msg.as_bytes()).decode("ascii"), thread_id

    def _draft_account(self, account: str) -> Account:
        acc = self.account(account)
        if self.read_only:
            raise ToolError("this server was started with --read-only; drafts are disabled")
        if not acc.can_draft:
            raise ToolError(
                f"account {acc.alias!r} is read-only. To allow drafts, the owner runs "
                f"`gmail-multi add {acc.alias} --drafts` (re-authorizes with the gmail.compose scope)."
            )
        return acc

    def send_account(self, account: str) -> Account:
        """Gate for both send tools; called before asking the human and again before sending."""
        acc = self.account(account)
        if self.read_only:
            raise ToolError("this server was started with --read-only; sending is disabled")
        if not acc.can_send:
            raise ToolError(
                f"account {acc.alias!r} is not allowed to send (mode: {acc.mode}). To allow it, the owner runs "
                f"`gmail-multi add {acc.alias} --send`."
            )
        return acc

    async def create_draft(self, account: str, to: str, subject: str, body: str, cc: str = "", bcc: str = "",
                           reply_to_message_id: str = "") -> str:
        self._rate.check()
        acc = self._draft_account(account)
        self._validate_outgoing(to, subject, body, cc, bcc)
        client = self.clients[acc.alias]
        raw, thread_id = await self._build(client, to, subject, body, cc, bcc, reply_to_message_id)
        draft = await self._call(client.create_draft(raw, thread_id))
        return f"Draft created in account {acc.alias!r} (draft id {draft.get('id')}). It has NOT been sent."

    def preview_send(self, account: str, to: str, subject: str, body: str, cc: str = "", bcc: str = "",
                     reply_to_message_id: str = "") -> str:
        """Validate a send and return the text shown to the human for confirmation."""
        acc = self.send_account(account)
        recipients = self._validate_outgoing(to, subject, body, cc, bcc)
        if reply_to_message_id:
            _check_id(reply_to_message_id, "reply_to_message_id")
        preview = body if len(body) <= 1500 else body[:1500] + f"\n[... {len(body) - 1500} more characters]"
        lines = [
            f"SEND a real email from {acc.email or acc.alias} (account '{acc.alias}')?",
            f"To: {to}",
            *([f"Cc: {cc}"] if cc else []),
            *([f"Bcc: {bcc}"] if bcc else []),
            f"Subject: {subject}",
            f"Recipients: {len(recipients)}" + (" (reply in existing thread)" if reply_to_message_id else ""),
            "",
            clean_text(preview),
        ]
        return "\n".join(lines)

    async def send_message(self, account: str, to: str, subject: str, body: str, cc: str = "", bcc: str = "",
                           reply_to_message_id: str = "") -> str:
        self._rate.check()
        acc = self.send_account(account)
        recipients = self._validate_outgoing(to, subject, body, cc, bcc)
        client = self.clients[acc.alias]
        raw, thread_id = await self._build(client, to, subject, body, cc, bcc, reply_to_message_id)
        sent = await self._call(client.send_message(raw, thread_id))
        return (f"SENT from account {acc.alias!r} to {len(recipients)} recipient(s). "
                f"Message id {sent.get('id')}, thread {sent.get('threadId')}.")

    async def _draft_recipients(self, acc: Account, draft_id: str) -> tuple[dict, list[str]]:
        _check_id(draft_id, "draft_id")
        draft = await self._call(self.clients[acc.alias].get_draft(draft_id))
        headers = header_map((draft.get("message") or {}).get("payload") or {})
        for k in ("to", "cc", "bcc"):
            if "\n" in headers.get(k, "") or "\r" in headers.get(k, ""):
                raise ToolError("draft has malformed recipient headers")
        recipients = self._check_recipients([headers.get("to", ""), headers.get("cc", ""), headers.get("bcc", "")])
        return headers, recipients

    async def preview_send_draft(self, account: str, draft_id: str) -> str:
        acc = self.send_account(account)
        headers, recipients = await self._draft_recipients(acc, draft_id)
        lines = [
            f"SEND existing draft {draft_id} from {acc.email or acc.alias} (account '{acc.alias}')?",
            f"To: {cap_field(headers.get('to'), 1000)}",
            *([f"Cc: {cap_field(headers.get('cc'), 1000)}"] if headers.get("cc") else []),
            *([f"Bcc: {cap_field(headers.get('bcc'), 1000)}"] if headers.get("bcc") else []),
            f"Subject: {cap_field(headers.get('subject'), 500)}",
            f"Recipients: {len(recipients)}",
            "Open the draft in Gmail to read the full body before confirming.",
        ]
        return "\n".join(lines)

    async def send_draft(self, account: str, draft_id: str) -> str:
        self._rate.check()
        acc = self.send_account(account)
        _, recipients = await self._draft_recipients(acc, draft_id)  # re-check right before sending
        sent = await self._call(self.clients[acc.alias].send_draft(draft_id))
        return (f"SENT draft {draft_id} from account {acc.alias!r} to {len(recipients)} recipient(s). "
                f"Message id {sent.get('id')}.")

    # ------------------------------------------------------------ attachments

    async def get_attachment(self, account: str, message_id: str, part_id: str) -> str:
        self._rate.check()
        acc = self.account(account)
        _check_id(message_id, "message_id")
        if not isinstance(part_id, str) or not re.match(r"^[0-9][0-9.]{0,30}$", part_id):
            raise ToolError("part_id must be the part_id listed under attachments by get_message")
        client = self.clients[acc.alias]
        msg = await self._call(client.get_message(message_id, "full"))
        part = find_part(msg.get("payload") or {}, part_id)
        if part is None:
            raise ToolError(f"message {message_id} has no attachment with part_id {part_id}")
        body = part.get("body") or {}
        size = int(body.get("size") or 0)
        mime = part.get("mimeType") or ""
        info = {"account": acc.alias, "message_id": message_id, "part_id": part_id,
                "filename": cap_field(part.get("filename"), 255), "mime_type": cap_field(mime, 100), "size": size}
        kind = attachment_kind(mime, part.get("filename") or "")

        def meta_only(reason: str) -> str:
            return wrap_untrusted("attachment", {**info, "text": None, "note": reason})

        if kind == "other":
            return meta_only("no text extract for this file type; metadata only")
        if kind == "pdf" and not pdf_available():
            return meta_only("PDF text extraction needs the optional 'pypdf' package; metadata only")
        cap = self.limits.attachment_download_bytes
        if size > cap:
            return meta_only(f"larger than the {cap // (1024 * 1024)} MB download cap; metadata only")
        if body.get("data"):
            data = _b64url(body["data"])
        elif body.get("attachmentId"):
            fetched = await self._call(client.get_attachment(message_id, body["attachmentId"], cap))
            data = _b64url(fetched.get("data") or "")
        else:
            return meta_only("attachment has no content")
        if len(data) > cap:
            return meta_only(f"larger than the {cap // (1024 * 1024)} MB download cap; metadata only")

        limit = self.limits.attachment_text_chars
        note = None
        if kind == "pdf":
            try:
                text, pages = await asyncio.wait_for(
                    asyncio.to_thread(pdf_to_text, data, self.limits.pdf_max_pages, limit), timeout=20)
                note = f"PDF with {pages} page(s); text from up to {self.limits.pdf_max_pages} pages"
            except asyncio.TimeoutError:
                return meta_only("PDF text extraction timed out; metadata only")
            except Exception as exc:  # malformed or hostile PDF
                return meta_only(f"could not extract PDF text ({type(exc).__name__}); metadata only")
        else:
            text = decode_text_bytes(data)
            if kind == "html":
                text = html_to_text(text)
        text, truncated = truncate(clean_text(text), limit)
        return wrap_untrusted("attachment", {**info, "text": text, "text_truncated": truncated,
                                             **({"note": note} if note else {})})


def _b64url(data: str) -> bytes:
    try:
        return base64.urlsafe_b64decode(data + "=" * (-len(data) % 4))
    except (ValueError, TypeError):
        return b""


def _short_error(exc: BaseException) -> str:
    text = f"{type(exc).__name__}: {exc}"
    return text[:300]
