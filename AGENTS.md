# AGENTS.md

Guidance for AI agents working in this repository.
For build commands and architecture, see `CLAUDE.md`; for the MCP tool surface,
see `apple_mail_mcp/server.py`.

## Known data quirks — `apple_mail` MCP server

Keep them in mind for **every** Mail query in this repo:

### 1. `search_emails` spans ALL mailboxes unless `mailbox_id` is passed

Without `mailbox_id`, deleted and archived messages are included in results.
When the user means their inbox (the common case — "the newsletters in my
inbox"), always resolve the mailbox first:

```
list_mailboxes  →  display_name == "INBOX"  →  pass that mailbox_id to search_emails
```

Symptom if ignored: inflated result sets. In the observed case, 18 hits
returned where only 6 lived in the INBOX — the other 12 had long been deleted
(they still matched the sender search from the "Deleted Messages" mailbox).
Never widen a search beyond the INBOX unless the user explicitly asks for it.

### 2. Returned dates may be offset +31 years into the future

Example: mails dated `2057-10-02` that were actually received `2026-10-02`.
`978307200` seconds — the Core Data epoch offset used by
`maildb.py::_core_data_to_iso` — is ≈ 31.0 years, so the symptom looks like
the offset being applied to timestamps that were already Unix-epoch values.
Root cause not finally pinned down; treat it as a data quirk of this setup:

- **Always sanity-check dates against the current date** before presenting,
  storing, or rendering them, and correct implausible years (shift back in
  whole years until plausible — in practice: −31).
- Grouping/filtering by date must happen **after** the correction, otherwise
  issues land in phantom years (the first TLDR digest showed "October 2057").

## Newsletter digest workflow

Summaries of newsletters as HTML digest pages are produced by the
`summarize-newsletters` skill (`.agents/skills/summarize-newsletters/`).
Output pages are built from `.templates/newsletters-summary/` (template +
`build.mjs` + schema — see its `README.md`). Both quirks above are part of
that workflow's procedure.
