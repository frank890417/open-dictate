// The website is two static pages (en at /, zh at /zh/) rendered from one
// template by site/build.mjs. These tests keep them honest: same shape, same
// sections, same links, nothing stale, nothing broken, no feature promoted
// beyond what the README says.
//
//   node --test site/test/site.test.mjs
import { test } from 'node:test';
import assert from 'node:assert/strict';
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { buildAll, shapeDiff } from '../build.mjs';
import { SITE, REPO, SECTIONS, FEATURES, UTTERANCES, segments } from '../src/page.mjs';
import en from '../src/en.mjs';
import zh from '../src/zh.mjs';

const SITE_DIR = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '..');
const ROOT = path.resolve(SITE_DIR, '..');
const PAGES = { en: { file: 'index.html', url: SITE }, zh: { file: 'zh/index.html', url: SITE + 'zh/' } };
const read = (rel) => fs.readFileSync(path.join(SITE_DIR, rel), 'utf8');
const html = { en: read(PAGES.en.file), zh: read(PAGES.zh.file) };
const all = (re, s) => [...s.matchAll(re)].map((m) => m[1]);
const body = (s) => s.slice(s.indexOf('<body'));

// one language's link, expressed so that the other language's twin is identical
const normLink = (href, base) => new URL(href, base).href
  .replace(SITE + 'zh/', SITE)
  .replace(/^(https?:\/\/(?:cheyuwu\.com|openaudiovisual\.com))\/zh(\/|$)/, '$1/');

test('string tables have the same shape (every key, every array length)', () => {
  assert.deepEqual(shapeDiff(en, zh), []);
});

test('committed files are what the template renders (run node site/build.mjs)', () => {
  for (const [rel, out] of Object.entries(buildAll())) assert.equal(read(rel), out, rel + ' is stale');
});

test('both pages have the same sections, in the same order', () => {
  const ids = (s) => all(/<section[^>]*\sid="([^"]+)"/g, s);
  assert.deepEqual(ids(html.en), SECTIONS);
  assert.deepEqual(ids(html.zh), ids(html.en));
  for (const lvl of [1, 2, 3, 4]) {
    const n = (s) => (body(s).match(new RegExp(`<h${lvl}[\\s>]`, 'g')) || []).length;
    assert.equal(n(html.zh), n(html.en), `h${lvl} count`);
  }
  for (const tag of ['li', 'tr', 'dt', 'pre', 'table', 'button']) {
    const n = (s) => (body(s).match(new RegExp(`<${tag}[\\s>]`, 'g')) || []).length;
    assert.equal(n(html.zh), n(html.en), `<${tag}> count`);
  }
  assert.equal((html.en.match(/<h1[\s>]/g) || []).length, 1, 'exactly one h1');
});

test('both pages link to the same places, one to one', () => {
  const links = (lang) => all(/<a\s[^>]*href="([^"]+)"/g, body(html[lang])).map((h) => normLink(h, PAGES[lang].url));
  assert.deepEqual(links('zh'), links('en'));
});

test('ids are unique and every #anchor has a target', () => {
  for (const [lang, s] of Object.entries(html)) {
    const ids = all(/\sid="([^"]+)"/g, s);
    assert.deepEqual(ids.filter((x, i) => ids.indexOf(x) !== i), [], `${lang}: duplicate ids`);
    for (const ref of all(/\shref="#([^"]*)"/g, s)) assert.ok(ids.includes(ref), `${lang}: #${ref} has no target`);
  }
});

test('language metadata: lang, canonical, hreflang, JSON-LD SoftwareApplication', () => {
  const want = { en: 'en', zh: 'zh-Hant-TW' };
  for (const [lang, p] of Object.entries(PAGES)) {
    const s = html[lang];
    assert.match(s, new RegExp(`<html lang="${want[lang]}">`));
    assert.ok(s.includes(`<link rel="canonical" href="${p.url}">`), `${lang} canonical`);
    for (const [hl, url] of [['en', PAGES.en.url], ['zh-Hant', PAGES.zh.url], ['x-default', PAGES.en.url]])
      assert.ok(s.includes(`<link rel="alternate" hreflang="${hl}" href="${url}">`), `${lang} hreflang ${hl}`);
    const ld = JSON.parse(s.match(/<script type="application\/ld\+json">([\s\S]*?)<\/script>/)[1]);
    assert.equal(ld['@type'], 'SoftwareApplication');
    assert.equal(ld.inLanguage, want[lang]);
    assert.equal(ld.url, p.url);
    assert.equal(ld.downloadUrl, REPO);
    assert.equal(ld.offers.price, '0');
  }
});

test('every local link and asset exists; nothing is root-absolute (the site must work under a subpath)', () => {
  for (const [lang, p] of Object.entries(PAGES)) {
    const s = html[lang];
    const dir = path.dirname(path.join(SITE_DIR, p.file));
    for (const ref of all(/\s(?:href|src)="([^"]+)"/g, s)) {
      if (/^(https?:|data:|mailto:|#)/.test(ref)) continue;
      assert.ok(!ref.startsWith('/'), `${lang}: ${ref} is root-absolute`);
      let f = path.join(dir, ref.split(/[?#]/)[0]);
      if (ref.split(/[?#]/)[0].endsWith('/')) f = path.join(f, 'index.html');
      assert.ok(fs.existsSync(f), `${lang}: ${ref} → ${path.relative(SITE_DIR, f)} missing`);
    }
    assert.ok(!body(s).includes(`href="${SITE}`), `${lang}: internal links must be relative`);
  }
  assert.ok(fs.existsSync(path.join(SITE_DIR, 'assets/og.png')), 'assets/og.png (social card) missing');
  const css = read('assets/site.css');
  for (const u of all(/url\(([^)]+)\)/g, css)) assert.ok(fs.existsSync(path.join(SITE_DIR, 'assets', u)), `css: ${u} missing`);
});

test('the demo is readable without JavaScript: its first sentence and pipeline are in the HTML', () => {
  for (const s of Object.values(html)) {
    const line = s.match(/<p class="line is-new">([\s\S]*?)<\/p>/)[1].replace(/<[^>]+>/g, '');
    assert.equal(line, UTTERANCES[0].text);
    for (const seg of segments(UTTERANCES[0]).filter((x) => x.was)) assert.ok(s.includes(`data-was="${seg.was}"`));
    assert.ok(s.includes('&quot;punct&quot;: &quot;smart_zh&quot;') || s.includes('"punct": "smart_zh"'));
  }
});

test('feature statuses match the README table, row for row', () => {
  const readme = fs.readFileSync(path.join(ROOT, 'README.md'), 'utf8');
  const table = readme.split('## 功能狀態')[1].split('\n## ')[0];
  const rows = table.split('\n').filter((l) => /^\|/.test(l) && !/^\|\s*-/.test(l)).slice(1);
  const status = rows.map((r) => r.split('|')[2].trim())
    .map((s) => s.startsWith('Public seed') ? 'seed' : s.startsWith('MVP') ? 'mvp' : s.startsWith('Planned') ? 'planned' : s);
  assert.deepEqual(FEATURES, status);
  assert.equal(en.status.features.length, status.length);
});

test('llms.txt points at both languages and the contract', () => {
  const s = read('llms.txt');
  assert.match(s, /^# Open Dictate\n\n> /);
  assert.ok(s.includes(SITE) && s.includes(SITE + 'zh/'));
  assert.ok(s.includes('IO-CONTRACT.md'));
  assert.ok(read('robots.txt').includes(SITE + 'sitemap.xml'));
});
