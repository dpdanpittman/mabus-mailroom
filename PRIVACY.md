# Privacy policy

gmail-multi is a program you run on your own computer. It lets an AI agent that you
run (an MCP client such as Claude Code or Claude Desktop) read, and only if you opt in,
draft or send mail in Gmail accounts that you connect.

## There is no service

- gmail-multi has no server. The author runs no service for it and receives no data
  from it.
- It does not collect, upload, store remotely, sell or share any of your data.
- It contains no analytics, telemetry, tracking or advertising.

## Who it talks to

- gmail-multi itself connects only to Google: the Gmail API
  (`gmail.googleapis.com`) and Google's OAuth endpoints (`accounts.google.com`,
  `oauth2.googleapis.com`).
- It uses your own Google Cloud OAuth client. The project ships no client ID.
- During sign-in, Google redirects your browser to a one-time listener on
  `127.0.0.1` on your own machine.
- If you choose a password manager as the token store, gmail-multi runs that password
  manager's command-line tool on your machine. That tool works under its own privacy
  terms.

## What is stored, and where

- Your OAuth tokens and your OAuth client are kept in the store you choose: your
  operating system's keychain, a file encrypted with AES-256-GCM under your passphrase,
  or your password manager. They are never written in plain text.
- A small settings file (account aliases, email addresses, modes, and password-manager
  settings) is kept on your machine in `~/.config/gmail-multi` (or `%APPDATA%\gmail-multi`,
  or `GMAIL_MULTI_HOME`), readable only by your user.
- Email content is processed on your machine and handed only to the MCP client you run.
  Attachments are read as text in memory and never saved to disk. What your MCP client
  or AI provider does with that content is covered by their own terms.

## Google API Services User Data Policy

gmail-multi's use of information received from Google APIs follows the
[Google API Services User Data Policy](https://developers.google.com/terms/api-services-user-data-policy),
including the Limited Use requirements. In plain words:

- Data from Google APIs is used only to provide the features you use: searching and
  reading your mail, and saving drafts or sending mail in the accounts where you turned
  that on.
- It is not transferred to anyone, except to the MCP client you run, at your request.
- It is not used for advertising, and it is not sold.
- No human reads it. The author never has access to it.

## How to revoke access

- Run `gmail-multi remove <alias>`. This revokes the grant at Google and deletes the
  stored tokens for that account. (If two aliases point to the same address, it deletes
  the tokens but does not revoke, so the other alias keeps working.)
- At any time, you can also remove access in your Google Account: Security >
  Third-party apps and services (https://myaccount.google.com/connections), then
  remove your OAuth client.

## Changes

Changes to this policy are made in this file, and its history is in the repository.
The same text is published at https://mabus.ai/gmail-multi/privacy.

Last updated 2026-11-23.
