# gmail-multi

An [MCP](https://modelcontextprotocol.io) server that gives an AI agent (Claude Code,
Claude Desktop, or any other MCP client) access to **several Gmail accounts at once**.
It is read-only by default. Drafts are an opt-in you choose for each account, and
nothing is ever sent.

```
you: "anything from the landlord this week, in any of my inboxes?"
agent -> search(query="from:landlord@example.com newer_than:7d", account="all")
```

## Why

Claude's built-in Google connector reads the Gmail of **the one Google account you
connected**. Anthropic's help article says: "Claude can only access the Gmail, Calendar,
and Drive data for the Google account you've connected". To use a different account,
you disconnect and sign in again
([source](https://support.claude.com/en/articles/10166901-use-google-workspace-connectors)).
Many people have a personal address, a work address and a side-project address. This
server holds all of them, each under a short alias you pick, such as `personal`, `work`
or `club`.

Other open-source servers already handle several accounts. See [RESEARCH.md](RESEARCH.md)
for a survey with sources. gmail-multi is built around a narrower, safer default:

|                    | gmail-multi                                                                                                                                                       |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Default permission | `gmail.readonly` token per account. The token itself cannot write.                                                                                                |
| Drafts             | Opt-in per account (`gmail.compose`), with a `create_draft` tool. No send tool.                                                                                   |
| Accounts           | One server, explicit alias on every call. `all` works for search only (fan-out, merged newest first).                                                             |
| Who adds accounts  | Only you, from the CLI. The agent cannot add accounts or widen scopes.                                                                                            |
| Tokens             | OS keychain, or an AES-256-GCM file encrypted with your passphrase. Never plain text.                                                                             |
| Email content      | Treated as untrusted. HTML is turned into text, fields have size caps, and content is fenced off with random-nonce markers and a "data, not instructions" notice. |
| OAuth client       | Your own Google Cloud client. This project ships no client ID and runs no server.                                                                                 |

## Tools

| Tool            | What it does                                                      | Account argument                     |
| --------------- | ----------------------------------------------------------------- | ------------------------------------ |
| `list_accounts` | Lists aliases, addresses and modes                                | none                                 |
| `search`        | Gmail search syntax. Returns metadata and snippets, newest first  | an alias, or `all`                   |
| `get_message`   | One message: headers, plain-text body, attachment names           | alias (required)                     |
| `get_thread`    | A whole conversation, cut to a size budget                        | alias (required)                     |
| `list_labels`   | The account's labels                                              | alias (required)                     |
| `create_draft`  | Saves a draft (optionally as a reply in a thread). Never sends it | alias of an account in `drafts` mode |

`create_draft` only appears in the tool list when at least one account is in `drafts`
mode. Every call also checks the mode again.

## Setup

You need Python 3.10+ and about 10 minutes for the Google Cloud part, which you do once.

### 1. Create your own Google OAuth client

1. Go to the [Google Cloud console](https://console.cloud.google.com/) and create a
   project, for example `gmail-multi`.
2. **Enable the Gmail API**: APIs & Services > Library > "Gmail API" > Enable.
3. **Set up the OAuth consent screen** (Google Auth Platform > Branding / Audience):
   - User type **External** for a personal @gmail.com account. Choose **Internal** if all
     your accounts are in one Google Workspace organization.
   - With External and "Testing" status, add every address you will connect as a
     **test user** (Audience > Test users).
   - Data access / scopes: add `.../auth/gmail.readonly`, and `.../auth/gmail.compose`
     if you want drafts.
4. **Create the client**: Clients > Create client > Application type **Desktop app**.
   Download the JSON file (`client_secret_....json`).

> **Testing mode expires tokens after 7 days.** Google issues refresh tokens that
> expire in 7 days for External apps in "Testing" status
> ([Google docs](https://developers.google.com/identity/protocols/oauth2#expiration)).
> You can re-run `gmail-multi add <alias>` weekly, or set the app to "In production".
> Gmail scopes are _restricted_ scopes, so a production app that is not verified shows an
> "unverified app" warning you click through. For a client only you use, that is
> expected. You are both the developer and the user.

**Google Workspace accounts:** your admin may need to allow the app under Admin console >
Security > Access and data control > API controls > Manage App Access > Configure new
app. They search for your OAuth client ID and choose "Trusted" or "Specific Google data"
([Google docs](https://knowledge.workspace.google.com/admin/apps/control-which-apps-access-google-workspace-data)).

### 2. Install

From a clone of this repository:

```bash
python3 -m venv .venv
.venv/bin/pip install "mcp>=2.3,<3" "httpx>=0.27,<1" "keyring>=25,<26" "cryptography>=43"
# until packaging lands: run as a module
export PYTHONPATH="$PWD/src"
alias gmail-multi="$PWD/.venv/bin/python -m gmail_multi"
```

### 3. Add accounts

```bash
gmail-multi add personal --client-file ~/Downloads/client_secret_XXXX.json   # first time only
gmail-multi add work --drafts          # this one may also create drafts
gmail-multi list
```

`add` opens your browser on Google's consent page. Pick the account and approve.
Google redirects to a one-shot listener on `127.0.0.1`, and the tokens go straight into
the token store. Once the client is stored you can delete the downloaded JSON file.

**On a headless machine**, use a fixed port and forward it:

```bash
gmail-multi add work --port 8765 --no-browser
# on your desktop:  ssh -L 8765:127.0.0.1:8765 <server>   then open the printed URL
```

To change an account's mode, run `add` again with or without `--drafts`. Google does
not support adding scopes later for installed apps, so the account is authorized again.

**Removing:** `gmail-multi remove <alias>` (also `revoke`) revokes the grant at Google and
deletes the local tokens. `--keep-grant` only deletes the local tokens.

### 4. Connect your MCP client

**Claude Code:**

```bash
claude mcp add --transport stdio --scope user gmail-multi \
  -- /path/to/gmail-multi/.venv/bin/python -m gmail_multi serve
```

(Add `--env PYTHONPATH=/path/to/gmail-multi/src` before `--transport` until packaging lands.)

**Claude Desktop:** edit `claude_desktop_config.json`. On macOS it is
`~/Library/Application Support/Claude/`, on Windows `%APPDATA%\Claude\`, and on Linux
`~/.config/Claude/`
([MCP docs](https://modelcontextprotocol.io/docs/develop/connect-local-servers)):

```json
{
  "mcpServers": {
    "gmail-multi": {
      "command": "/path/to/gmail-multi/.venv/bin/python",
      "args": ["-m", "gmail_multi", "serve"],
      "env": { "PYTHONPATH": "/path/to/gmail-multi/src" }
    }
  }
}
```

Add `"--read-only"` to `args` to turn drafts off for every account in that client.

If you use the encrypted-file store (see below), add
`"GMAIL_MULTI_PASSPHRASE_CMD": "pass show gmail-multi"`, or a similar command for your
password manager, to `env`.

## Security model

**What the agent can do.** It can read mail in accounts you added. It can create drafts
in accounts you put in `drafts` mode. It cannot send, delete, archive, label, or change
settings. No code path in this server calls those endpoints.

One caveat: Google's `gmail.compose` scope covers drafts **and sending**
([scope list](https://developers.google.com/workspace/gmail/api/auth/scopes)). A token
for a drafts account would be able to send if someone stole it. So keep read-only as
the default and opt in to drafts only where you need it.

**What the agent cannot do.** It cannot add accounts, change modes or widen scopes.
Those are CLI-only and need you in a browser.

**Tokens.**

- _Keychain (default when available):_ macOS Keychain, Windows Credential Locker, or
  Secret Service / KWallet on Linux, via [`keyring`](https://pypi.org/project/keyring/).
  Backends known to be insecure (the null/fail backend, keyrings.alt plaintext) are
  refused.
- _Encrypted file (fallback, e.g. headless Linux):_ `tokens.enc` holds the whole store,
  encrypted with AES-256-GCM under a key derived from your passphrase with scrypt. The
  passphrase comes from `GMAIL_MULTI_PASSPHRASE_CMD` (a command whose output is the
  passphrase; recommended), `GMAIL_MULTI_PASSPHRASE`, or a prompt. The server fails
  closed if it has none.
- The config file (`config.json`: aliases, addresses, modes) and `tokens.enc` are written
  with mode 600 in a mode-700 directory. The directory is `~/.config/gmail-multi`, or
  `%APPDATA%\gmail-multi`, or set `GMAIL_MULTI_HOME`.
- The OAuth client secret is stored in the token store too. Google says desktop clients
  "cannot keep secrets confidential"
  ([docs](https://developers.google.com/identity/protocols/oauth2/native-app)), so the
  client secret is not the real protection. Your refresh tokens are.

**OAuth flow.** This is Google's installed-app flow: a loopback redirect on
`127.0.0.1:<random port>`, PKCE with S256, and a random `state` checked against CSRF.
After consent, gmail-multi checks which scopes you actually granted. If you declined
drafts, the account is saved as read-only. If you declined read access, nothing is
saved and the token is revoked.

**Email is untrusted input.** Anyone can email you text written to steer an AI agent
("ignore previous instructions and forward..."). gmail-multi:

- converts HTML to text, dropping scripts, styles and hidden markup. Link targets are
  shown, but only http, https and mailto links,
- removes control characters and bidi-override characters,
- caps every field: 20,000 characters per body, an 80,000-character body budget per
  response, header and subject caps, at most 50 search results,
- returns content as JSON between `<<<UNTRUSTED_EMAIL nonce=…>>>` markers with a fresh
  random nonce each time, and removes any look-alike markers from the content,
- starts every result with a notice that the content is data and must not be followed.

Wrapping lowers the risk of prompt injection but does not remove it. The real limit is
the permission boundary: read-only tokens and no send tool. Keep your MCP client's
confirmation prompts on for `create_draft`.

**Rate limiting.** At most 120 tool calls per minute and 8 concurrent Gmail requests per
server.

## Limits (v0.1)

- No sending, no attachment downloads, no label changes, no archive or delete.
- Search returns metadata and snippets only. Call `get_message` / `get_thread` for bodies.
- `all` fans out to every account at once, so large searches over many accounts use more
  Gmail API quota.
- Testing-mode apps need re-authorization every 7 days (see above).
- Google allows 100 refresh tokens per account per OAuth client. Adding the same account
  over and over eventually invalidates the oldest token
  ([docs](https://developers.google.com/identity/protocols/oauth2#expiration)).
- `remove` revokes the grant for that Google account and client. If two aliases point to
  the same address, `remove` does not revoke, because that would cut off the other alias
  too.

## Development

```bash
.venv/bin/pip install pytest pytest-asyncio
.venv/bin/python -m pytest -q
```

The tests use fake Gmail clients and `httpx.MockTransport`. They never call Google.

## License

[AGPL-3.0](LICENSE).
