---
name: summarize-newsletters
description: >
  Summarize email newsletters from Apple Mail into a static HTML digest page
  (entry cards with 8-word summaries, trending topics, four-word mood).
  The user specifies which newsletters in free text (sender, newsletter family, 
  or topic).
disable-model-invocation: true
---

# Summarize Newsletters

Turn newsletters found in the user's Apple Mail (via the `apple_mail` MCP
server) into a single good-looking static HTML digest page, built with this
repository's newsletters-summary template.

## Scope

The arguments/free text of the invocation describe **what** to summarize —
interpret them; do not assume a fixed sender such as "TLDR":

- **Sender / newsletter**: a name, address, or family ("the TLDR newsletters",
  "everything from heise", "the Axios newsletters"). If none is identifiable,
  ask before searching.
- **Mailbox**: default is the **INBOX only**. Include Trash/Archive/other
  mailboxes only when the user explicitly asks for them ("including deleted",
  "all folders").
- **Time range**: only if stated ("this week", "yesterday").
- **Output location**: default `html/` in the repository root unless the user
  specifies otherwise.

## Procedure

1. **Resolve the mailbox.** Call `list_accounts` and use the `inbox_id` of
   the account the user means (default: `kai@kaipahl.de`; ask if unclear).
   Several accounts each have a mailbox named `INBOX` — never pick one by name
   alone, and never add them up. Always pass `mailbox_id` to
   `search_emails` — without it the search silently spans *all* mailboxes and
   deleted/archived mail pollutes the result (see *Known data quirks* in
   `AGENTS.md`).
2. **Search.** `search_emails` with a `sender` substring (or `query` for
   free-text) plus the `mailbox_id`, and `date_from`/`date_to` if a range was
   given. If nothing is found, say so and ask whether to extend the search to
   other mailboxes — do not widen the scope on your own.
3. **Read and parse every issue** (`read_email`). An *entry* is a
   headline/title, the text given below it, and its link:
   - Plain-text newsletters often use numbered link references (`[1]`, `[2]` …)
     with a `Links:` map at the end — resolve entry links against that map.
   - Also handle inline URLs directly in headline or text.
   - Exclude sponsor advertisements and the newsletter's own house content
     (job ads, referral links, subscription management) when identifiable.
4. **Merge duplicates**: entries with the same URL (after stripping tracking
   parameters like `utm_*`) or the same headline (case/reading-time
   normalized) become one entry — one text (prefer the most recent, or the
   longest), one summary, one URL. Keep note of which issues carried it.
5. **Write one 8-word summary per entry** — exactly eight words, capturing the
   gist of the entry text, not just its first words.
6. **Analyze the whole set**: identify the **two trending topics** across the
   entries and a **four-word mood summary**.
7. **Sanity-check dates.** Dates must not lie in the future. If they do,
   stop and report it as a server bug — do not shift years by hand.
8. **Build the page with the repository template**, following the procedure
   and schema in `.templates/newsletters-summary/README.md`:
   - Template: `.templates/newsletters-summary/template.html` (fill via
     `build.mjs` — do not hand-edit generated HTML).
   - Write a `data.json` matching the schema (stats, newsletters, days →
     entries with headline/section/minutes/summary/text/url/sources,
     trending, mood).
   - Generate: `node .templates/newsletters-summary/build.mjs data.json <output>.html`
     (run from the repository root; resolve paths from there).
   - The builder warns about summaries that are not 8 words or missing URLs —
     fix warnings, rebuild.
9. **Verify the result** by opening the generated HTML (headless browser DOM
   check is enough): cards render, no unprocessed `{{…}}` tokens, links valid,
   no horizontal overflow.

## Conventions

- Respond in english; keep entry texts in their original language.
- Hero stats: issues read, entries found, unique entries after merging,
  duplicates merged, days covered.
- Each digest run leaves the source mails untouched (read-only).
