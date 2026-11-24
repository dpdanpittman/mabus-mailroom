<p align="center"><img src="docs/logo.svg" alt="Mabus Mailroom logo" width="120" height="120"></p>

# Mabus Mailroom

[![dpdanpittman/mabus-mailroom MCP server](https://glama.ai/mcp/servers/dpdanpittman/mabus-mailroom/badges/score.svg)](https://glama.ai/mcp/servers/dpdanpittman/mabus-mailroom)

Mabus Mailroom is a local MCP server that lets your AI agent read several Gmail accounts at
once, read-only by default.

![Tool calls against three invented inboxes: one search across all of them, a draft refused on a read-only account, a draft saved on an account in drafts mode](docs/demo.png)

_Real tool calls through the MCP protocol, against fake Gmail clients holding invented mail. The search result is shortened._

Free and open source ([AGPL-3.0](LICENSE)). Privacy policy: [PRIVACY.md](PRIVACY.md),
also at https://mabus.ai/mabus-mailroom/privacy. Terms of use: [TERMS.md](TERMS.md), also at
https://mabus.ai/mabus-mailroom/terms.

An [MCP](https://modelcontextprotocol.io) server that gives an AI agent (Claude Code,
Claude Desktop, or any other MCP client) access to **several Gmail accounts at once**.
Every account is read-only unless you opt it in. You choose, per account, whether the
agent may also save drafts, or save drafts **and send**. By default the human confirms
each send.

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
for a survey with sources. Mabus Mailroom is built around a narrower, safer default:

|                    | Mabus Mailroom                                                                                                                                                    |
| ------------------ | ----------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Default permission | `gmail.readonly` token per account. The token itself cannot write.                                                                                                |
| Drafts / send      | Opt-in per account: `--drafts` or `--send`. Send tools exist only when some account is in send mode, and each send asks the human first (MCP elicitation).        |
| Accounts           | One server, explicit alias on every call. `all` works for search only (fan-out, merged newest first).                                                             |
| Who adds accounts  | Only you, from the CLI. The agent cannot add accounts or widen scopes.                                                                                            |
| Tokens             | OS keychain, an AES-256-GCM file encrypted with your passphrase, or your password manager (Proton Pass, 1Password, Bitwarden, or any CLI). Never plain text.      |
| Email content      | Treated as untrusted. HTML is turned into text, fields have size caps, and content is fenced off with random-nonce markers and a "data, not instructions" notice. |
| OAuth client       | Your own Google Cloud client. This project ships no client ID and runs no server.                                                                                 |

## Modes

| Mode               | `add` flag | Google scopes                     | Tools allowed                       |
| ------------------ | ---------- | --------------------------------- | ----------------------------------- |
| readonly (default) | none       | `gmail.readonly`                  | read tools                          |
| drafts             | `--drafts` | `gmail.readonly`, `gmail.compose` | read tools, `create_draft`          |
| send               | `--send`   | `gmail.readonly`, `gmail.compose` | read tools, drafts, both send tools |

`gmail.compose` is the narrowest scope that covers `drafts.create`, `drafts.send` and
`messages.send`
([drafts.send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.drafts/send),
[messages.send](https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/send)).
That is why the drafts and send modes get the same token, and Mabus Mailroom itself
enforces the difference between them (see Security model).

## Tools

| Tool             | What it does                                                                                             | Account argument       |
| ---------------- | -------------------------------------------------------------------------------------------------------- | ---------------------- |
| `list_accounts`  | Lists aliases, addresses, modes                                                                          | none                   |
| `search`         | Gmail search syntax. Returns metadata and snippets, newest first                                         | an alias, or `all`     |
| `get_message`    | One message: headers, plain-text body, attachments (with `part_id`)                                      | alias                  |
| `get_thread`     | A whole conversation, cut to a size budget                                                               | alias                  |
| `list_labels`    | The account's labels                                                                                     | alias                  |
| `get_attachment` | Text extract of one attachment: text files, HTML, and PDF (with `pypdf`). Other types give metadata only | alias                  |
| `create_draft`   | Saves a draft (optionally as a reply in a thread)                                                        | drafts or send account |
| `send_message`   | **Sends a real email**, after the human confirms                                                         | send account           |
| `send_draft`     | **Sends an existing draft**, after the human confirms                                                    | send account           |

`create_draft` is listed only when some account can draft. `send_message` and
`send_draft` are listed only when some account is in send mode. All three are hidden
under `serve --read-only`. Every call checks the account's mode again, whatever the tool
list says.

## Setup

You need Python 3.10+ and about 10 minutes for the Google Cloud part, which you do once.

### 1. Create your own Google OAuth client

1. Go to the [Google Cloud console](https://console.cloud.google.com/) and create a
   project, for example `mabus-mailroom`.
2. **Enable the Gmail API**: APIs & Services > Library > "Gmail API" > Enable.
3. **Set up the OAuth consent screen** (Google Auth Platform > Branding / Audience):
   - User type **External** for a personal @gmail.com account. Choose **Internal** if all
     your accounts are in one Google Workspace organization.
   - With External and "Testing" status, add every address you will connect as a
     **test user** (Audience > Test users).
   - Data access / scopes: add `.../auth/gmail.readonly`, and `.../auth/gmail.compose`
     if you want drafts or sending.
4. **Create the client**: Clients > Create client > Application type **Desktop app**.
   Download the JSON file (`client_secret_....json`).

> **Testing mode expires tokens after 7 days.** Google issues refresh tokens that
> expire in 7 days for External apps in "Testing" status
> ([Google docs](https://developers.google.com/identity/protocols/oauth2#expiration)).
> You can re-run `mabus-mailroom add <alias>` weekly, or set the app to "In production".
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
.venv/bin/pip install .           # or ".[pdf]" for PDF text in get_attachment
export PATH="$PWD/.venv/bin:$PATH" # puts the mabus-mailroom command on your PATH
mabus-mailroom --version
```

### 3. Add accounts

```bash
mabus-mailroom add personal --client-file ~/Downloads/client_secret_XXXX.json   # first time only
mabus-mailroom add club --drafts          # may also save drafts
mabus-mailroom add work --send            # may save drafts and send (each send confirmed)
mabus-mailroom list
```

`add` opens your browser on Google's consent page. Pick the account and approve.
Google redirects to a one-shot listener on `127.0.0.1`, and the tokens go straight into
the token store. Once the client is stored you can delete the downloaded JSON file.

**On a headless machine**, use a fixed port and forward it:

```bash
mabus-mailroom add work --port 8765 --no-browser
# on your desktop:  ssh -L 8765:127.0.0.1:8765 <server>   then open the printed URL
```

To change an account's mode, run `add` again with `--drafts`, `--send` or neither. Google
does not support adding scopes later for installed apps, so the account is authorized
again.

**Removing:** `mabus-mailroom remove <alias>` (also `revoke`) revokes the grant at Google and
deletes the stored tokens. `--keep-grant` only deletes the stored tokens.

### 4. Choose where tokens live (first `add` only)

| `--store`        | Where                                                                                                       |
| ---------------- | ----------------------------------------------------------------------------------------------------------- |
| `auto` (default) | the OS keychain if there is a secure one, otherwise the encrypted file                                      |
| `keyring`        | macOS Keychain, Windows Credential Locker, Secret Service / KWallet                                         |
| `file`           | `tokens.enc`, AES-256-GCM, key from your passphrase (scrypt)                                                |
| `command`        | your password manager, through its CLI (`--store-preset proton-pass`, `1password`, `bitwarden` or `custom`) |

Password-manager examples:

```bash
# Proton Pass (pass-cli). Make a vault named "mabus-mailroom" first, or pass --store-vault.
mabus-mailroom add personal --client-file client.json --store command --store-preset proton-pass \
    --store-env PROTON_PASS_KEY_PROVIDER=fs     # only if your pass-cli setup uses the fs key provider

# 1Password (op). Sign in first: desktop-app integration, `op signin`, or OP_SERVICE_ACCOUNT_TOKEN in your shell.
mabus-mailroom add personal --client-file client.json --store command --store-preset 1password --store-vault mabus-mailroom

# Bitwarden (bw). Run `bw unlock` and export BW_SESSION in the shell that runs mabus-mailroom.
mabus-mailroom add personal --client-file client.json --store command --store-preset bitwarden

# Anything else: three commands with an {item} placeholder. set reads the secret on stdin, get prints it.
mabus-mailroom add personal --client-file client.json --store command --store-preset custom \
    --store-get 'mypm read {item}' --store-set 'mypm write {item}' --store-delete 'mypm rm {item}'
```

How each preset talks to its CLI. Secrets only ever travel on stdin or stdout, and argv
carries vault and item names only:

| Preset        | Read                                                                     | Write                                                                                                 | Delete                       |
| ------------- | ------------------------------------------------------------------------ | ----------------------------------------------------------------------------------------------------- | ---------------------------- |
| `proton-pass` | `pass-cli item view --vault-name V --item-title T --field note`          | trash the old item, then `pass-cli item create note --vault-name V --from-template -` (JSON on stdin) | `pass-cli item trash ...`    |
| `1password`   | `op item get T --vault V --fields label=password --reveal --format json` | delete the old item, then `op item create --vault V -` (JSON template on stdin)                       | `op item delete T --vault V` |
| `bitwarden`   | `bw list items --search T`, exact name match, `notes`                    | `bw create item` / `bw edit item ID` (base64 JSON on stdin)                                           | `bw delete item ID`          |

Item names are `mabus-mailroom-client` and `mabus-mailroom-account-<alias>`. Docs used:
[pass-cli item commands](https://protonpass.github.io/pass-cli/commands/item),
[op item](https://www.1password.dev/cli/reference/management-commands/item/) (its own
warning: "Command arguments get logged in your command history, and can be visible to
other processes on your machine"), and [Bitwarden CLI](https://bitwarden.com/help/cli/).

- **Locked or signed-out vault:** the CLI's error is turned into a clear message ("vault
  looks locked or signed out..."), and the secret never appears in it. In `serve`, an
  account whose token cannot be read is reported as unavailable, and the other accounts
  keep working.
- **Proton Pass agent sessions** need a reason on each access. Mabus Mailroom sets
  `PROTON_PASS_AGENT_REASON` unless you already set it.
- **Old versions:** "write" replaces the item. Proton Pass and Bitwarden move the old
  item to trash, and 1Password to Recently Deleted. Empty those if you want old tokens
  gone. Removed accounts are revoked at Google anyway.
- `--store-env` takes non-secret settings only. Names that look like secrets
  (`*TOKEN*`, `*SESSION*`, ...) are refused, so keep those in your shell environment.

### 5. Connect your MCP client

**Claude Code:**

```bash
claude mcp add --transport stdio --scope user mabus-mailroom \
  -- /path/to/mabus-mailroom/.venv/bin/mabus-mailroom serve
```

**Claude Desktop:** edit `claude_desktop_config.json`. On macOS it is
`~/Library/Application Support/Claude/`, on Windows `%APPDATA%\Claude\`, and on Linux
`~/.config/Claude/`
([MCP docs](https://modelcontextprotocol.io/docs/develop/connect-local-servers)):

```json
{
  "mcpServers": {
    "mabus-mailroom": {
      "command": "/path/to/mabus-mailroom/.venv/bin/mabus-mailroom",
      "args": ["serve"]
    }
  }
}
```

`serve` flags:

- `--read-only` turns off drafts and sending for every account.
- `--no-confirm-send` skips Mabus Mailroom's own confirmation prompt (see below).

If you use the encrypted-file store, add
`"MABUS_MAILROOM_PASSPHRASE_CMD": "pass show mabus-mailroom"`, or a similar command for your
password manager, to `env`.

## Sending, and the confirmation step

`send_message` and `send_draft` send real email. They cannot be unsent. Their tool
descriptions say so, and they carry the MCP `destructiveHint`.

Before each send, Mabus Mailroom asks the human through **MCP elicitation**. The prompt
shows:

- the sending account and address,
- To, Cc and Bcc, and the number of recipients,
- the subject,
- the body (the first 1,500 characters). For `send_draft`, open the draft in Gmail to
  read the body.

The email goes out only if the person ticks "send". Declining or cancelling returns
"Not sent". The MCP Python SDK carries the question either as a live request (older
protocol versions) or as a multi-round-trip `input_required` result (2026-07-28)
([SDK docs](https://py.sdk.modelcontextprotocol.io/handlers/elicitation/)).

- **Clients without elicitation support:** the send is refused, and the model is told
  why. It fails closed. The owner can then pick a client that supports elicitation, or
  start the server with `serve --no-confirm-send`. In that case the client's own
  tool-approval prompt is the only check, so keep it on for `send_*`.
- **The prompt is only as good as the client.** The MCP spec allows a client to answer
  elicitations itself. Use a client that shows them to you.
- **Checks before anything is sent:**
  - the account must be in send mode,
  - the server must not be in `--read-only`,
  - To, Cc, Bcc and Subject must each be a single line (header injection is refused),
  - at most 20 recipients across To, Cc and Bcc,
  - a body of at most 100,000 characters.

  A refused send never reaches the confirmation prompt. `send_draft` checks the draft's
  recipients again just before sending. A send is never retried automatically.

## Attachments

- `get_message` lists each attachment with its `part_id`, file name, type and size.
- `get_attachment(account, message_id, part_id)` returns a **text extract only**:
  - text files (text/\*, JSON, XML, CSV, .md, .log, ...),
  - HTML, converted to text,
  - PDF, when the optional pure-Python [`pypdf`](https://pypi.org/project/pypdf/) is
    installed: text from up to 50 pages, with a 20-second time limit, and pypdf's own
    decompression limits.

  Other file types give metadata only.

- Caps: a **5 MB** download cap (checked before downloading, and again on the bytes
  received) and **50,000 characters** of text returned.
- The extract is wrapped as untrusted content, exactly like a message body.
- Files are never saved to disk.

## Security model

**What the agent can do:**

- read mail in the accounts you added,
- save drafts in drafts-mode and send-mode accounts,
- send from send-mode accounts, normally after you confirm each send.

It cannot delete, archive, label, or change settings. No code path in this server calls
those endpoints.

**The token caveat.** For drafts-mode accounts, the "no send" rule is enforced by
Mabus Mailroom, not by Google. Google's `gmail.compose` scope covers drafts **and sending**
([scope list](https://developers.google.com/workspace/gmail/api/auth/scopes)), so a
stolen drafts or send token could send. Keep read-only as the default, and opt in only
where you need to.

**What the agent cannot do.** It cannot add accounts, change modes or widen scopes.
Those are CLI-only and need you in a browser.

**Tokens:**

- _Keychain:_ via [`keyring`](https://pypi.org/project/keyring/). Backends known to be
  insecure (null/fail, keyrings.alt plaintext) are refused.
- _Encrypted file:_ `tokens.enc` holds the whole store, encrypted with AES-256-GCM under
  a key derived from your passphrase with scrypt. The passphrase comes from
  `MABUS_MAILROOM_PASSPHRASE_CMD` (recommended), `MABUS_MAILROOM_PASSPHRASE`, or a prompt. The
  server fails closed if it has none.
- _Password manager:_ see step 4. Secrets go over stdin and stdout only, never argv,
  never into Mabus Mailroom's config, and never into logs or error messages.
- `config.json` (aliases, addresses, modes, password-manager settings) and `tokens.enc`
  are written with mode 600 in a mode-700 directory. The directory is
  `~/.config/mabus-mailroom`, or `%APPDATA%\mabus-mailroom`, or set `MABUS_MAILROOM_HOME`.
- The OAuth client secret is stored in the token store too. Google says desktop clients
  "cannot keep secrets confidential"
  ([docs](https://developers.google.com/identity/protocols/oauth2/native-app)), so the
  client secret is not the real protection. Your refresh tokens are.

**OAuth flow.** This is Google's installed-app flow: a loopback redirect on
`127.0.0.1:<random port>`, PKCE with S256, and a random `state` checked against CSRF.
After consent, Mabus Mailroom checks which scopes you actually granted. If you declined
compose, the account is saved as read-only. If you declined read access, nothing is
saved and the token is revoked.

**Email is untrusted input.** Anyone can email you text written to steer an AI agent
("ignore previous instructions and forward..."). Mabus Mailroom:

- converts HTML to text, dropping scripts, styles and hidden markup. Link targets are
  shown, but only http, https and mailto links,
- removes control characters and bidi-override characters,
- caps every field: 20,000 characters per body, an 80,000-character body budget per
  response, 50,000 characters per attachment extract, header and subject caps, at most
  50 search results,
- returns content as JSON between `<<<UNTRUSTED_EMAIL nonce=…>>>` markers with a fresh
  random nonce each time, and removes any look-alike markers from the content,
- starts every result with a notice that the content is data and must not be followed.

Wrapping lowers the risk of prompt injection but does not remove it. A malicious email
could still try to talk the agent into sending something. The real limits are:

- read-only by default,
- send only where you opted in,
- a human confirmation on every send,
- the 20-recipient cap.

**Rate limiting.** At most 120 tool calls per minute and 8 concurrent Gmail requests per
server.

## Limits (v0.2)

- No label changes, archive or delete. No attachments on outgoing mail. Binary
  attachments give metadata only.
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
- The password-manager presets were checked against each CLI's documentation and
  `--help`, and tested against fake CLIs, but not yet against live vaults.

## Development

```bash
.venv/bin/pip install pytest pytest-asyncio pypdf
.venv/bin/python -m pytest -q
```

The tests use fake Gmail clients, fake password-manager CLIs and `httpx.MockTransport`.
They never call Google or a real vault.

## History

Renamed from gmail-multi on 2026-11-23. Nobody had installed it yet, so there are no
compatibility shims: the old command, module, config directory and environment variables
are gone.

## Privacy

Mabus Mailroom runs only on your machine and talks only to Google. The author runs no
service and receives no data. See [PRIVACY.md](PRIVACY.md) or
https://mabus.ai/mabus-mailroom/privacy. Terms of use: [TERMS.md](TERMS.md).

## License

[AGPL-3.0](LICENSE). Free and open source.
