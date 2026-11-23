import base64
import hashlib
import threading
import urllib.parse
import urllib.request

import httpx
import pytest

from gmail_multi.gmail import GmailClient
from gmail_multi.oauth import (AccessTokenCache, OAuthClient, OAuthError, ReauthRequired, build_auth_url,
                               exchange_code, make_pkce_pair, refresh_access_token, wait_for_redirect)

CLIENT = OAuthClient("123-abc.apps.googleusercontent.com", "GOCSPX-fake")


def test_pkce_is_s256():
    verifier, challenge = make_pkce_pair()
    assert 43 <= len(verifier) <= 128
    expected = base64.urlsafe_b64encode(hashlib.sha256(verifier.encode()).digest()).rstrip(b"=").decode()
    assert challenge == expected


def test_auth_url_parameters():
    url = build_auth_url(CLIENT, "http://127.0.0.1:5555", ["https://www.googleapis.com/auth/gmail.readonly"], "st", "ch")
    q = urllib.parse.parse_qs(urllib.parse.urlparse(url).query)
    assert url.startswith("https://accounts.google.com/o/oauth2/v2/auth?")
    assert q["code_challenge_method"] == ["S256"] and q["code_challenge"] == ["ch"]
    assert q["redirect_uri"] == ["http://127.0.0.1:5555"] and q["state"] == ["st"]
    assert q["scope"] == ["https://www.googleapis.com/auth/gmail.readonly"]
    assert "client_secret" not in q


def test_client_json_parsing():
    c = OAuthClient.from_google_json({"installed": {"client_id": "9.apps.googleusercontent.com", "client_secret": "s"}})
    assert c.client_id.startswith("9.")
    with pytest.raises(OAuthError, match="Desktop"):
        OAuthClient.from_google_json({"web": {"client_id": "9.apps.googleusercontent.com"}})


def _hit(port_holder, path):
    def run():
        port_holder["ready"].wait(5)
        for p in path:
            try:
                urllib.request.urlopen(f"http://127.0.0.1:{port_holder['port']}{p}", timeout=5).read()
            except Exception:
                pass
    t = threading.Thread(target=run, daemon=True)
    t.start()
    return t


def test_loopback_rejects_wrong_state_then_accepts_right_one():
    holder = {"ready": threading.Event()}

    def on_ready(port):
        holder["port"] = port
        holder["ready"].set()

    t = _hit(holder, ["/favicon.ico", "/?state=forged&code=EVIL", "/?state=good&code=REAL"])
    code = wait_for_redirect(0, "good", 10, on_ready)
    t.join(5)
    assert code == "REAL"


def test_loopback_reports_user_denial():
    holder = {"ready": threading.Event()}

    def on_ready(port):
        holder["port"] = port
        holder["ready"].set()

    _hit(holder, ["/?state=good&error=access_denied"])
    with pytest.raises(OAuthError, match="access_denied"):
        wait_for_redirect(0, "good", 10, on_ready)


def mock_http(handler):
    return httpx.AsyncClient(transport=httpx.MockTransport(handler))


@pytest.mark.asyncio
async def test_exchange_sends_verifier():
    seen = {}

    def handler(request):
        seen.update(urllib.parse.parse_qs(request.content.decode()))
        return httpx.Response(200, json={"access_token": "at", "refresh_token": "rt", "expires_in": 3599,
                                         "scope": "https://www.googleapis.com/auth/gmail.readonly"})

    async with mock_http(handler) as http:
        body = await exchange_code(http, CLIENT, "code1", "verifier1", "http://127.0.0.1:1")
    assert body["refresh_token"] == "rt"
    assert seen["code_verifier"] == ["verifier1"] and seen["grant_type"] == ["authorization_code"]


@pytest.mark.asyncio
async def test_invalid_grant_means_reauth():
    async with mock_http(lambda r: httpx.Response(400, json={"error": "invalid_grant"})) as http:
        with pytest.raises(ReauthRequired):
            await refresh_access_token(http, CLIENT, "rt")


@pytest.mark.asyncio
async def test_token_cache_refreshes_once_and_records_rotation():
    calls = {"n": 0}
    rotated = []

    def handler(request):
        calls["n"] += 1
        return httpx.Response(200, json={"access_token": f"at{calls['n']}", "expires_in": 3600, "refresh_token": "rt2"})

    async with mock_http(handler) as http:
        cache = AccessTokenCache(http, CLIENT, "rt1", rotated.append)
        assert await cache.get() == "at1"
        assert await cache.get() == "at1"
        assert await cache.get(force=True) == "at2"
    assert calls["n"] == 2 and rotated == ["rt2"]


@pytest.mark.asyncio
async def test_gmail_client_retries_reads_once_on_401_but_never_drafts():
    hits = []

    def handler(request):
        hits.append((request.method, request.url.path, request.headers["authorization"]))
        return httpx.Response(401, json={"error": {"message": "expired"}})

    tokens = iter(["t1", "t2", "t3", "t4"])

    async def get_token(force):
        return next(tokens)

    async with mock_http(handler) as http:
        g = GmailClient(http, get_token)
        with pytest.raises(Exception):
            await g.list_labels()
        assert [h[2] for h in hits] == ["Bearer t1", "Bearer t2"]
        hits.clear()
        with pytest.raises(Exception):
            await g.create_draft("cmF3")
        assert len(hits) == 1 and hits[0][0] == "POST"
