# Apple Mail MCP Server

A lightweight, **read-only** MCP server for Apple Mail on macOS.
Reads directly from Mail's local SQLite database and `.emlx` files — no
AppleScript needed for reading. Designed for large mailboxes (tested with
290K+ messages across multiple accounts).

## Features

- **Fast search** — SQL-level filtering pushed down to SQLite, not Python-side iteration
- **Pagination** — `offset` / `limit` support for large result sets
- **Deterministic file lookup** — `.emlx` files located via a cached directory map, no `rglob` per message
- **Read-only** — no send capability, no AppleScript, minimal attack surface
- **Minimal dependencies** — just the `mcp` SDK and Python stdlib
- **Multi-account support** — works with iCloud (IMAP), Exchange (EWS), Gmail, and other accounts configured in Mail.app

## Tools

| Tool | Description |
|------|-------------|
| `list_accounts` | List configured mail accounts |
| `list_mailboxes` | List all folders with message counts and unread counts |
| `search_emails` | Search / filter by sender, subject, date range, mailbox, read status. Paginated. |
| `read_email` | Fetch full content of a single email by ID (including To, CC, body) |

## Installation

### As a Claude Desktop Extension (`.mcpb`)

Build the bundle from a checkout:

```bash
git clone https://github.com/kaipahl/apple-mail-mcp
cd apple-mail-mcp
npx @anthropic-ai/mcpb pack
```

Then double-click the generated `apple-mail-mcp.mcpb` (or drag it into
**Claude Desktop → Settings → Extensions**). The extension uses the `uv`
server type: Claude Desktop installs the dependencies from `pyproject.toml`
itself, no bundled virtualenv needed. Files excluded from the bundle are
listed in `.mcpbignore`.

### Via `claude_desktop_config.json` with `uvx`

Add to `~/Library/Application Support/Claude/claude_desktop_config.json`:

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/opt/homebrew/bin/uvx",
      "args": [
        "--from",
        "git+https://github.com/kaipahl/apple-mail-mcp",
        "apple-mail-mcp"
      ]
    }
  }
}
```

Use the absolute path to `uvx` (`which uvx`) — Claude Desktop does not
inherit your shell's `PATH`. Restart Claude Desktop after saving.

### From a local checkout

```bash
git clone https://github.com/kaipahl/apple-mail-mcp
cd apple-mail-mcp
uv run apple-mail-mcp
```

To point Claude Desktop at the checkout instead of the Git URL:

```json
{
  "mcpServers": {
    "apple-mail": {
      "command": "/opt/homebrew/bin/uv",
      "args": ["--directory", "/path/to/apple-mail-mcp", "run", "apple-mail-mcp"]
    }
  }
}
```

## Requirements

- macOS 10.15+ (Catalina or later)
- Python 3.10+ (handled by `uv` when installed as an extension)
- Apple Mail configured with at least one account
- **Full Disk Access** (see below)

### macOS permissions

Apple Mail's database lives in `~/Library/Mail/`, which macOS protects.
The server needs **Full Disk Access**. Claude Desktop launches MCP servers
through a helper that disclaims responsibility for the child process, so
macOS attributes the access to the **first launched binary** — not to
Claude.app and not to the Python interpreter:

| Installed via | Grant Full Disk Access to |
|---|---|
| `.mcpb` extension | `uv` (`which uv`, e.g. `~/.local/bin/uv`) |
| config with `uvx` | `uvx` (`which uvx`) |
| config with `uv run` | `uv` |

1. Open **System Settings → Privacy & Security → Full Disk Access**
2. Click **+**, press ⌘⇧G and paste the binary's path
3. Restart Claude Desktop (or disable/re-enable the extension)

Without this, the server will fail with `unable to open database file`.

> **Security note:** The grant applies to *everything* run through that
> binary, not just this server.

## How it works

Apple Mail stores email metadata in a SQLite database at
`~/Library/Mail/V10/MailData/Envelope Index` and message bodies in `.emlx`
files inside `.mbox` directories.

This server:

1. Opens the SQLite DB **read-only** for fast, indexed queries.
2. Caches the list of `.mbox/Messages/` directories at startup (typically
   10–50 dirs regardless of email count).
3. Resolves `.emlx` files by message ID with simple `stat()` calls —
   no filesystem search.

## search_emails parameters

All filters are AND-combined.

| Parameter | Type | Description |
|-----------|------|-------------|
| `query` | `str` | Free-text across sender + subject |
| `sender` | `str` | Substring match on address or display name |
| `subject` | `str` | Substring match on subject line |
| `mailbox_id` | `int` | Restrict to a mailbox (from `list_mailboxes`) |
| `unread_only` | `bool` | Only unread messages |
| `date_from` | `str` | ISO date, inclusive start (`2025-01-01`) |
| `date_to` | `str` | ISO date, inclusive end (`2025-12-31`) |
| `limit` | `int` | Max results, default 20, max 200 |
| `offset` | `int` | Pagination offset |

## License

MIT
