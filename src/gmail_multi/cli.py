"""Command line: gmail-multi add | list | remove | serve."""

from __future__ import annotations

import argparse
import asyncio
import json
import logging
import secrets
import sys
import webbrowser
from pathlib import Path

import httpx

from . import __version__
from .config import MODE_DRAFTS, MODE_READONLY, MODE_SCOPES, SCOPE_COMPOSE, SCOPE_READONLY, Account, Config, ConfigError, validate_alias
from .gmail import GmailClient
from .oauth import (OAuthClient, OAuthError, build_auth_url, exchange_code, make_pkce_pair, revoke_token,
                    wait_for_redirect)
from .tokenstore import CLIENT_KEY, StoreError, account_key, open_store, resolve_store_kind


def _err(msg: str) -> int:
    print(f"gmail-multi: {msg}", file=sys.stderr)
    return 1


def _open_store(cfg: Config, requested: str | None = None):
    kind = resolve_store_kind(requested, cfg.store)
    store = open_store(kind)
    if cfg.store == "auto":
        cfg.store = kind
    return store


# ----------------------------------------------------------------------------- add


def cmd_add(args) -> int:
    alias = validate_alias(args.alias)
    cfg = Config.load()
    store = _open_store(cfg, args.store)

    client_data = store.get(CLIENT_KEY)
    if args.client_file:
        data = json.loads(Path(args.client_file).expanduser().read_text(encoding="utf-8"))
        client = OAuthClient.from_google_json(data)
        store.set(CLIENT_KEY, client.to_dict())
        print("Stored your OAuth client in the token store. You may delete the downloaded JSON file now.")
    elif client_data:
        client = OAuthClient(**client_data)
    else:
        return _err("no OAuth client yet: pass --client-file path/to/client_secret.json (a Desktop app client)")

    mode = MODE_DRAFTS if args.drafts else MODE_READONLY
    scopes = MODE_SCOPES[mode]
    verifier, challenge = make_pkce_pair()
    state = secrets.token_urlsafe(24)

    def on_ready(port: int) -> None:
        on_ready.redirect = f"http://127.0.0.1:{port}"
        url = build_auth_url(client, on_ready.redirect, scopes, state, challenge, args.login_hint)
        print(f"\nAuthorize account '{alias}' ({mode}). Open this URL in a browser on THIS machine:\n\n{url}\n", file=sys.stderr)
        if args.port:
            print(f"(Headless? From your desktop: ssh -L {port}:127.0.0.1:{port} <this-host>, then open the URL.)", file=sys.stderr)
        if not args.no_browser:
            webbrowser.open(url)

    try:
        code = wait_for_redirect(args.port or 0, state, args.timeout, on_ready)
    except OAuthError as exc:
        return _err(str(exc))

    async def finish() -> int:
        async with httpx.AsyncClient(timeout=30) as http:
            tokens = await exchange_code(http, client, code, verifier, on_ready.redirect)
            granted = set((tokens.get("scope") or "").split())
            if SCOPE_READONLY not in granted:
                await revoke_token(http, tokens["refresh_token"])
                return _err("the gmail.readonly permission was not granted; nothing was saved")
            final_mode = mode
            if mode == MODE_DRAFTS and SCOPE_COMPOSE not in granted:
                print("Drafts permission was not granted; saving this account as read-only.", file=sys.stderr)
                final_mode = MODE_READONLY
            access = tokens["access_token"]

            async def fixed(_force: bool) -> str:
                return access

            profile = await GmailClient(http, fixed).profile()
            email = profile.get("emailAddress", "")
            for other in cfg.accounts.values():
                if other.alias != alias and other.email.lower() == email.lower():
                    print(f"Note: {email} is also configured as '{other.alias}'.", file=sys.stderr)
            # No revoke of a previous token here: Google revokes the whole grant for this
            # user and project, which would also kill the token we just received.
            store.set(account_key(alias), {
                "refresh_token": tokens["refresh_token"],
                "scopes": sorted(granted & {SCOPE_READONLY, SCOPE_COMPOSE}),
            })
            cfg.accounts[alias] = Account(alias=alias, email=email, mode=final_mode,
                                          scopes=sorted(granted & {SCOPE_READONLY, SCOPE_COMPOSE}))
            cfg.save()
            print(f"Added '{alias}' = {email} ({final_mode}). Tokens are in the {store.name} store.")
            return 0

    try:
        return asyncio.run(finish())
    except (OAuthError, httpx.HTTPError) as exc:
        return _err(str(exc))


# ----------------------------------------------------------------------------- list / remove


def cmd_list(args) -> int:
    cfg = Config.load()
    if not cfg.accounts:
        print("No accounts. Add one with: gmail-multi add <alias> --client-file client_secret.json")
        return 0
    print(f"store: {cfg.store}   config: {cfg.path}")
    for a in sorted(cfg.accounts.values(), key=lambda a: a.alias):
        print(f"  {a.alias:<16} {a.email:<40} {a.mode}")
    return 0


def cmd_remove(args) -> int:
    alias = validate_alias(args.alias)
    cfg = Config.load()
    if alias not in cfg.accounts:
        return _err(f"no account {alias!r}")
    store = _open_store(cfg)
    secret = store.get(account_key(alias))
    email = cfg.accounts[alias].email.lower()
    shared = [a.alias for a in cfg.accounts.values() if a.alias != alias and email and a.email.lower() == email]
    if shared and not args.keep_grant:
        print(f"Not revoking at Google: the same address is also '{', '.join(shared)}', "
              "and revoking would cut that alias off too.")
    elif secret and secret.get("refresh_token") and not args.keep_grant:
        async def rev() -> bool:
            async with httpx.AsyncClient(timeout=30) as http:
                return await revoke_token(http, secret["refresh_token"])
        try:
            ok = asyncio.run(rev())
        except httpx.HTTPError:
            ok = False
        print("Revoked the grant at Google." if ok else
              "Could not revoke at Google (already revoked or offline); remove it at myaccount.google.com/permissions.")
    store.delete(account_key(alias))
    del cfg.accounts[alias]
    cfg.save()
    if not cfg.accounts:
        store.delete(CLIENT_KEY)
        print("That was the last account; the stored OAuth client was deleted too.")
    print(f"Removed '{alias}'.")
    return 0


# ----------------------------------------------------------------------------- serve


def cmd_serve(args) -> int:
    from .server import build_server, build_service

    logging.basicConfig(level=logging.INFO, stream=sys.stderr, format="gmail-multi %(levelname)s %(message)s")
    cfg = Config.load()
    store = _open_store(cfg) if cfg.accounts else None

    async def main() -> None:
        async with httpx.AsyncClient(timeout=httpx.Timeout(30.0, connect=10.0)) as http:
            if store is None:
                from .service import MultiGmail
                service = MultiGmail(cfg, {}, read_only=True)
            else:
                service = build_service(cfg, store, http, read_only=args.read_only)
            await build_server(service).run_stdio_async()

    asyncio.run(main())
    return 0


# ----------------------------------------------------------------------------- main


def build_parser() -> argparse.ArgumentParser:
    p = argparse.ArgumentParser(prog="gmail-multi", description="MCP server for several Gmail accounts.")
    p.add_argument("--version", action="version", version=f"gmail-multi {__version__}")
    sub = p.add_subparsers(dest="cmd", required=True)

    a = sub.add_parser("add", help="authorize a Gmail account under an alias (re-run to change its mode)")
    a.add_argument("alias")
    a.add_argument("--drafts", action="store_true", help="also allow creating drafts (gmail.compose); default is read-only")
    a.add_argument("--client-file", help="Desktop-app OAuth client JSON from Google Cloud (needed the first time)")
    a.add_argument("--store", choices=["auto", "keyring", "file"], default=None, help="where tokens live (first run only)")
    a.add_argument("--port", type=int, default=0, help="fixed loopback port (for SSH forwarding); default random")
    a.add_argument("--no-browser", action="store_true", help="print the URL instead of opening a browser")
    a.add_argument("--login-hint", help="pre-select this Google address on the consent screen")
    a.add_argument("--timeout", type=float, default=300, help="seconds to wait for the browser (default 300)")
    a.set_defaults(func=cmd_add)

    ls = sub.add_parser("list", help="show configured accounts")
    ls.set_defaults(func=cmd_list)

    for name in ("remove", "revoke"):
        r = sub.add_parser(name, help="revoke an account's grant at Google and delete its tokens")
        r.add_argument("alias")
        r.add_argument("--keep-grant", action="store_true", help="only delete local tokens, do not revoke at Google")
        r.set_defaults(func=cmd_remove)

    s = sub.add_parser("serve", help="run the MCP server on stdio (MCP clients start this)")
    s.add_argument("--read-only", action="store_true", help="disable create_draft for every account")
    s.set_defaults(func=cmd_serve)
    return p


def main(argv: list[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        return args.func(args)
    except (ConfigError, StoreError, OAuthError) as exc:
        return _err(str(exc))
    except KeyboardInterrupt:
        return 130


if __name__ == "__main__":
    sys.exit(main())
