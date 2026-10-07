# AGENTS.md

Guidance for AI agents working in this repository.
For build commands and architecture, see `CLAUDE.md`; for the MCP tool surface,
see `apple_mail_mcp/server.py`.

## Known data quirks — `apple_mail` MCP server

Keep them in mind for **every** Mail query in this repo:

### 1. Several accounts each have an `INBOX`

This Mac has five mail accounts (plus "On My Mac"), each with its own
mailbox named `INBOX`. "My inbox" means **kai@kaipahl.de** unless the user
says otherwise. Resolve it via `list_accounts` → `inbox_id`; never pick an
`INBOX` by name alone and never sum them up (that once reported 1,085 mails
instead of 73 — the bulk belonged to a client's account).

### 2. `search_emails` spans ALL mailboxes unless `mailbox_id` is passed

Without `mailbox_id`, deleted and archived messages are included in results.
When the user means their inbox (the common case — "the newsletters in my
inbox"), always resolve the mailbox first:

```
list_accounts  →  inbox_id of the account  →  pass it as mailbox_id to search_emails
```

Symptom if ignored: inflated result sets. In the observed case, 18 hits
returned where only 6 lived in the INBOX — the other 12 had long been deleted
(they still matched the sender search from the "Deleted Messages" mailbox).
Never widen a search beyond the INBOX unless the user explicitly asks for it.

## Newsletter digest workflow

Summaries of newsletters as HTML digest pages are produced by the
`summarize-newsletters` skill (`.agents/skills/summarize-newsletters/`).
Output pages are built from `.templates/newsletters-summary/` (template +
`build.mjs` + schema — see its `README.md`). The quirks above are part of
that workflow's procedure.
