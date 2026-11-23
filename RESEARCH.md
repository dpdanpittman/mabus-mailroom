# Multi-account Gmail for AI agents: prior art survey

Research date: 2026-11-23. Star counts and commit dates were read from the GitHub REST API on that
date (`/repos/{owner}/{repo}` and `/repos/{owner}/{repo}/commits?per_page=1`, default branch). Source
claims (scopes, token storage) come from reading each repository's code from a shallow clone of its
default branch; file links below point at those files. "Not determined" means I could not verify it.

## 1. Claude's built-in Gmail / Google Workspace connector

**Claim tested:** "Claude's built-in Google/Gmail connector holds one Google account at a time; a second
sign-in replaces the first."

### What Anthropic's own docs say

- Help center, _Use Google Workspace connectors_
  (https://support.claude.com/en/articles/10166901-use-google-workspace-connectors, page says "Updated
  this week"):
  - "Claude can only access the Gmail, Calendar, and Drive data for the Google account you've connected."
  - If the Google account in the file pane differs from the connected one, "Claude tells you and offers
    to switch."
  - Switching is done by disconnecting under Customize > Connectors; "The next time you use a Google
    Workspace feature in your chat, you'll be redirected to Google to re-authenticate."
  - Lists Gmail search/read, drafting, send/reply/forward (with per-action approval outside Auto mode),
    labels, threads, attachments up to about 12 MB.
- Docs page _Gmail_ (https://claude.com/docs/connectors/google/gmail.md): "You connect Gmail once from
  your connector settings"; "Claude accesses only data from the Google account you connected". Note: this
  page also says "The connector is read-only ... Claude can't create, send, or modify emails", which
  **contradicts** the help-center article above. The two Anthropic pages disagree on write capability.

**Neither Anthropic page says in words that a second sign-in replaces the first, or that multiple
accounts are unsupported.** They consistently use the singular ("the Google account you've connected")
and describe switching as disconnect-then-reconnect. So the claim is consistent with Anthropic's docs,
but the "replaces the first" wording is a third-party statement, not Anthropic's.

### What third parties say (all are vendors selling an alternative)

| Source (date shown on page)                                                                                         | Statement                                                                                                                                                                                 |
| ------------------------------------------------------------------------------------------------------------------- | ----------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Drag, https://www.dragapp.com/blog/connect-multiple-gmail-accounts-to-claude/ (updated 2026-10-14)                  | "Claude's built-in Google Workspace connector holds one Google account at a time"; workaround is "disconnect and reconnect". Cites no Anthropic source.                                   |
| Carly, https://www.usecarly.com/blog/can-claude-connect-to-multiple-gmail-accounts/ (2026-08-25)                    | "Claude's help center describes a single connected Google account"; "signing in with a second account replaces the first".                                                                |
| alfred\_, https://get-alfred.ai/blog/claude-multiple-gmail-accounts (updated 2026-11-09)                            | "Claude's Gmail connector works with one Google account at a time; connecting a second replaces the first."                                                                               |
| Composio, https://composio.dev/content/how-to-connect-multiple-gmail-accounts-to-claude-(in-2-minutes) (2026-11-20) | Claude "only supports one Gmail account natively"; also says Anthropic **appears to be beta-testing multi-account support for some users, not officially confirmed** as of November 2026. |

**Verdict:** Confirmed as practical behaviour per Anthropic's docs (one connected account, switch by
disconnecting). The exact phrase "a second sign-in replaces the first" appears only in vendor blogs.
One vendor (Composio) reports an unconfirmed multi-account beta, so re-check before publishing claims.

### Google's own hosted Gmail MCP server (relevant to the same question)

Google runs remote Workspace MCP servers, including Gmail at `https://gmailmcp.googleapis.com/mcp/v1`
(https://developers.google.com/workspace/guides/configure-mcp-servers). Status: Developer Preview,
requires membership in the Google Workspace Developer Preview Program. Gmail scopes listed:
`gmail.readonly`, `gmail.compose`, `gmail.modify`. Claude Pro/Max/Team/Enterprise can add it as a custom
connector with your own OAuth client. The page does not say one connection can span several accounts;
"personal mode" "works only for your Google Account". As a custom connector it is still one OAuth
identity per connector entry (not determined whether Claude allows the same URL to be added twice).

## 2. Open-source candidates

### Comparison table

| Project                                                                                                        | License    | Stars | Last commit | Lang       | Multi-account                                                    | Default Gmail scopes (code)                                                              | Token storage                                                 | Maintained                                |
| -------------------------------------------------------------------------------------------------------------- | ---------- | ----- | ----------- | ---------- | ---------------------------------------------------------------- | ---------------------------------------------------------------------------------------- | ------------------------------------------------------------- | ----------------------------------------- |
| [taylorwilsdon/google_workspace_mcp](https://github.com/taylorwilsdon/google_workspace_mcp)                    | MIT        | 3,572 | 2026-11-19  | Python     | Yes, one server, `user_google_email` arg per tool (stdio)        | readonly+send+compose+modify+labels+settings.basic; `--read-only` gives `gmail.readonly` | Plain JSON per email, 0600                                    | Very active                               |
| [aaronsb/google-workspace-mcp](https://github.com/aaronsb/google-workspace-mcp)                                | Apache-2.0 | 200   | 2026-11-10  | TypeScript | Yes, one server, `email` arg per call                            | `gmail.modify`+`settings.basic`; per-account `access: read` gives `gmail.readonly`       | Plain JSON per account, 0600                                  | Active                                    |
| [bakissation/mcp-google-multi](https://github.com/bakissation/mcp-google-multi)                                | MIT        | 22    | 2026-11-23  | TypeScript | Yes, one server, **aliases** + `"*"` / CSV **fan-out**           | `gmail.modify`+`gmail.send` (+ full Drive, Calendar, etc.) in base profile               | **AES-256-GCM** files; key in **OS keychain**, else 0600 file | Very active, young (created 2026-04-04)   |
| [MarkusPfundstein/mcp-gsuite](https://github.com/MarkusPfundstein/mcp-gsuite)                                  | MIT        | 492   | 2025-04-14  | Python     | Yes, one server, `__user_id__` arg, accounts in `.accounts.json` | **`https://mail.google.com/`** + calendar                                                | Plain JSON `.oauth2.<email>.json`                             | Dormant                                   |
| [j3k0/mcp-google-workspace](https://github.com/j3k0/mcp-google-workspace)                                      | MIT        | 33    | 2026-11-21  | TypeScript | Yes, same design as mcp-gsuite (`.accounts.json`)                | `gmail.modify`+`settings.basic`+`calendar.events`                                        | Plain JSON `.oauth2.<email>.json`, 0600                       | Active, small                             |
| [GongRzhe/Gmail-MCP-Server](https://github.com/GongRzhe/Gmail-MCP-Server)                                      | MIT        | 1,173 | 2025-08-06  | JS/TS      | No (one credentials file; separate instances via env path)       | `gmail.modify`+`settings.basic`                                                          | Plain JSON `~/.gmail-mcp/credentials.json`                    | **Archived**                              |
| [shinzo-labs/gmail-mcp](https://github.com/shinzo-labs/gmail-mcp)                                              | MIT        | 65    | 2025-11-25  | JS/TS      | No (one config dir; separate instances)                          | modify+compose+send+settings.basic+settings.sharing                                      | Plain JSON `credentials.json`                                 | Quiet for a year; telemetry on by default |
| [dguido/google-workspace-mcp](https://github.com/dguido/google-workspace-mcp)                                  | MIT        | 37    | 2026-03-11  | TypeScript | Via named profiles = separate instances                          | `gmail.modify` (+labels) per README                                                      | Plain `tokens.json`                                           | Low activity                              |
| [gemini-cli-extensions/workspace](https://github.com/gemini-cli-extensions/workspace) (Google-org, Gemini CLI) | Apache-2.0 | 654   | 2026-06-29  | TypeScript | No account parameter found                                       | `gmail.modify`, `gmail.readonly`                                                         | OS keychain, encrypted-file fallback                          | Active                                    |
| [tecnologicachile/mail-mcp](https://github.com/tecnologicachile/mail-mcp)                                      | MIT        | 123   | 2026-11-21  | Rust       | Yes (IMAP/SMTP, multi-provider)                                  | N/A: Gmail via IMAP + App Password                                                       | Credentials in env/config                                     | Active                                    |

Not MCP but relevant: [googleworkspace/cli](https://github.com/googleworkspace/cli) (`gws`, Apache-2.0,
32,455 stars, last commit 2026-10-30, Rust, "not an officially supported Google product"). It **removed
its `mcp` command** (CHANGELOG entry "dd3fc90: Remove `mcp` command",
https://github.com/googleworkspace/cli/blob/main/CHANGELOG.md). Credentials "are encrypted at rest
(AES-256-GCM) with the key stored in your OS keyring" (README). Account switching is by
`GOOGLE_WORKSPACE_CLI_ACCOUNT` env var / `--account` flag per CHANGELOG.

### Details per project

**taylorwilsdon/google_workspace_mcp** (the market leader)

- 120+ tools across 12 Workspace services; tool tiers, `--read-only`, per-service `--permissions`,
  `--disabled-tools` (README, https://github.com/taylorwilsdon/google_workspace_mcp/blob/main/README.md).
- Gmail ops: search, get message/thread (single and batch), attachments, send, forward, draft, labels,
  filters, modify labels (function list in `gmail/gmail_tools.py`).
- Scopes: `GMAIL_SCOPES` = readonly, send, compose, modify, labels, settings.basic; read-only map uses
  `gmail.readonly` (https://github.com/taylorwilsdon/google_workspace_mcp/blob/main/auth/scopes.py).
  Read-only is **server-wide**, not per account.
- Tokens: `LocalDirectoryCredentialStore` writes plain JSON (access token, refresh token, client id
  **and client secret**) to `~/.google_workspace_mcp/credentials/<email>`, mode 0600; optional GCS
  backend relies on bucket encryption
  (https://github.com/taylorwilsdon/google_workspace_mcp/blob/main/auth/credential_store.py). Only the
  separate `workspace-cli` client advertises "encrypted, disk-backed OAuth token caching" (README).
- Multi-account: tools take `user_google_email`; in stdio mode the middleware looks up the session for
  the requested email (https://github.com/taylorwilsdon/google_workspace_mcp/blob/main/auth/auth_info_middleware.py).
  Multi-account stdio is a real, used mode (issue #926, "Multi-account stdio (non --single-user)",
  https://github.com/taylorwilsdon/google_workspace_mcp/issues/926). No aliases, no cross-account search.
- Prompt injection: README warns "Prompt injection is real" and advises limiting write tools; no
  content wrapping found in code.
- Requires your own OAuth client (`GOOGLE_OAUTH_CLIENT_ID`, README).

**aaronsb/google-workspace-mcp** (closest to the "safe multi-account" idea)

- "Add as many accounts as you like (personal and work, side by side); the server routes each request to
  the right one" (https://github.com/aaronsb/google-workspace-mcp/blob/main/README.md). 12 coarse tools;
  `manage_email` covers search, read, send, reply, forward, triage, trash, drafts, labels, threads,
  attachments. `manage_accounts` lets the agent add/remove accounts and re-scope them.
- **Per-account read-only at the token level:** `access: "read"` requests `gmail.readonly` so "the token
  itself cannot send, edit or delete" (README; `SERVICE_SCOPE_MAP_READONLY` in
  https://github.com/aaronsb/google-workspace-mcp/blob/main/src/accounts/oauth.ts). But the default is
  read/write: "`access` defaults to 'readwrite'" (same file), default Gmail scopes `gmail.modify` +
  `gmail.settings.basic`.
- Tokens: plain JSON, 0600, under `~/.local/share/google-workspace-mcp/credentials/`
  (https://github.com/aaronsb/google-workspace-mcp/blob/main/src/accounts/credentials.ts).
- Prompt injection: an HTML sanitiser strips hidden CSS blocks, Unicode tag/bidi characters, and wraps
  output in an "untrusted" spotlighting block, but it is **"Default-off ... Opt-in via
  `bodyFormat: 'html'`"**
  (https://github.com/aaronsb/google-workspace-mcp/blob/main/src/server/formatting/html-sanitize.ts).
- No alias names (accounts are addressed by email) and no all-accounts fan-out found in README or source.

**bakissation/mcp-google-multi** (already implements most of the wish list)

- "drive any number of Google accounts by alias, or fan one call out across all of them"; "your own
  OAuth app, tokens encrypted at rest (AES-256-GCM), writes deny-by-default, no telemetry"
  (https://github.com/bakissation/mcp-google-multi/blob/dev/README.md). About 970 tools across 29
  services, including generated tools for every Gmail API method plus curated ones.
- Fan-out: account field accepts an alias, `"*"` for all accounts, or a CSV subset, concurrency 5
  (https://github.com/bakissation/mcp-google-multi/blob/dev/src/fanout.ts).
- Writes: server policy `GOOGLE_PROFILE` = `read-only` (default) / `safe-writes` / `full-writes`, plus
  allow/deny globs and an outbound recipient allowlist (unset by default) described as "a prompt-injection
  blast-radius control" (https://github.com/bakissation/mcp-google-multi/blob/dev/docs/configuration.md,
  https://github.com/bakissation/mcp-google-multi/blob/dev/docs/features.md).
- **But OAuth scopes are broad by default:** `BASE_SCOPES` = `gmail.modify`, `gmail.send`, full `drive`,
  `calendar`, `spreadsheets`, `documents`, `contacts`, `webmasters`, `tasks`, meet readonly
  (https://github.com/bakissation/mcp-google-multi/blob/dev/src/auth.ts). Per-account scope profiles add
  bundles on top of base; `"includesBase": false` drops base ("advanced"). So "read-only" is enforced by
  the server, not by the token, unless the user hand-builds a profile.
- Tokens: AES-256-GCM per alias (https://github.com/bakissation/mcp-google-multi/blob/dev/src/token-store.ts);
  master key from env, OS keychain via `@napi-rs/keyring`, or a 0600 file
  (https://github.com/bakissation/mcp-google-multi/blob/dev/src/master-key.ts). Its own docs note this
  "protects tokens at rest, not against same-user malware"
  (https://github.com/bakissation/mcp-google-multi/blob/dev/docs/secrets.md).
- Prompt injection: CRLF header-injection guard and the recipient allowlist; I found **no wrapping or
  marking of email bodies as untrusted content** in `src/` (searched for untrusted/injection/spotlight).
- Very large surface (hundreds of tools, HTTP mode, multi-tenant, admin APIs). npm `mcp-google-multi`
  latest 6.8.0 (https://registry.npmjs.org/mcp-google-multi). Young project, 22 stars, single maintainer
  (not determined beyond commit history).

**MarkusPfundstein/mcp-gsuite** (the original multi-account design)

- One server; every tool takes `__user_id__`; accounts must be listed in `.accounts.json`
  (https://github.com/MarkusPfundstein/mcp-gsuite/blob/main/src/mcp_gsuite/server.py,
  https://github.com/MarkusPfundstein/mcp-gsuite/blob/main/src/mcp_gsuite/toolhandler.py).
- Gmail ops: query, get, bulk get, create/delete draft, reply (send or draft), attachments. No labels, no
  new-message send.
- Scopes: **`https://mail.google.com/`** (full mailbox incl. permanent delete) + full calendar
  (https://github.com/MarkusPfundstein/mcp-gsuite/blob/main/src/mcp_gsuite/gauth.py).
- Tokens: plain JSON `.oauth2.<email>.json` in `--credentials-dir` (default `.`), via
  `oauth2client` (deprecated library). Last commit 2025-04-14.

**j3k0/mcp-google-workspace**: TypeScript take on the same `.accounts.json` design, with
`gmail_list_accounts`, query, get, bulk get, archive, drafts, reply, attachments
(https://github.com/j3k0/mcp-google-workspace/blob/main/src/tools/gmail.ts). Scopes `gmail.modify`,
`gmail.settings.basic`, `calendar.events`; tokens plain JSON 0600
(https://github.com/j3k0/mcp-google-workspace/blob/main/src/services/gauth.ts).

**GongRzhe/Gmail-MCP-Server**: archived (GitHub API `archived: true`). Single credentials file at
`~/.gmail-mcp/credentials.json` written with default permissions
(https://github.com/GongRzhe/Gmail-MCP-Server/blob/main/src/index.ts). Tools: send, draft, read, search,
modify, delete, labels, filters, attachments. It calls `messages.delete` while requesting only
`gmail.modify` + `gmail.settings.basic`; Google requires `https://mail.google.com/` for that method
(https://developers.google.com/workspace/gmail/api/reference/rest/v1/users.messages/delete), so
permanent delete likely fails with insufficient scope (inferred, not tested).

**shinzo-labs/gmail-mcp**: 65 tools mapping nearly the whole Gmail API (messages, threads, drafts,
labels, settings, delegates, filters, send-as, S/MIME, watch)
(https://github.com/shinzo-labs/gmail-mcp/blob/main/src/index.ts). Scopes modify, compose, send,
settings.basic, settings.sharing (https://github.com/shinzo-labs/gmail-mcp/blob/main/src/oauth2.ts).
Single `credentials.json`; `TELEMETRY_ENABLED` defaults to `"true"`
(https://github.com/shinzo-labs/gmail-mcp/blob/main/src/config.ts).

**dguido/google-workspace-mcp**: multi-account via "Named profiles isolate credentials and tokens per
Google account under `~/.config/google-workspace-mcp/profiles/<name>/`", i.e. one server process per
profile (https://github.com/dguido/google-workspace-mcp/blob/main/docs/ADVANCED.md); tokens in
`tokens.json` (README).

**gemini-cli-extensions/workspace**: linked from Google's MCP list as the open-source Workspace server
(https://github.com/google/mcp/blob/main/README.md). Keychain token storage with encrypted-file fallback
(`workspace-server/src/auth/token-storage/`); ships a default OAuth client id, overridable with
`WORKSPACE_CLIENT_ID` (`workspace-server/src/utils/config.ts`). Gmail tools: search, get, attachments,
modify, batch modify, send, drafts, labels. No account parameter in tool registrations.

**tecnologicachile/mail-mcp**: IMAP/SMTP/Graph/EWS, multi-account, Gmail via App Password; tools carry
`readOnlyHint` annotations (https://github.com/tecnologicachile/mail-mcp/blob/main/README.md). Different
trust model (App Password = full IMAP access, no OAuth scopes).

**Long tail** (GitHub search "gmail mcp multi account", 2026-11-23; stars / last push):
dmorrill/gmail-mcp-multi (9, 2026-03-08), Vinksj/claude-gmail-multi (9, 2026-09-07),
boonkgim/mcp-gmail-multi-account (4, 2026-09-02), cunicopia-dev/gmail-mcp (6, 2026-11-02; registered as
`io.github.cunicopia-dev/multi-account-gmail-mcp`), gx-55/multi-gmail-mcp (in the registry). Not
reviewed in depth; all under 10 stars.

### Directories checked

- Official MCP Registry search "gmail" returned 50+ entries in the first page (Smithery-hosted forks,
  mintmcp, pulsemcp, domdomegg, ArtyMcLabin fork of GongRzhe, several multi-account ones)
  (https://registry.modelcontextprotocol.io/v0/servers?search=gmail&limit=50).
- modelcontextprotocol/servers README no longer lists third-party servers; it points to the registry
  (https://github.com/modelcontextprotocol/servers/blob/main/README.md).
- Smithery, Glama, mcp.so, PulseMCP: not browsed individually; their Gmail entries appear in the
  registry results above (e.g. `ai.smithery/*`, `com.pulsemcp/gmail`).

## 3. Paid / hosted options

All of these hold your Google OAuth refresh tokens on their servers; your agent talks to their MCP
endpoint, and they call Gmail for you.

| Service                 | Multi-account Gmail                                                                                                                                                                                                                                            | Price (as read 2026-11-23)                                                                                                                                                                                                        |
| ----------------------- | -------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- | --------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Composio                | One Composio connector in Claude, each Gmail linked under a name ("work", "personal"); you name the account in the request (https://composio.dev/content/how-to-connect-multiple-gmail-accounts-to-claude-(in-2-minutes)). "Up to 10 linked accounts per app". | Free $0: 10,000 tool calls/mo, up to 3 members, $2 one-time credits; Standard $18/seat ("coming soon"), $0.005/call over; Enterprise custom (https://composio.dev/pricing). Says data encrypted at rest/in transit, SOC 2 Type 2. |
| Nango                   | Connect each Google account to a Gmail integration; Claude uses Nango's Management MCP; "The free Nango account supports up to 10 Gmail accounts" (https://nango.dev/blog/how-to-access-multiple-gmail-accounts-with-claude-codex-cursor, 2026-11-02).         | Free $0, 10 connections; Pay-as-you-go $50/mo, $0.29 per connection; Enterprise custom (https://nango.dev/pricing).                                                                                                               |
| Carly                   | One MCP connector, several Google/Microsoft accounts (vendor blog above).                                                                                                                                                                                      | Free $0 incl. "MCP access"; Personal $35/mo with 200 credits; Organization $35/seat/mo; Enterprise custom (https://www.usecarly.com/pricing).                                                                                     |
| alfred\_                | One connector, up to six mailboxes (Gmail, Workspace, Outlook, iCloud, Yahoo, IMAP), results tagged by account, replies after approval (https://get-alfred.ai/blog/claude-multiple-gmail-accounts).                                                            | "Plans start at $29.99 a month with a 7-day free trial" (same page).                                                                                                                                                              |
| Zapier MCP              | "You can set one default app connection per app on your server", with a per-action connection override (https://docs.zapier.com/mcp/manage/configure-your-actions.md). Simultaneous use of two Gmail accounts not explicitly documented.                       | "Each tool call uses two tasks from your existing task quota" (https://zapier.com/mcp). Free 100 tasks/mo; Professional from $19.99/mo annual (750 tasks) (https://zapier.com/pricing).                                           |
| Pipedream Connect / MCP | Multi-account handling for the same app not described on the MCP page; "User credentials are encrypted at rest" (https://pipedream.com/docs/connect/mcp). Aimed at developers embedding it for their own users.                                                | Free: development only; Startup $99/mo annual or $150 monthly, 100 external users, 10,000 credits, $0.012/credit over (https://pipedream.com/pricing).                                                                            |
| Google hosted Gmail MCP | One OAuth identity per connection (see section 1).                                                                                                                                                                                                             | Developer Preview; pricing not stated (https://developers.google.com/workspace/guides/configure-mcp-servers).                                                                                                                     |

## 4. Conclusion

**Is there already a good free multi-account Gmail MCP server?** Yes, more than one:

- **taylorwilsdon/google_workspace_mcp** is the mature, popular choice and does serve several accounts
  from one stdio server (by email argument), but read-only is server-wide, tokens (with client secret)
  are plain JSON, and there is no fan-out search.
- **aaronsb/google-workspace-mcp** has the best token-level safety model: per-account read-only consent
  that yields a `gmail.readonly` token, plus an untrusted-content wrapper (opt-in only).
- **bakissation/mcp-google-multi** already ships aliases, `"*"` fan-out, AES-256-GCM tokens with the key
  in the OS keychain, your own OAuth client, and deny-by-default writes. It is young (22 stars) and very
  broad (hundreds of tools, broad default scopes).

So a new project cannot claim to be "the first multi-account Gmail MCP". The honest pitch is a
**small, Gmail-only, least-privilege** server. Gap check against the candidate list, verified above:

| Candidate feature                                    | Already done by                                                                                                     | Gap that remains (verified)                                                                                                                                                                                                                                                                                                                                                           |
| ---------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------- | ------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------------- |
| Read-only by default, per-account opt-in to drafts   | aaronsb (per-account, but default readwrite); bakissation (default read-only, but server policy over a broad token) | **Nobody found defaults to a `gmail.readonly` token for every account, with drafts as a per-account scope upgrade (`gmail.compose`).** Real gap.                                                                                                                                                                                                                                      |
| Narrow scopes                                        | aaronsb read mode; Google hosted server                                                                             | Every self-hosted server reviewed requests `gmail.modify` or broader by default; mcp-gsuite asks for full `https://mail.google.com/`. Gap for a default-narrow server. Note `gmail.readonly` and `gmail.compose` are still **Restricted** scopes; only `gmail.labels` is non-sensitive and `gmail.send` is Sensitive (https://developers.google.com/workspace/gmail/api/auth/scopes). |
| Keychain / encrypted token storage                   | bakissation, gemini extension, gws CLI                                                                              | Not a gap in the ecosystem; a gap versus taylorwilsdon, aaronsb, mcp-gsuite, j3k0, GongRzhe, shinzo, dguido (all plain JSON). Table stakes for ours.                                                                                                                                                                                                                                  |
| One instance, explicit aliases, "all" fan-out search | bakissation only                                                                                                    | Small gap: only one young project does it, inside a 970-tool server. A Gmail-only implementation with results labelled per alias is still differentiated.                                                                                                                                                                                                                             |
| Untrusted-content wrapping against prompt injection  | aaronsb (opt-in, HTML path only)                                                                                    | **Not on by default anywhere reviewed.** Real gap: wrap every message body/snippet/subject returned, always.                                                                                                                                                                                                                                                                          |
| User's own OAuth client                              | taylorwilsdon, aaronsb, bakissation, mcp-gsuite (all require it); gemini extension ships a default client           | Not a gap; standard practice. Document the "Testing" mode 7-day refresh-token expiry (noted in dguido README).                                                                                                                                                                                                                                                                        |
| Agent cannot add/remove accounts or widen scopes     | aaronsb exposes `manage_accounts` (authenticate/remove/scopes) as an MCP tool                                       | Possible gap: keep account and scope management in a CLI the human runs, not an MCP tool. (Not checked for bakissation in depth; it has an `account_add` path per `fanout.ts` comments.)                                                                                                                                                                                              |

Recommended positioning: a Gmail-only server where each account alias holds a `gmail.readonly` token by
default, drafts are a per-account opt-in to `gmail.compose` (no `gmail.send`, no `gmail.modify`, no
delete), tokens live in the OS keychain, one process serves all aliases with an `all` fan-out search, and
every piece of mail content is returned inside an untrusted-content wrapper. Account and scope changes
happen only through a human-run CLI.
