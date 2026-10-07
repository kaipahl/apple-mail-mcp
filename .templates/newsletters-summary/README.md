# newsletters-summary template

A reusable, dependency-free template kit that turns newsletter digests into a
good-looking **static HTML page** — the same design as the TLDR inbox digest
(header with stats, day-grouped entry cards with 8-word summaries, trending
topics and a four-word mood at the end).

## Files

| File                 | Purpose                                            |
|----------------------|----------------------------------------------------|
| `template.html`      | The HTML+CSS skeleton with `{{mustache}}` placeholders |
| `build.mjs`          | Zero-dependency Node builder: JSON data → HTML     |
| `data.example.json`  | Example dataset (real TLDR entries) showing the schema |
| `example-output.html`| What the example renders to (generated, for preview) |

## Quick start

```bash
cd .templates/newsletters-summary
node build.mjs data.example.json example-output.html   # → writes file
node build.mjs my-digest.json                          # → prints to stdout
```

Open the result in any browser. No server, no JavaScript in the output —
pure static HTML with inline CSS.

## Data schema (`data.json`)

```jsonc
{
  "meta": {
    "kicker": "Newsletter Digest · Apple Mail Inbox",   // small label above the h1
    "title": "TLDR Inbox Digest",                       // page <title> + h1
    "subtitle": "Every story from …",                   // paragraph under the h1
    "dateRange": "Sep 25 – Oct 2, 2026",                // used in <title>
    "summaryWords": 8,                                  // default: 8 (drives warnings + label)
    "mergedLabel": "merged from {n} issues",            // optional, {n} = source count
    "entryNounSingular": "story",                       // optional counters wording
    "entryNounPlural": "stories",
    "sourceNote": "Generated from 18 newsletters …",    // first footer paragraph
    "footerNotes": ["Sponsored promotions were excluded …"]  // more footer paragraphs
  },
  "stats": [                                            // hero stat chips (any count)
    { "value": "18", "label": "issues read" }
  ],
  "newsletters": ["TLDR", "TLDR Dev", "TLDR AI"],       // order = badge order
                                                        // or { "name": "…", "badgeClass": "p4" }
  "days": [                                             // newest first
    {
      "date": "2026-10-02",                             // ISO; weekday label auto-generated
      "label": "Friday, Oct 2, 2026",                  // optional override
      "entries": [
        {
          "headline": "INTRODUCING CLEF …",             // card title
          "section": "ENGINEERING & RESEARCH",          // small tag on the card
          "minutes": 10,                                // optional reading time
          "summary": "Cloudflare open-sources Jev-compatible decision models for autonomous agents.",
          "text": "Clef and Clef-flash are fully Jev-API …",   // original newsletter text
          "url": "https://blog.cloudflare.com/…?utm_source=tldrai",  // tracking params stripped automatically
          "sources": ["TLDR", "TLDR Dev", "TLDR AI"]    // same-day sources (strings)
                                                        // or { "newsletter": "TLDR", "date": "2026-09-28" }
                                                        // 2+ sources → "⧉ merged from N issues" chip
        }
      ]
    }
  ],
  "trendingTitle": "Trending across the newsletters",   // optional heading
  "trending": [                                         // 1+ cards, numbered automatically
    { "title": "Agents everywhere — decision models & agentic AI", "text": "…" }
  ],
  "mood": {
    "heading": "The mood",                              // optional
    "label": "in four words",                           // small caption
    "words": "“Giddy, breathless, ambitious, anxious.”"
  }
}
```

### Auto-derived conveniences

- **Day labels** — `Friday, Oct 2, 2026` computed from `date` if `label` is absent.
- **Badge colors** — newsletters get palette classes `p1…p8` (see CSS vars) by
  order; override per newsletter with `badgeClass`. More than 8 sources cycle.
- **Merged chips** — entries with 2+ `sources` get the gold `⧉ merged from N
  issues` chip; a source dated on another day shows its date on the badge.
- **URL cleanup** — `utm_*`, `ref`, `source`, `via`, `dub_id` query params are
  stripped from the link target; the visible label is the trimmed URL.
- **Validation warnings** — the builder warns when a `summary` doesn't have
  `meta.summaryWords` words (default 8) or a URL is missing.

- **Validation warnings** — the builder warns when a `summary` doesn't have
  `meta.summaryWords` words (default 8) or a URL is missing.
- **Data quality** — verify `date` values are plausible ISO dates before
  rendering. The template renders whatever it is given, including wrong
  years.

## Using another template engine

`template.html` uses a standard Mustache subset (`{{var}}`, `{{.}}`,
`{{#section}}…{{/section}}`, `{{^section}}…{{/section}}`), so it also works
with mustache.js, Handlebars, etc. — feed it the *same* view data that
`build.mjs` normalizes (see `normalize()` in the script).

## Customizing the look

All colors live in the `:root` CSS variables at the top of `template.html`
(`--bg`, `--accent`, `--p1…--p8`, …). The layout is responsive (2-column card
grid ≥ 760 px) and needs no external assets or fonts.
