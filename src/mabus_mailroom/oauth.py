"""Google OAuth 2.0 for installed (desktop) apps: loopback redirect + PKCE (S256).

Follows https://developers.google.com/identity/protocols/oauth2/native-app :
listen on 127.0.0.1 on a free port, send a ``state`` value against CSRF, use a
PKCE code verifier with the S256 challenge, request every scope up front
(installed apps get no incremental authorization), then check which scopes the
user actually granted.
"""

from __future__ import annotations

import asyncio
import base64
import logging
import hashlib
import secrets
import threading
import time
import urllib.parse
from dataclasses import dataclass
from http.server import BaseHTTPRequestHandler, HTTPServer
from typing import Callable

import httpx

AUTH_URL = "https://accounts.google.com/o/oauth2/v2/auth"
TOKEN_URL = "https://oauth2.googleapis.com/token"
REVOKE_URL = "https://oauth2.googleapis.com/revoke"

log = logging.getLogger("mabus_mailroom")


class OAuthError(Exception):
    pass


class ReauthRequired(OAuthError):
    """The refresh token no longer works (revoked, expired, or a Testing-mode 7-day expiry)."""


@dataclass
class OAuthClient:
    client_id: str
    client_secret: str | None = None

    @classmethod
    def from_google_json(cls, data: dict) -> "OAuthClient":
        """Accept the JSON Google Cloud console downloads for a Desktop client."""
        block = data.get("installed")
        if block is None:
            if "web" in data:
                raise OAuthError("this is a 'Web application' client; create a 'Desktop app' client")
            block = data
        cid = block.get("client_id")
        if not cid or not str(cid).endswith(".apps.googleusercontent.com"):
            raise OAuthError("client file has no valid client_id")
        return cls(client_id=cid, client_secret=block.get("client_secret"))

    def to_dict(self) -> dict:
        return {"client_id": self.client_id, "client_secret": self.client_secret}


def make_pkce_pair() -> tuple[str, str]:
    verifier = secrets.token_urlsafe(64)[:96]  # 43..128 unreserved characters
    digest = hashlib.sha256(verifier.encode("ascii")).digest()
    challenge = base64.urlsafe_b64encode(digest).rstrip(b"=").decode("ascii")
    return verifier, challenge


def build_auth_url(client: OAuthClient, redirect_uri: str, scopes: list[str], state: str, challenge: str, login_hint: str | None = None) -> str:
    params = {
        "client_id": client.client_id,
        "redirect_uri": redirect_uri,
        "response_type": "code",
        "scope": " ".join(scopes),
        "state": state,
        "code_challenge": challenge,
        "code_challenge_method": "S256",
        "access_type": "offline",
        "prompt": "consent select_account",
    }
    if login_hint:
        params["login_hint"] = login_hint
    return f"{AUTH_URL}?{urllib.parse.urlencode(params)}"


_DONE_PAGE = (
    b"<!doctype html><meta charset='utf-8'><title>Mabus Mailroom</title>"
    b"<p>Mabus Mailroom: authorization received. You can close this tab.</p>"
)


def wait_for_redirect(port: int, expected_state: str, timeout: float, on_ready: Callable[[int], None]) -> str:
    """Run a one-shot loopback HTTP server; return the authorization code."""
    result: dict[str, str] = {}
    done = threading.Event()

    class Handler(BaseHTTPRequestHandler):
        def do_GET(self) -> None:  # noqa: N802
            parsed = urllib.parse.urlparse(self.path)
            if parsed.path != "/":
                self.send_response(404)
                self.end_headers()
                return
            q = urllib.parse.parse_qs(parsed.query)
            state = (q.get("state") or [""])[0]
            if not secrets.compare_digest(state, expected_state):
                self.send_response(400)
                self.end_headers()
                self.wfile.write(b"state mismatch")
                return
            if "error" in q:
                result["error"] = q["error"][0]
            elif "code" in q:
                result["code"] = q["code"][0]
            else:
                self.send_response(400)
                self.end_headers()
                return
            self.send_response(200)
            self.send_header("Content-Type", "text/html; charset=utf-8")
            self.end_headers()
            self.wfile.write(_DONE_PAGE)
            done.set()

        def log_message(self, *args) -> None:  # keep stdout/stderr clean
            pass

    server = HTTPServer(("127.0.0.1", port), Handler)
    server.timeout = 0.5
    on_ready(server.server_address[1])
    deadline = time.monotonic() + timeout
    try:
        while not done.is_set():
            if time.monotonic() > deadline:
                raise OAuthError("timed out waiting for the browser redirect")
            server.handle_request()
    finally:
        server.server_close()
    if "error" in result:
        raise OAuthError(f"authorization refused: {result['error']}")
    return result["code"]


async def exchange_code(http: httpx.AsyncClient, client: OAuthClient, code: str, verifier: str, redirect_uri: str) -> dict:
    data = {
        "client_id": client.client_id,
        "code": code,
        "code_verifier": verifier,
        "grant_type": "authorization_code",
        "redirect_uri": redirect_uri,
    }
    if client.client_secret:
        data["client_secret"] = client.client_secret
    resp = await http.post(TOKEN_URL, data=data)
    body = _json(resp)
    if resp.status_code != 200:
        raise OAuthError(f"token exchange failed: {body.get('error', resp.status_code)}")
    if not body.get("refresh_token"):
        raise OAuthError("Google returned no refresh token; remove the app's access at myaccount.google.com/permissions and retry")
    return body


async def refresh_access_token(http: httpx.AsyncClient, client: OAuthClient, refresh_token: str) -> dict:
    data = {
        "client_id": client.client_id,
        "grant_type": "refresh_token",
        "refresh_token": refresh_token,
    }
    if client.client_secret:
        data["client_secret"] = client.client_secret
    resp = await http.post(TOKEN_URL, data=data)
    body = _json(resp)
    if resp.status_code != 200:
        if body.get("error") == "invalid_grant":
            raise ReauthRequired("refresh token rejected (invalid_grant)")
        raise OAuthError(f"token refresh failed: {body.get('error', resp.status_code)}")
    return body


async def revoke_token(http: httpx.AsyncClient, token: str) -> bool:
    resp = await http.post(REVOKE_URL, data={"token": token})
    return resp.status_code == 200


def _json(resp: httpx.Response) -> dict:
    try:
        body = resp.json()
        return body if isinstance(body, dict) else {}
    except ValueError:
        return {}


class AccessTokenCache:
    """Per-account access token, refreshed on demand and shared across tool calls."""

    def __init__(self, http: httpx.AsyncClient, client: OAuthClient, refresh_token: str, on_rotate: Callable[[str], None] | None = None):
        self._http = http
        self._client = client
        self._refresh_token = refresh_token
        self._on_rotate = on_rotate
        self._token: str | None = None
        self._expires = 0.0
        self._lock = asyncio.Lock()

    async def get(self, force: bool = False) -> str:
        async with self._lock:
            if force or not self._token or time.monotonic() > self._expires - 60:
                body = await refresh_access_token(self._http, self._client, self._refresh_token)
                self._token = body["access_token"]
                self._expires = time.monotonic() + float(body.get("expires_in", 3600))
                new_rt = body.get("refresh_token")
                if new_rt and new_rt != self._refresh_token:
                    self._refresh_token = new_rt
                    if self._on_rotate:
                        # Saving the new refresh token can fail (a read-only token store, a locked vault). The new
                        # token is already in use in memory, so this request and the rest of this run keep working.
                        # Never let the save failure fail the request; say what the owner has to do.
                        try:
                            self._on_rotate(new_rt)
                        except Exception as exc:  # noqa: BLE001 - any store failure is handled the same way
                            log.warning(
                                "Google issued a new refresh token but it could not be saved (%s). This run keeps "
                                "working; after a restart the account needs `mabus-mailroom add` again, run where "
                                "the token store is writable.", type(exc).__name__)
            return self._token
