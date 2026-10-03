#!/usr/bin/env node
// build — render the website in both languages from ONE template.
//
//   node site/build.mjs          # write the files below
//   node site/build.mjs --check  # exit 1 if a committed file is stale
//
// Why a generator for a static site: the site is two static pages (crawlers and
// AI agents must read both languages without running JS) and two hand-kept
// HTML files drift. No dependencies, no install: Node 18+ only. Inputs:
//   site/src/page.mjs   structure, links, code, the demo script, SITE (the domain)
//   site/src/en.mjs     English strings            → site/index.html
//   site/src/zh.mjs     Traditional Chinese strings → site/zh/index.html
// Also written: llms.txt, llms-full.txt (README + contract + docs), robots.txt,
// sitemap.xml, favicon.svg. The social card (assets/og.png) is a screenshot of
// site/src/og-card.html; see the comment in that file.

import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { renderPage, LOCALES, SITE, REPO, FAVICON_SVG } from './src/page.mjs';
import en from './src/en.mjs';
import zh from './src/zh.mjs';

const SITE_DIR = path.dirname(fileURLToPath(import.meta.url));
const ROOT = path.resolve(SITE_DIR, '..');
const RAW = 'https://raw.githubusercontent.com/frank890417/open-dictate/main/';

/** the daemon's version is the product version the page reports */
export function daemonVersion() {
  const src = fs.readFileSync(path.join(ROOT, 'daemon/dictated.py'), 'utf8');
  const m = src.match(/^__version__\s*=\s*"([^"]+)"/m);
  if (!m) throw new Error('could not read __version__ from daemon/dictated.py');
  return m[1];
}

/** Both string tables must have the same shape: same keys, same array lengths. */
export function shapeDiff(a, b, at = '') {
  const out = [];
  const kind = (v) => Array.isArray(v) ? 'array' : v === null ? 'null' : typeof v;
  if (kind(a) !== kind(b)) return [`${at || '(root)'}: ${kind(a)} vs ${kind(b)}`];
  if (kind(a) === 'array') {
    if (a.length !== b.length) out.push(`${at}: length ${a.length} vs ${b.length}`);
    for (let i = 0; i < Math.min(a.length, b.length); i++) out.push(...shapeDiff(a[i], b[i], `${at}[${i}]`));
  } else if (kind(a) === 'object') {
    for (const k of new Set([...Object.keys(a), ...Object.keys(b)])) {
      if (!(k in a)) out.push(`${at}.${k}: missing in en`);
      else if (!(k in b)) out.push(`${at}.${k}: missing in zh`);
      else out.push(...shapeDiff(a[k], b[k], `${at}.${k}`));
    }
  }
  return out;
}

function llms(version) {
  return `# Open Dictate

> Local-first push-to-talk dictation and meeting transcription for Traditional Chinese on Apple Silicon Macs. Hold a hotkey (fn or right Option), speak, release: a Swift menu-bar app records 16 kHz mono audio, a local Python daemon transcribes it with MLX Whisper (whisper-large-v3-turbo), applies the user's deterministic glossary pairs and full-width punctuation, and the app inserts the text at the cursor. Audio, transcripts, logs and glossaries stay on the Mac. MIT license. Early public seed (daemon ${version}).

Site: ${SITE} (English) · ${SITE}zh/ (繁體中文, Traditional Chinese)
Repository: ${REPO}
Author: Che-Yu Wu 吳哲宇 (https://cheyuwu.com)

## Core concepts

- **Pipeline (dictation)**: hold key → record WAV → daemon over Unix socket \`/tmp/open-dictate.sock\` (newline-delimited JSON) → MLX Whisper → glossary pairs → punctuation (\`smart_zh\` rules by default; optional local LLM \`llm_zh\` behind a no-rewrite gate; or \`raw\`) → JSON response → Accessibility insertion at the cursor (paste fallback).
- **Glossary pair**: a deterministic \`wrong → right\` replacement owned by the user. Only matching pairs are applied; sentences are never rewritten. Risky pairs live in \`_contextual\`, suggestions in \`_review_queue\`, decisions in \`_history\`.
- **Design rule**: the system may flag and suggest, but it must not silently rewrite meaning. Prefer missed corrections over wrong corrections. Numbers are not changed automatically.
- **Meeting Mode**: \`daemon/meeting_cli.py\` turns a local audio file or JSON/JSONL segments into Markdown, JSONL, SRT, VTT and meeting-result.json, with anonymous speaker labels (SPEAKER_00…) and QA flags.
- **Self-evolving glossary**: a scanner flags possible mishearings as candidates; the user accepts, edits or rejects (\`daemon/glossary/cli.py\`); only accepted pairs reach the glossary.
- **Privacy**: everything under \`~/.open-dictate/\`; speaker embeddings are biometric data, local only, never committed. Network use: PyPI at install, the Whisper model from Hugging Face (first run; the library may check for updates when loading), optional LLM punctuation via Ollama on 127.0.0.1.

## Status (from the README)

Public seed: push-to-talk dictation, local MLX Whisper daemon, deterministic glossary correction, Traditional Chinese punctuation, menu-bar teaching flow, meeting transcript package, local audio ASR for Meeting Mode. MVP: post-transcription mishearing flags, review-first glossary queue (CLI), anonymous speaker labels. Planned: local speaker identity, menu-bar review UI, glossary import/export, a self-contained signed and notarized app (see docs/MACOS-DISTRIBUTION-ROADMAP.md), an MCP server (see docs/MCP-ROADMAP.md; none exists yet).

## Install (Apple Silicon, macOS 14+, Xcode Command Line Tools, Python 3.11+)

\`\`\`bash
git clone https://github.com/frank890417/open-dictate.git
cd open-dictate
./install.sh                         # venv, builds OpenDictate.app into /Applications, LaunchAgents, model warm-up
./scripts/doctor.sh                  # read-only diagnostics
python3 daemon/dictate_cli.py ping   # daemon health
\`\`\`

Then the user must allow OpenDictate under Microphone, Accessibility and Input Monitoring (System Settings → Privacy & Security) and set "Press fn key to" → Do Nothing. An agent cannot grant these. Never delete \`~/.open-dictate\` (user glossary and logs); \`./uninstall.sh\` keeps it.

## Develop

- Read IO-CONTRACT.md before changing \`daemon/\` or \`OpenDictate/\`. Readers ignore unknown fields; keep the wire protocol (1.0) backward compatible.
- Tests: \`python3 -m unittest discover tests\`, \`python3 scripts/golden-bench.py --skip-daemon\`, \`python3 scripts/public-safety-scan.py\`, \`./build.sh\` for Swift, \`./scripts/smoke-test.sh\` for releases.
- Public fixtures must be fictional. Never commit real audio, transcripts, logs, personal glossaries or speaker profiles.

## Docs

- [README](${RAW}README.md): overview, feature status, quick start, architecture, roadmap.
- [IO-CONTRACT.md](${RAW}IO-CONTRACT.md): paths, socket protocol, glossary schema, correction rules, quality gates. Source of truth.
- [Setup](${RAW}docs/SETUP.md): requirements, permissions, hotkey conflicts, doctor, uninstall.
- [Privacy](${RAW}docs/PRIVACY.md): data classes and where they live.
- [Meeting Mode](${RAW}docs/MEETING.md): inputs, pipeline, segment schema.
- [Self-evolving glossary](${RAW}docs/SELF-EVOLVING-GLOSSARY.md): the review loop and its commands.
- [Speaker identity](${RAW}docs/SPEAKER-ID.md): anonymous labels now, optional local profiles later.
- [Contributing](${RAW}CONTRIBUTING.md) · [Security](${RAW}SECURITY.md)
- [Full text](${SITE}llms-full.txt): README, contract and every doc in one file.

## Optional

- [macOS distribution roadmap](${RAW}docs/MACOS-DISTRIBUTION-ROADMAP.md): self-contained app, Developer ID signing, notarization, updates (Traditional Chinese).
- [MCP roadmap](${RAW}docs/MCP-ROADMAP.md): a local, read-only stdio server first, consent-gated actions later.
- [open-audiovisual](https://openaudiovisual.com/): sibling project by the same author.
`;
}

const LLMS_FULL_SOURCES = ['README.md', 'IO-CONTRACT.md', 'docs/SETUP.md', 'docs/PRIVACY.md', 'docs/MEETING.md',
  'docs/SELF-EVOLVING-GLOSSARY.md', 'docs/SPEAKER-ID.md', 'docs/MACOS-DISTRIBUTION-ROADMAP.md', 'docs/MCP-ROADMAP.md',
  'CONTRIBUTING.md', 'SECURITY.md'];

function llmsFull() {
  const head = `# Open Dictate: full text for language models

> Every document of Open Dictate in one file: README, IO-CONTRACT.md and docs/.
> Generated by site/build.mjs from the repository; the short index is /llms.txt.
> Site: ${SITE} (English) · ${SITE}zh/ (繁體中文)
> Repository: ${REPO} (MIT)
`;
  return head + LLMS_FULL_SOURCES.map((f) =>
    `\n\n---\n\n<!-- source: ${f} -->\n\n` + fs.readFileSync(path.join(ROOT, f), 'utf8').trim()).join('') + '\n';
}

const robots = () => `User-agent: *
Allow: /

# A short guide for language models: ${SITE}llms.txt
# Everything in one file:          ${SITE}llms-full.txt
Sitemap: ${SITE}sitemap.xml
`;

function sitemap() {
  const alts = `
    <xhtml:link rel="alternate" hreflang="en" href="${LOCALES.en.url}"/>
    <xhtml:link rel="alternate" hreflang="zh-Hant" href="${LOCALES.zh.url}"/>
    <xhtml:link rel="alternate" hreflang="x-default" href="${LOCALES.en.url}"/>`;
  return `<?xml version="1.0" encoding="UTF-8"?>
<urlset xmlns="http://www.sitemaps.org/schemas/sitemap/0.9" xmlns:xhtml="http://www.w3.org/1999/xhtml">
  <url>
    <loc>${LOCALES.en.url}</loc>${alts}
  </url>
  <url>
    <loc>${LOCALES.zh.url}</loc>${alts}
  </url>
</urlset>
`;
}

export function buildAll() {
  const diff = shapeDiff(en, zh);
  if (diff.length) throw new Error('string tables differ in shape:\n  ' + diff.join('\n  '));
  const version = daemonVersion();
  return {
    [LOCALES.en.path + 'index.html']: renderPage(en, { locale: 'en', other: 'zh', version }),
    [LOCALES.zh.path + 'index.html']: renderPage(zh, { locale: 'zh', other: 'en', version }),
    'llms.txt': llms(version),
    'llms-full.txt': llmsFull(),
    'robots.txt': robots(),
    'sitemap.xml': sitemap(),
    'favicon.svg': FAVICON_SVG + '\n',
  };
}

if (process.argv[1] && fileURLToPath(import.meta.url) === path.resolve(process.argv[1])) {
  const check = process.argv.includes('--check');
  const files = buildAll();
  const stale = [];
  for (const [rel, out] of Object.entries(files)) {
    const file = path.join(SITE_DIR, rel);
    const cur = fs.existsSync(file) ? fs.readFileSync(file, 'utf8') : null;
    if (check) { if (cur !== out) stale.push(rel); continue; }
    fs.mkdirSync(path.dirname(file), { recursive: true });
    fs.writeFileSync(file, out);
    console.log('wrote', 'site/' + rel, `(${out.length} chars)`);
  }
  if (check) {
    if (stale.length) { console.error('stale (run node site/build.mjs):\n  ' + stale.join('\n  ')); process.exit(1); }
    console.log('site is up to date (en, zh, llms.txt, llms-full.txt, robots.txt, sitemap.xml, favicon.svg)');
  }
}
