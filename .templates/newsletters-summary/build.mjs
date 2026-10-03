#!/usr/bin/env node
/**
 * build.mjs — newsletters-summary template builder
 *
 * Usage:
 *   node build.mjs data.json > digest.html     (write to stdout)
 *   node build.mjs data.json digest.html       (write to file)
 *
 * Reads a JSON file matching the schema in README.md, normalizes it
 * (badge colors, date labels, merged-source badges, URL cleanup …),
 * fills template.html (Mustache-style placeholders) and emits static HTML.
 * No dependencies — plain Node (>= 16).
 */

import { readFileSync, writeFileSync } from 'node:fs';
import { fileURLToPath } from 'node:url';
import { dirname, join } from 'node:path';

const HERE = dirname(fileURLToPath(import.meta.url));

/* ----------------------------- data schema defaults ----------------------------- */

const PALETTE = ['p1', 'p2', 'p3', 'p4', 'p5', 'p6', 'p7', 'p8'];
const MONTHS = ['Jan', 'Feb', 'Mar', 'Apr', 'May', 'Jun', 'Jul', 'Aug', 'Sep', 'Oct', 'Nov', 'Dec'];
const WEEKDAYS = ['Sunday', 'Monday', 'Tuesday', 'Wednesday', 'Thursday', 'Friday', 'Saturday'];

const longDate = iso => {
  const d = new Date(iso + 'T00:00:00Z');
  return `${WEEKDAYS[d.getUTCDay()]}, ${MONTHS[d.getUTCMonth()]} ${d.getUTCDate()}, ${d.getUTCFullYear()}`;
};
const shortDate = iso => `${MONTHS[+iso.slice(5, 7) - 1]} ${+iso.slice(8, 10)}`;

/** Strip tracking params (utm_*, ref, source, via, …) from a URL. */
const cleanHref = u => {
  const qi = u.indexOf('?');
  if (qi < 0) return u;
  const base = u.slice(0, qi);
  const keep = u.slice(qi + 1).split('&').filter(p => p && !/^(utm_[a-z_]+|ref|source|via|dub_id)=/i.test(p));
  return keep.length ? `${base}?${keep.join('&')}` : base;
};

/** Human-friendly, length-capped label for a URL. */
const urlLabel = u => {
  let l = u.replace(/^https?:\/\//, '').replace(/^www\./, '');
  const qi = l.indexOf('?');
  if (qi >= 0) l = l.slice(0, qi);
  return l.length > 76 ? l.slice(0, 73) + '…' : l;
};

/** Normalize the raw JSON data into template-ready view data. */
function normalize(raw) {
  const meta = raw.meta ?? {};
  const summaryWords = meta.summaryWords ?? 8;

  // newsletters: "Name" or { name, badgeClass } → lookup map in stable order
  const newsletters = (raw.newsletters ?? []).map((nl, i) =>
    typeof nl === 'string' ? { name: nl, cls: PALETTE[i % PALETTE.length] }
                           : { name: nl.name, cls: nl.badgeClass || PALETTE[i % PALETTE.length] });
  const byName = new Map(newsletters.map(n => [n.name, n]));
  const nlRank = name => {
    let i = newsletters.findIndex(n => n.name === name);
    if (i < 0) { // unknown source name → append with next free palette color
      i = newsletters.length;
      newsletters.push({ name, cls: PALETTE[i % PALETTE.length] });
      byName.set(name, newsletters[i]);
    }
    return i;
  };

  const days = (raw.days ?? []).map(day => {
    const entries = (day.entries ?? []).map(e => {
      // sources: ["Name"] (same day) or [{ newsletter, date }]
      const sources = (e.sources ?? [{ newsletter: e.newsletter, date: day.date }])
        .map(s => typeof s === 'string' ? { newsletter: s, date: day.date } : s)
        .map(s => ({ ...s, date: s.date ?? day.date }))
        .sort((a, b) => nlRank(a.newsletter) - nlRank(b.newsletter));

      // one badge per distinct newsletter; if any of its sources is dated on
      // another day, the differing date(s) are shown on the badge
      const grouped = new Map();
      for (const s of sources) {
        if (!grouped.has(s.newsletter)) grouped.set(s.newsletter, []);
        grouped.get(s.newsletter).push(s.date);
      }
      const badges = [...grouped.entries()].map(([name, dates]) => {
        const nl = byName.get(name);
        const differing = [...new Set(dates.filter(d => d !== day.date))].map(shortDate);
        return { name, cls: nl.cls, extra: differing.length ? ` · ${differing.join('/')}` : '' };
      });

      const mergedTpl = meta.mergedLabel ?? 'merged from {n} issues';
      return {
        headline: e.headline ?? '',
        section: e.section ?? 'NEWS',
        mins: e.minutes ? `${e.minutes} min` : '',
        merged: sources.length > 1 ? mergedTpl.replace('{n}', String(sources.length)) : '',
        summary: e.summary ?? '',
        summaryLabel: e.summaryLabel ?? meta.summaryLabel ?? `${summaryWords}-word summary`,
        text: e.text ?? '',
        urlHref: cleanHref(e.url ?? ''),
        urlLabel: urlLabel(e.url ?? ''),
        badges,
      };
    });

    const present = [...new Set(entries.flatMap(e => e.badges.map(b => b.name)))]
      .sort((a, b) => nlRank(a) - nlRank(b))
      .map(name => ({ name, cls: byName.get(name).cls }));
    const n = entries.length;
    const one = meta.entryNounSingular ?? 'story';
    const many = meta.entryNounPlural ?? 'stories';

    return {
      label: day.label ?? longDate(day.date),
      chips: present,
      countLabel: `${n} ${n === 1 ? one : many}`,
      entries,
    };
  });

  return {
    meta,
    summaryWords,
    stats: raw.stats ?? [],
    days,
    trendingTitle: raw.trendingTitle ?? 'Trending across the newsletters',
    trending: (raw.trending ?? []).map((t, i) => ({ num: i + 1, ...t })),
    mood: {
      heading: raw.mood?.heading ?? 'The mood',
      label: raw.mood?.label ?? `in ${meta.moodWords ?? 4} words`,
      words: raw.mood?.words ?? '',
    },
  };
}

/* ----------------------------- mini Mustache renderer ----------------------------- */

const escapeHtml = s => String(s)
  .replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;')
  .replace(/"/g, '&quot;').replace(/'/g, '&#39;');

const escapeRe = s => s.replace(/[.*+?^${}()|[\]\\]/g, '\\$&');

/** Find the matching {{/name}} for a section starting at `from` (handles nesting). */
function findSectionEnd(tpl, from, name) {
  const re = new RegExp(`\\{\\{\\s*([#^/])\\s*${escapeRe(name)}\\s*\\}\\}`, 'g');
  re.lastIndex = from;
  let depth = 1, m;
  while ((m = re.exec(tpl))) {
    if (m[1] === '/') { if (--depth === 0) return { start: m.index, end: re.lastIndex }; }
    else depth++;
  }
  return { start: tpl.length, end: tpl.length };
}

/** Resolve a (possibly dotted) name against a stack of context frames. */
function lookup(frames, name) {
  if (name === '.' || name === 'this') return frames[frames.length - 1];
  const parts = name.split('.');
  for (let f = frames.length - 1; f >= 0; f--) {
    let v = frames[f], ok = true;
    for (const p of parts) {
      if (v && typeof v === 'object' && p in v) v = v[p];
      else { ok = false; break; }
    }
    if (ok) return v;
  }
  return undefined;
}

/** Render a Mustache subset: {{var}}, {{.}}, {{#sec}}…{{/sec}}, {{^sec}}…{{/sec}}, {{!comment}}. */
function render(tpl, frames) {
  let out = '', i = 0;
  while (i < tpl.length) {
    const open = tpl.indexOf('{{', i);
    if (open < 0) { out += tpl.slice(i); break; }
    out += tpl.slice(i, open);
    const close = tpl.indexOf('}}', open);
    if (close < 0) { out += tpl.slice(open); break; }
    const tag = tpl.slice(open + 2, close).trim();
    i = close + 2;

    if (tag.startsWith('!')) continue;                       // comment
    if (tag.startsWith('#') || tag.startsWith('^')) {        // section
      const inverted = tag[0] === '^';
      const name = tag.slice(1).trim();
      const { start, end } = findSectionEnd(tpl, i, name);
      const body = tpl.slice(i, start);
      i = end;
      const val = lookup(frames, name);
      const truthy = Array.isArray(val) ? val.length > 0 : val !== undefined && val !== null && val !== false && val !== '';
      if (inverted ? !truthy : truthy) {
        if (Array.isArray(val)) for (const item of val) out += render(body, [...frames, item]);
        else out += render(body, [...frames, val]);
      }
    } else if (tag.startsWith('/')) {                        // stray end tag → ignore
    } else {                                                 // variable (HTML-escaped)
      const val = lookup(frames, tag);
      if (val !== undefined && val !== null) out += escapeHtml(String(val));
    }
  }
  return out;
}

/* ----------------------------- validation warnings ----------------------------- */

function warn(raw, view) {
  const expected = view.summaryWords;
  let entries = 0, bad = 0;
  for (const day of raw.days ?? []) {
    for (const e of day.entries ?? []) {
      entries++;
      const wc = (e.summary ?? '').trim().split(/\s+/).filter(Boolean).length;
      if (expected && wc !== expected) { bad++; console.error(`⚠ summary has ${wc} words (expected ${expected}): ${e.headline}`); }
      if (!e.url || !/^https?:\/\//.test(e.url)) console.error(`⚠ missing/invalid URL: ${e.headline}`);
    }
  }
  console.error(`✔ ${entries} entries rendered${bad ? `, ${bad} summary warnings` : ''}`);
}

/* ------------------------------------ main ------------------------------------ */

const [, , dataPath, outPath] = process.argv;
if (!dataPath) {
  console.error('Usage: node build.mjs <data.json> [out.html]');
  process.exit(1);
}

const raw = JSON.parse(readFileSync(dataPath, 'utf8'));
const view = normalize(raw);
const template = readFileSync(join(HERE, 'template.html'), 'utf8');
const html = render(template, [view]);
warn(raw, view);

if (outPath) { writeFileSync(outPath, html); console.error(`✔ written ${outPath} (${(html.length / 1024).toFixed(1)} KB)`); }
else process.stdout.write(html);
