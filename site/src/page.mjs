// The page template: one structure, rendered once per language by site/build.mjs
// (strings: en.mjs → /, zh.mjs → /zh/). Everything that must not drift between
// the two languages lives HERE: section order, ids, links, code, paths, the
// demo script.
//
// Output is static HTML on purpose: crawlers and AI agents read every word
// without running JavaScript. assets/site.js only animates the demo, copies
// code and remembers the language.
//
// ── Moving to a custom domain ─────────────────────────────────────────────
// Every link inside the site is relative, so the pages work under
// /open-dictate/ (GitHub Pages) and at the root of any domain unchanged.
// Only absolute URLs (canonical, hreflang, og:url, og:image, JSON-LD,
// llms.txt, robots.txt, sitemap.xml) use SITE below. To switch domains:
//   1. change SITE (the one line below), e.g. 'https://opendictate.app/'
//   2. run `node site/build.mjs` and commit the regenerated files
//   3. set the custom domain in GitHub → Settings → Pages (the Actions
//      deploy does not read a CNAME file), then update the README link.
// ──────────────────────────────────────────────────────────────────────────

export const SITE = 'https://frank890417.github.io/open-dictate/';
export const REPO = 'https://github.com/frank890417/open-dictate';
export const BLOB = REPO + '/blob/main/';
const TREE = REPO + '/tree/main/';
const AUTHOR_URL = { en: 'https://cheyuwu.com/', zh: 'https://cheyuwu.com/zh/' };
const SIBLING = { en: 'https://openaudiovisual.com/', zh: 'https://openaudiovisual.com/zh/' };

export const LOCALES = {
  en: { path: '', root: './', url: SITE },
  zh: { path: 'zh/', root: '../', url: SITE + 'zh/' },
};

export const SECTIONS = ['demo', 'how', 'status', 'privacy', 'start', 'develop', 'agents', 'lineage'];

// ---------- the demo script (identical on both pages; the speech is Chinese) ----------
// Each utterance is what the daemon would see and answer. The glossary pairs in
// 1 and 5 are pairs a user taught; the pairs in 2 come from the starter glossary
// (vendor/tools/td-subtitle/glossaries/general-zh.json). Raw text is Whisper
// output; smart_zh turns half-width marks after Chinese into full-width ones.
export const UTTERANCES = [
  { dur: 3.4, raw: '幫我把 touch designer 的檔案放上 git hub 的專案頁,明天下午三點前寄給大家.',
    changes: [['touch designer', 'TouchDesigner'], ['git hub', 'GitHub']],
    text: '幫我把 TouchDesigner 的檔案放上 GitHub 的專案頁，明天下午三點前寄給大家。' },
  { dur: 3.1, raw: '那個那個那個那個,我們下週在臺灣辦一場工作坊,名額三十人.',
    changes: [['那個那個那個那個', '那個'], ['臺灣', '台灣']],
    text: '那個，我們下週在台灣辦一場工作坊，名額三十人。' },
  { dur: 0.3, ignored: true },
  { dur: 2.2, raw: '記得把筆記同步到阿布西店.', changes: [],
    text: '記得把筆記同步到阿布西店。', teach: ['阿布西店', 'Obsidian'] },
  { dur: 2.3, raw: '記得把筆記同步到阿布西店.', changes: [['阿布西店', 'Obsidian']],
    text: '記得把筆記同步到Obsidian。' },
];
const DOC_TITLE = '週會筆記';

// feature table, in README order; the status of each row is README's, not ours
export const FEATURES = ['seed', 'seed', 'seed', 'seed', 'seed', 'seed', 'seed', 'mvp', 'mvp', 'mvp', 'planned'];
const DATA_PATHS = ['~/.open-dictate/dictation-log/', '~/.open-dictate/meetings/', '~/.open-dictate/review-queue/',
  '~/.open-dictate/glossaries/', '~/.open-dictate/speakers/', 'fixtures/ · examples/'];
const DATA_REPO = ['no', 'no', 'no', 'default', 'never', 'yes'];
const COMPONENTS = [
  ['OpenDictate/', 'tree'], ['daemon/dictated.py', 'blob'], ['daemon/meeting_cli.py', 'blob'],
  ['daemon/qa/mishear_detector.py', 'blob'], ['daemon/glossary/', 'tree'], ['daemon/speaker/', 'tree'],
  ['vendor/tools/td-subtitle/glossaries/', 'tree'], ['vendor/tools/muse-lexicon/muse_lexicon.py', 'blob'],
  ['~/.open-dictate/dictation-log/', null],
];
const DOCS = [
  ['contract', 'IO-CONTRACT.md'], ['setup', 'docs/SETUP.md'], ['privacy', 'docs/PRIVACY.md'],
  ['meeting', 'docs/MEETING.md'], ['glossary', 'docs/SELF-EVOLVING-GLOSSARY.md'], ['speaker', 'docs/SPEAKER-ID.md'],
  ['distribution', 'docs/MACOS-DISTRIBUTION-ROADMAP.md'], ['mcp', 'docs/MCP-ROADMAP.md'], ['contributing', 'CONTRIBUTING.md'],
];

// ---------- code shown on the page (identical in both languages) ----------
const CMD_INSTALL = `git clone https://github.com/frank890417/open-dictate.git
cd open-dictate
./install.sh        # venv, app, LaunchAgents, model warm-up`;

const CMD_MAINT = `./scripts/doctor.sh            # read-only checks, then a real ping
./uninstall.sh                 # removes the app and LaunchAgents, keeps ~/.open-dictate
./uninstall.sh --purge-data    # shows where your data is; never deletes it`;

const CMD_CLI = `# Meeting Mode: a local recording in, a reviewable package out
python3 daemon/meeting_cli.py transcribe ~/Desktop/meeting.m4a --out /tmp/od-meeting --language zh

# the review queue: look, then decide
python3 daemon/glossary/cli.py candidates
python3 daemon/glossary/cli.py accept cand_xxxxx`;

const ARCH_DICTATE = `hold fn
  |
OpenDictate.app (Swift)       records 16 kHz mono PCM16 WAV
  |  {"cmd": "transcribe", "wav": "/tmp/...wav", "punct": "smart_zh"}
  v
/tmp/open-dictate.sock        newline-delimited JSON
  |
dictated.py (Python, warm)
  |-- MLX Whisper             audio -> raw text
  |-- glossary pairs          raw -> corrected   (muse_lexicon)
  |-- punctuation             smart_zh | llm_zh (gated) | raw
  |-- log                     ~/.open-dictate/dictation-log/
  v
{"ok": true, "text": "...", "raw": "...", "changes": [...]}
  |
OpenDictate.app  ->  Accessibility insert (paste fallback)  ->  cursor`;

const ARCH_MEETING = `meeting.m4a   or   segments.json / .jsonl
  |
meeting_cli.py transcribe
  |-- MLX Whisper             audio files only
  |-- glossary pairs          the same deterministic table
  |-- speaker labels          SPEAKER_00, SPEAKER_01 (anonymous)
  |-- QA flags                possible mishearings, numbers
  v
transcript.md  .jsonl  .srt  .vtt  meeting-result.json`;

const PROTO = `# requests: one JSON object per line on /tmp/open-dictate.sock
{"cmd": "transcribe", "wav": "/tmp/open-dictate-rec-....wav", "punct": "smart_zh"}
{"cmd": "ping"}
{"cmd": "reload_lexicon"}
{"cmd": "add_pair", "wrong": "誤聽", "right": "正確", "source": "dictate-ui"}
{"cmd": "stats"}

# responses
{"ok": true, "text": "校正後文字", "raw": "whisper 原始輸出", "changes": [["誤聽", "正確"]], "punct": "smart_zh"}
{"ok": false, "error": "no_speech"}`;

const CMD_TEST = `./build.sh
python3 -m unittest discover tests
python3 scripts/golden-bench.py --skip-daemon
python3 scripts/public-safety-scan.py
./scripts/smoke-test.sh        # builds, tests, exports a meeting demo, checks the signed app`;

// ---------- helpers ----------
export const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
const attr = (s) => esc(s).replace(/"/g, '&quot;');

/** tiny, deliberate highlighter: # comments and strings. Escapes as it goes. */
function hl(code) {
  let out = '', i = 0;
  const re = /(#[^\n]*)|("(?:[^"\\]|\\.)*")/g;
  let m;
  while ((m = re.exec(code))) {
    out += esc(code.slice(i, m.index));
    if (m[1]) {
      const prev = m.index ? code[m.index - 1] : '\n';
      if (!/\s/.test(prev)) { out += '#'; re.lastIndex = m.index + 1; i = m.index + 1; continue; }
      out += `<span class="tc">${esc(m[1])}</span>`;
    } else out += `<span class="ts">${esc(m[2])}</span>`;
    i = re.lastIndex;
  }
  return out + esc(code.slice(i));
}

/** split an utterance's final text into plain runs and glossary fixes */
export function segments(u) {
  if (u.ignored) return [];
  const marks = [];
  for (const [was, now] of u.changes) {
    const i = u.text.indexOf(now);
    if (i >= 0) marks.push({ i, len: now.length, was });
  }
  // a mishearing no pair caught: typed as heard, marked so you can teach it
  if (u.teach) {
    const i = u.text.indexOf(u.teach[0]);
    if (i >= 0) marks.push({ i, len: u.teach[0].length, heard: true });
  }
  marks.sort((a, b) => a.i - b.i);
  const segs = [];
  let p = 0;
  for (const m of marks) {
    if (m.i < p) continue;
    if (m.i > p) segs.push({ t: u.text.slice(p, m.i) });
    segs.push(m.heard ? { t: u.text.slice(m.i, m.i + m.len), heard: true } : { t: u.text.slice(m.i, m.i + m.len), was: m.was });
    p = m.i + m.len;
  }
  if (p < u.text.length) segs.push({ t: u.text.slice(p) });
  return segs;
}

const PUNCT_MAP = { ',': '，', '.': '。', '?': '？', '!': '！', ':': '：', ';': '；' };
function punctMarks(u) {
  const seen = [...new Set([...(u.raw || '')].filter((c) => c in PUNCT_MAP))];
  return seen.map((c) => `${c} → ${PUNCT_MAP[c]}`).join('   ');
}

const jstr = (s) => JSON.stringify(s);
function wireText(u, t) {
  if (u.ignored) return t.demo.nothingSent;
  const req = `→ {"cmd": "transcribe", "wav": "/tmp/open-dictate-rec-….wav", "punct": "smart_zh"}`;
  const ch = '[' + u.changes.map(([a, b]) => `[${jstr(a)}, ${jstr(b)}]`).join(', ') + ']';
  return `${req}\n← {"ok": true,\n   "text": ${jstr(u.text)},\n   "raw": ${jstr(u.raw)},\n   "changes": ${ch},\n   "punct": "smart_zh"}`;
}
const teachWire = (u) => u.teach
  ? `→ {"cmd": "add_pair", "wrong": ${jstr(u.teach[0])}, "right": ${jstr(u.teach[1])}, "source": "dictate-ui"}\n← {"ok": true}` : '';

/** the five pipeline readouts of one utterance (strings; changes is an array) */
function rows(u, t) {
  const sec = (d) => d.toFixed(1) + ' s';
  if (u.ignored) return [`${sec(u.dur)} < 0.5 s`, '—', '—', '—', t.demo.notTyped];
  return [
    `${sec(u.dur)} · 16 kHz mono`,
    u.raw,
    u.changes.length ? u.changes.map(([a, b]) => `${a} → ${b}`) : [t.demo.noChange],
    punctMarks(u),
    t.demo.typed,
  ];
}

function lineHTML(u) {
  return segments(u).map((s) => s.was ? `<span class="fix" data-was="${attr(s.was)}">${esc(s.t)}</span>`
    : s.heard ? `<span class="heard">${esc(s.t)}</span>`
    : esc(s.t).replace(/[，。？！：；]/g, (c) => `<span class="pm">${c}</span>`)).join('');
}

const mark = `<svg class="mark" viewBox="0 0 32 32" aria-hidden="true" focusable="false"><path d="M3 16c1.7 0 1.9-7 3.9-7s2.1 14 4.1 14 2.1-7 3.9-7"/><rect x="21" y="6" width="3.2" height="20" rx=".6"/></svg>`;
export const FAVICON_SVG = `<svg xmlns="http://www.w3.org/2000/svg" viewBox="0 0 32 32"><rect width="32" height="32" rx="6" fill="#0b0b0c"/><path d="M4 16c1.6 0 1.8-6.5 3.6-6.5s2 13 3.8 13 2-6.5 3.6-6.5" fill="none" stroke="#edebe5" stroke-width="2.4" stroke-linecap="round"/><rect x="20.5" y="6.5" width="3.4" height="19" rx=".6" fill="#2ee6c6"/></svg>`;
const FAVICON = 'data:image/svg+xml,' + encodeURIComponent(FAVICON_SVG);

// ---------- the page ----------
export function renderPage(t, { locale, other, version }) {
  const L = LOCALES[locale];
  const root = L.root;
  const self = L.url;
  const href = (h) => h.startsWith('#') || /^https?:/.test(h) ? h : root + h;
  const nav = ['how', 'privacy', 'start', 'develop', 'agents'];
  const langHref = { en: locale === 'en' ? './' : '../', zh: locale === 'en' ? './zh/' : './' };
  const altPage = langHref[other];
  const U0 = UTTERANCES[0];
  const r0 = rows(U0, t);

  const ld = {
    '@context': 'https://schema.org',
    '@type': 'SoftwareApplication',
    name: 'Open Dictate',
    alternateName: 'open-dictate',
    description: t.meta.ld,
    url: self,
    inLanguage: t.ldLanguage,
    applicationCategory: 'UtilitiesApplication',
    operatingSystem: 'macOS 14 or later, Apple Silicon',
    softwareVersion: version,
    softwareRequirements: 'Apple Silicon Mac; macOS 14+; Xcode Command Line Tools; Python 3.11+',
    featureList: t.meta.features,
    downloadUrl: REPO,
    installUrl: self + '#start',
    license: 'https://opensource.org/licenses/MIT',
    isAccessibleForFree: true,
    offers: { '@type': 'Offer', price: '0', priceCurrency: 'USD' },
    author: { '@type': 'Person', name: 'Che-Yu Wu', alternateName: '吳哲宇', url: 'https://cheyuwu.com' },
    sameAs: [REPO],
    image: SITE + 'assets/og.png',
  };

  // everything the demo needs at runtime, precomputed so the script stays small
  const demo = {
    u: UTTERANCES.map((u, i) => ({
      dur: u.dur, ignored: !!u.ignored, segs: segments(u), rows: rows(u, t),
      wire: wireText(u, t), teach: u.teach || null, teachWire: teachWire(u), note: t.demo.notes[i],
    })),
    s: { ...t.demo.status, typed: t.demo.typed, transcribing: t.demo.transcribing, taught: t.demo.taught,
      nothingSent: t.demo.nothingSent, copy: t.ui.copy, copied: t.ui.copied },
  };

  const sec = (id, eyebrow, title, lede = '') => `
    <header class="sec-head">
      <p class="eyebrow">${eyebrow}</p>
      <h2 id="${id}-title">${title}</h2>
      ${lede ? `<p class="sec-lede">${lede}</p>` : ''}
    </header>`;

  const copyBtn = (target) => `<button type="button" class="copy" data-copy="${target}">${t.ui.copy}</button>`;
  const term = (label, id, code, { copy = true, cls = '', raw = false } = {}) => `
      <div class="term ${cls}">
        <div class="term-bar"><span>${label}</span>${copy ? copyBtn(id) : ''}</div>
        <pre id="${id}"><code>${raw ? esc(code) : hl(code)}</code></pre>
      </div>`;
  const pair = (x) => { const [a, b] = x.split(' → '); return b === undefined ? esc(x) : `<s>${esc(a)}</s> → <b>${esc(b)}</b>`; };
  const stepVal = (v) => Array.isArray(v) ? v.map((x) => `<span>${pair(x)}</span>`).join('') : esc(v);

  return `<!DOCTYPE html>
<html lang="${t.htmlLang}">
<head>
<meta charset="utf-8">
<meta name="viewport" content="width=device-width, initial-scale=1">
<title>${esc(t.meta.title)}</title>
<meta name="description" content="${attr(t.meta.description)}">
<link rel="canonical" href="${self}">
<link rel="alternate" hreflang="en" href="${LOCALES.en.url}">
<link rel="alternate" hreflang="zh-Hant" href="${LOCALES.zh.url}">
<link rel="alternate" hreflang="x-default" href="${LOCALES.en.url}">
<link rel="alternate" type="text/markdown" href="${root}llms.txt" title="llms.txt">
<meta name="theme-color" content="#0b0b0c">
<meta name="color-scheme" content="dark">
<meta property="og:type" content="website">
<meta property="og:site_name" content="Open Dictate">
<meta property="og:title" content="${attr(t.meta.title)}">
<meta property="og:description" content="${attr(t.meta.ogDescription)}">
<meta property="og:url" content="${self}">
<meta property="og:locale" content="${t.ogLocale}">
<meta property="og:image" content="${SITE}assets/og.png">
<meta property="og:image:width" content="1200">
<meta property="og:image:height" content="630">
<meta name="twitter:card" content="summary_large_image">
<link rel="icon" href="${FAVICON}">
<link rel="preload" href="${root}assets/fonts/archivo-var-latin.woff2" as="font" type="font/woff2" crossorigin>
<link rel="stylesheet" href="${root}assets/site.css">
<noscript><style>.key { pointer-events: none; } .key-hint, .copy, .status #status-text { display: none; }</style></noscript>
<script type="application/ld+json">
${JSON.stringify(ld, null, 2)}
</script>
</head>
<body>
<a class="skip" href="#main">${t.ui.skip}</a>

<header class="top">
  <a class="brand" href="./" aria-label="${attr(t.ui.home)}">${mark}<span>open-dictate</span></a>
  <nav class="nav" aria-label="${attr(t.ui.navLabel)}">
    ${nav.map((k) => `<a href="#${k}">${t.ui.nav[k]}</a>`).join('\n    ')}
  </nav>
  <div class="top-end">
    <div class="langs" role="group" aria-label="${attr(t.ui.langLabel)}">
      <a href="${langHref.en}" hreflang="en" lang="en" data-lang="en"${locale === 'en' ? ' aria-current="page"' : ''}>EN</a><span aria-hidden="true">|</span><a href="${langHref.zh}" hreflang="zh-Hant" lang="zh-Hant-TW" data-lang="zh"${locale === 'zh' ? ' aria-current="page"' : ''}>中文</a>
    </div>
    <a class="gh" href="${REPO}">GitHub</a>
  </div>
</header>

<main id="main">

  <!-- ═════ 01 · hold, speak, release: the demo ═════ -->
  <section class="hero" id="demo" aria-labelledby="hero-title">
    <h1 id="hero-title"><span class="wordmark">open-dictate</span> <span class="h1-sub">${t.hero.sub}</span></h1>
    <div class="hero-grid">
      <div class="hero-text">
        <p class="lede">${t.hero.lede}</p>
        <p class="eyebrow facts">${t.hero.eyebrow.replace('{v}', version)}</p>
      </div>
      <nav class="doors" aria-label="${attr(t.hero.doorsLabel)}">
        ${t.hero.doors.map((d, i) => `<a class="door door-${i + 1}" href="${href(d.href)}"><span class="door-who">${d.who}</span><span class="door-what">${d.what}</span><span class="door-arrow" aria-hidden="true">→</span></a>`).join('\n        ')}
      </nav>
    </div>

    <div class="desk" id="desk" aria-labelledby="desk-title">
      <h2 class="desk-title" id="desk-title"><span class="eyebrow">${t.demo.eyebrow}</span> ${t.demo.title}</h2>
      <div class="field">
        <div class="field-bar"><span>${t.demo.fieldLabel}</span><span class="field-tag">${t.demo.demoTag}</span></div>
        <div class="doc" id="doc">
          <p class="doc-h">${DOC_TITLE}</p>
          <p class="line is-new">${lineHTML(U0)}<span class="caret" aria-hidden="true"></span></p>
        </div>
        <p class="doc-note" id="doc-note">${t.demo.notes[0]}</p>
      </div>
      <div class="keyrow">
        <button type="button" class="key" id="key" aria-label="${attr(t.demo.keyLabel)}" aria-describedby="key-hint">fn</button>
        <canvas class="wave" id="wave" width="600" height="56" aria-hidden="true"></canvas>
        <span class="timer" id="timer">${U0.dur.toFixed(1)} s</span>
      </div>
      <p class="status" id="desk-status"><span class="led" aria-hidden="true"></span><span id="status-text">${t.demo.status.idle}</span></p>
      <aside class="bay" aria-labelledby="bay-title">
        <div class="bay-head"><h3 id="bay-title">${t.demo.bayTitle}</h3><span class="bay-live"><span class="led" aria-hidden="true"></span>${t.demo.bayLive}</span></div>
        <ol class="pipe" id="pipe">
          ${t.demo.steps.map((s, i) => `<li class="step step-${i}" data-st="done"><span class="step-n">0${i + 1}</span><span class="step-name">${s.name}<small>${s.sub}</small></span><span class="step-v" id="v${i}">${stepVal(r0[i])}</span></li>`).join('\n          ')}
        </ol>
        <div class="wire">
          <p class="wire-h">${t.demo.wireTitle}</p>
          <pre id="wire"><code>${esc(wireText(U0, t))}</code></pre>
        </div>
        <p class="bay-legend">${t.demo.legend}</p>
      </aside>
      <p class="key-hint" id="key-hint">${t.demo.keyHint}</p>
    </div>
    <p class="desk-caption">${t.demo.caption} <a href="${BLOB}IO-CONTRACT.md">${t.demo.contractLink} →</a></p>
    <noscript><p class="desk-caption">${t.demo.noscript}</p></noscript>
  </section>

  <!-- ═════ 02 · how it works ═════ -->
  <section class="sec" id="how" aria-labelledby="how-title">
    ${sec('how', t.how.eyebrow, t.how.title, t.how.lede)}
    <ol class="layers">
      ${t.how.layers.map((x, i) => `<li class="layer layer-${i + 1}">
        <p class="layer-tag"><span>0${i + 1}</span> ${x.name}</p>
        <p class="layer-quote">${x.quote}</p>
        <p class="layer-text">${x.text}</p>
        <p class="layer-spec"><code>${[
          `<span class="k">rec</span> = <b data-live="rec">${U0.dur.toFixed(1)} s</b>`,
          `<span class="k">model</span> = <b>large-v3-turbo</b>`,
          `<span class="k">changes</span> = <b data-live="changes">${U0.changes.length}</b>`,
          `<span class="k">punct</span> = <b>"smart_zh"</b>`,
          `<span class="k">insert</span> → <b>AX</b> <span class="c">· paste</span>`,
        ][i]}</code><small>${t.how.specimenLabel}</small></p>
      </li>`).join('\n      ')}
    </ol>

    <div class="how-grid">
      <div>
        <h3>${t.how.spinesTitle}</h3>
        <dl class="spines">
          ${t.how.spines.map((s) => `<div><dt>${s.name}</dt><dd>${s.text}</dd></div>`).join('\n          ')}
        </dl>
      </div>
      <div>
        <h3>${t.how.rulesTitle}</h3>
        <ol class="rules">
          ${t.how.rules.map((r) => `<li><strong>${r.head}</strong> ${r.text}</li>`).join('\n          ')}
        </ol>
      </div>
    </div>

    <h3 class="glossary-title">${t.how.glossaryTitle}</h3>
    <dl class="glossary">
      ${t.how.glossary.map((g) => `<div><dt><dfn>${g.term}</dfn></dt><dd>${g.def}</dd></div>`).join('\n      ')}
    </dl>
  </section>

  <!-- ═════ 03 · status ═════ -->
  <section class="sec" id="status" aria-labelledby="status-title">
    ${sec('status', t.status.eyebrow, t.status.title, t.status.lede)}
    <div class="status-grid">
      <div>
        <h3>${t.status.featuresTitle}</h3>
        <ul class="feats">
          ${t.status.features.map((f, i) => `<li><span class="feat-name">${f.name}</span><span class="chip chip-${FEATURES[i]}">${t.status.statusNames[FEATURES[i]]}</span>${f.note ? `<span class="feat-note">${f.note}</span>` : ''}</li>`).join('\n          ')}
        </ul>
      </div>
      <div>
        <h3>${t.status.limitsTitle}</h3>
        <ul class="limits">
          ${t.status.limits.map((l) => `<li>${l}</li>`).join('\n          ')}
        </ul>
      </div>
    </div>
    <h3 class="phases-title">${t.status.roadmapTitle}</h3>
    <ol class="phases">
      ${t.status.phases.map((p, i) => `<li><span class="phase-n">${t.status.phaseLabel} ${i}</span><strong>${p.name}</strong> ${p.text}</li>`).join('\n      ')}
    </ol>
    <p><a class="more" href="${BLOB}docs/MACOS-DISTRIBUTION-ROADMAP.md">${t.status.roadmapLink} →</a></p>
  </section>

  <!-- ═════ 04 · privacy ═════ -->
  <section class="sec" id="privacy" aria-labelledby="privacy-title">
    ${sec('privacy', t.privacy.eyebrow, t.privacy.title, t.privacy.lede)}
    <div class="privacy-grid">
      <div>
        <h3>${t.privacy.dataTitle}</h3>
        <table class="data">
          <thead><tr>${t.privacy.dataHead.map((h) => `<th scope="col">${h}</th>`).join('')}</tr></thead>
          <tbody>
            ${t.privacy.data.map((d, i) => `<tr class="repo-${DATA_REPO[i]}"><th scope="row">${d.name}</th><td><code>${DATA_PATHS[i]}</code></td><td>${d.repo}</td></tr>`).join('\n            ')}
          </tbody>
        </table>
      </div>
      <div>
        <h3>${t.privacy.netTitle}</h3>
        <ol class="net">
          ${t.privacy.net.map((n) => `<li>${n}</li>`).join('\n          ')}
        </ol>
        <p class="net-end">${t.privacy.netEnd}</p>
      </div>
    </div>
    <figure class="pull pull-voice">
      <blockquote><p>${t.privacy.voiceQuote}</p></blockquote>
      <figcaption>${t.privacy.voiceText}</figcaption>
    </figure>
    <p><a class="more" href="${BLOB}docs/PRIVACY.md">${t.privacy.link} →</a></p>
  </section>

  <!-- ═════ 05 · start (people who want to use it) ═════ -->
  <section class="sec" id="start" aria-labelledby="start-title">
    ${sec('start', t.start.eyebrow, t.start.title, t.start.lede)}
    <div class="start-grid">
      <div class="start-run">
        ${term('terminal', 'cmd-install', CMD_INSTALL)}
        <p>${t.start.after}</p>
        <p class="note">${t.start.slow}</p>
      </div>
      <div>
        <h3>${t.start.permsTitle}</h3>
        <ol class="perms">
          ${t.start.perms.map((p) => `<li>${p}</li>`).join('\n          ')}
        </ol>
      </div>
    </div>
    <div class="start-grid start-more">
      <div>
        <h3>${t.start.maintTitle}</h3>
        ${term('terminal', 'cmd-maint', CMD_MAINT, { cls: 'term-quiet' })}
        <p class="note">${t.start.maintNote}</p>
      </div>
      <div>
        <h3>${t.start.cliTitle}</h3>
        ${term('terminal', 'cmd-cli', CMD_CLI, { cls: 'term-quiet' })}
        <p class="note">${t.start.cliNote}</p>
      </div>
    </div>
    <p><a class="more" href="${BLOB}docs/SETUP.md">${t.start.setupLink} →</a></p>
  </section>

  <!-- ═════ 06 · develop ═════ -->
  <section class="sec" id="develop" aria-labelledby="develop-title">
    ${sec('develop', t.develop.eyebrow, t.develop.title, t.develop.lede)}
    <div class="dev-grid">
      <div>
        <h3>${t.develop.archTitle}</h3>
        ${term(t.develop.archDictate, 'arch-dictate', ARCH_DICTATE, { copy: false, cls: 'term-diagram', raw: true })}
        ${term(t.develop.archMeeting, 'arch-meeting', ARCH_MEETING, { copy: false, cls: 'term-diagram', raw: true })}
      </div>
      <div>
        <h3>${t.develop.protoTitle}</h3>
        <p>${t.develop.protoText}</p>
        ${term('IO-CONTRACT.md', 'proto', PROTO, { copy: false, cls: 'term-quiet' })}
        <p class="note">${t.develop.protoNote}</p>
      </div>
    </div>
    <div class="dev-grid dev-more">
      <div>
        <h3>${t.develop.compTitle}</h3>
        <table class="comps">
          <thead><tr>${t.develop.compHead.map((h) => `<th scope="col">${h}</th>`).join('')}</tr></thead>
          <tbody>
            ${COMPONENTS.map(([p, kind], i) => `<tr><th scope="row">${t.develop.comps[i]}</th><td>${kind ? `<a href="${kind === 'tree' ? TREE : BLOB}${p}"><code>${p}</code></a>` : `<code>${p}</code>`}</td></tr>`).join('\n            ')}
          </tbody>
        </table>
      </div>
      <div>
        <h3>${t.develop.testTitle}</h3>
        ${term('terminal', 'cmd-test', CMD_TEST)}
        <h4>${t.develop.contribTitle}</h4>
        <ul class="contrib">
          ${t.develop.contrib.map((c) => `<li>${c}</li>`).join('\n          ')}
        </ul>
      </div>
    </div>
    <h3 class="docs-title">${t.develop.docsTitle}</h3>
    <p class="note">${t.develop.docsNote}</p>
    <ul class="docs">
      ${DOCS.map(([k, p]) => `<li><a href="${BLOB}${p}">${t.develop.docNames[k]}</a><span>${t.develop.docs[k]}</span></li>`).join('\n      ')}
    </ul>
  </section>

  <!-- ═════ 07 · for AI agents ═════ -->
  <section class="sec sec-agents" id="agents" aria-labelledby="agents-title">
    ${sec('agents', t.agents.eyebrow, t.agents.title, t.agents.lede)}
    <div class="agents-grid">
      <div>
        <h3>${t.agents.installTitle}</h3>
        <div class="term term-prompt">
          <div class="term-bar"><span>prompt</span>${copyBtn('prompt-install')}</div>
          <pre id="prompt-install"><code>${esc(t.agents.installPrompt.join('\n'))}</code></pre>
        </div>
      </div>
      <div>
        <h3>${t.agents.devTitle}</h3>
        <div class="term term-prompt">
          <div class="term-bar"><span>prompt</span>${copyBtn('prompt-dev')}</div>
          <pre id="prompt-dev"><code>${esc(t.agents.devPrompt.join('\n'))}</code></pre>
        </div>
      </div>
    </div>
    <div class="agents-grid">
      <div>
        <h3>${t.agents.filesTitle}</h3>
        <ul class="files">
          <li><a href="${root}llms.txt">/llms.txt</a><span>${t.agents.files.llms}</span></li>
          <li><a href="${root}llms-full.txt">/llms-full.txt</a><span>${t.agents.files.llmsFull}</span></li>
          <li><a href="${BLOB}IO-CONTRACT.md">IO-CONTRACT.md</a><span>${t.agents.files.contract}</span></li>
          <li><code>application/ld+json</code><span>${t.agents.files.ld}</span></li>
          <li><a href="${altPage}" hreflang="${other === 'zh' ? 'zh-Hant' : 'en'}">${other === 'zh' ? '/zh/' : '/'}</a><span>${t.agents.files.alt}</span></li>
        </ul>
      </div>
      <div>
        <h3>${t.agents.mcpTitle}</h3>
        <p>${t.agents.mcpText}</p>
        <p><a class="more" href="${BLOB}docs/MCP-ROADMAP.md">${t.agents.mcpLink} →</a></p>
      </div>
    </div>
  </section>

  <!-- ═════ 08 · lineage ═════ -->
  <section class="sec" id="lineage" aria-labelledby="lineage-title">
    ${sec('lineage', t.lineage.eyebrow, t.lineage.title)}
    <div class="lineage-grid">
      <div class="prose">
        <p>${t.lineage.p1}</p>
        <p>${t.lineage.p2.replace('{sibling}', SIBLING[locale])}</p>
      </div>
      <figure class="pull">
        <blockquote><p>${t.lineage.quote}</p></blockquote>
        <figcaption>${t.lineage.quoteCite}</figcaption>
      </figure>
    </div>
    <h3>${t.lineage.nextTitle}</h3>
    <ol class="next">
      ${t.lineage.next.map((n) => `<li>${n}</li>`).join('\n      ')}
    </ol>
    <p><a class="more" href="${REPO}#路線圖--roadmap">${t.lineage.roadmapLink} →</a></p>
  </section>

</main>

<footer class="foot">
  <p class="foot-mark" aria-hidden="true">open-dictate</p>
  <div class="foot-grid">
    <div>
      <p>${t.footer.license.replace('{author}', AUTHOR_URL[locale])}</p>
      <p class="note">${t.footer.sibling.replace('{sibling}', SIBLING[locale])}</p>
    </div>
    <nav aria-label="${attr(t.footer.machine)}">
      <a href="${REPO}">GitHub</a>
      <a href="${root}llms.txt">llms.txt</a>
      <a href="${root}llms-full.txt">llms-full.txt</a>
      <a href="${BLOB}IO-CONTRACT.md">IO-CONTRACT.md</a>
    </nav>
  </div>
</footer>

<script type="application/json" id="od-demo">${JSON.stringify(demo).replace(/</g, '\\u003c')}</script>
<script src="${root}assets/site.js" defer></script>
</body>
</html>
`;
}
