// open-dictate website: the demo desk, copy buttons, the language switch.
// No dependencies. Every word is already in the HTML; this file only animates.
//
// The demo is a script, not recognition: the sentences, raw text and glossary
// pairs come from site/src/page.mjs (embedded as #od-demo). The only real input
// is how long you hold the key, which decides between "dropped as too short"
// and "transcribed". The page never asks for the microphone.
(() => {
  'use strict';
  const $ = (s, r = document) => r.querySelector(s);
  const $$ = (s, r = document) => [...r.querySelectorAll(s)];
  let data = {};
  try { data = JSON.parse(($('#od-demo') || {}).textContent || '{}'); } catch (e) { data = {}; }
  const S = data.s || {};

  wireCopyButtons();
  wireLanguageSwitch();

  const desk = $('#desk');
  const U = data.u;
  if (!desk || !Array.isArray(U) || !U.length) return;

  const reduced = matchMedia('(prefers-reduced-motion: reduce)').matches;
  const doc = $('#doc'), note = $('#doc-note'), key = $('#key'), timer = $('#timer');
  const statusEl = $('#desk-status'), statusText = $('#status-text'), wireEl = $('#wire code');
  const steps = $$('#pipe .step'), vals = steps.map((li) => $('.step-v', li));
  let caret = $('.caret', doc);
  const STOP = Symbol('stop');
  let token = 0;          // bumping it cancels whatever sequence is running
  let idx = 0;            // the utterance on screen (the static HTML shows 0, done)
  let holding = false, t0 = 0;

  // ---------- small helpers ----------
  const esc = (s) => String(s).replace(/&/g, '&amp;').replace(/</g, '&lt;').replace(/>/g, '&gt;');
  const pair = (x) => { const [a, b] = String(x).split(' → '); return b === undefined ? esc(x) : `<s>${esc(a)}</s> → <b>${esc(b)}</b>`; };
  const valHTML = (v) => Array.isArray(v) ? v.map((x) => `<span>${pair(x)}</span>`).join('') : esc(v);
  const scale = reduced ? 0.15 : 1;
  const wait = (ms, my) => new Promise((res, rej) => setTimeout(() => (my === token ? res() : rej(STOP)), ms * scale));
  const quiet = (p) => p.catch((e) => { if (e !== STOP) throw e; });
  const sec = (d) => d.toFixed(1) + ' s';

  function setMode(m) { desk.dataset.mode = m; }
  function setStatus(s) { statusText.textContent = s; }
  function setStep(i, st, v) { steps[i].dataset.st = st; if (v !== undefined) vals[i].innerHTML = valHTML(v); }
  function resetSteps() { steps.forEach((_, i) => setStep(i, 'idle', '—')); }
  function setWire(s) { wireEl.textContent = s; }
  function setLive(u, dur) {
    for (const el of $$('[data-live="rec"]')) el.textContent = sec(dur);
    for (const el of $$('[data-live="changes"]')) el.textContent = String(u.segs.filter((s) => s.was).length);
  }

  // ---------- the waveform: drawn only while "recording" ----------
  const wave = (() => {
    const c = $('#wave');
    const ctx = c && c.getContext ? c.getContext('2d') : null;
    const css = getComputedStyle(document.documentElement);
    const ON = css.getPropertyValue('--sig').trim() || '#2ee6c6';
    const OFF = css.getPropertyValue('--fg-3').trim() || '#8a8882';
    let bars = [], live = false, raf = 0, last = 0, ph = 0, n = 0;
    // speech-shaped, not speech: syllables near 4 Hz under a slower phrase envelope
    const level = (t) => {
      const syl = Math.max(0, 0.6 * Math.sin(t * 27) + 0.4 * Math.sin(t * 10.7 + 1));
      const phrase = 0.55 + 0.45 * Math.sin(t * 2.8);
      return Math.min(1, (0.08 + syl * phrase) * (0.75 + Math.random() * 0.5));
    };
    function draw() {
      if (!ctx) return;
      ctx.clearRect(0, 0, c.width, c.height);
      const step = c.width / n, bw = Math.max(1, Math.round(step * 0.45));
      for (let i = 0; i < n; i++) {
        const bh = Math.max(1, bars[i] * c.height * 0.92);
        ctx.globalAlpha = live ? 0.3 + 0.7 * (i / n) : 0.55;
        ctx.fillStyle = live ? ON : OFF;
        ctx.fillRect(Math.round(i * step), Math.round((c.height - bh) / 2), bw, Math.round(bh));
      }
      ctx.globalAlpha = 1;
    }
    function size() {
      if (!ctx) return;
      const dpr = Math.min(2, window.devicePixelRatio || 1);
      c.width = Math.max(1, Math.round(c.clientWidth * dpr));
      c.height = Math.max(1, Math.round(c.clientHeight * dpr));
      const m = Math.max(12, Math.floor(c.clientWidth / 5));
      if (!bars.length) { bars = new Array(m).fill(0); fill(U[0].dur || 3); }
      while (bars.length < m) bars.unshift(0);
      bars = bars.slice(-m); n = m;
      draw();
    }
    function fill(seconds) {   // a frozen trace, as if a recording just ended
      const k = Math.min(bars.length, Math.round(seconds / 0.045));
      for (let i = 0; i < bars.length; i++) bars[i] = i >= bars.length - k ? level(i * 0.045) : 0;
    }
    function tick(now) {
      if (!live) return;
      if (now - last > 45) { last = now; ph += 0.045; bars.push(level(ph)); bars.shift(); draw(); }
      raf = requestAnimationFrame(tick);
    }
    return {
      size,
      start() { if (reduced) { fill(2); live = true; draw(); return; } live = true; last = 0; raf = requestAnimationFrame(tick); },
      stop(seconds) { live = false; cancelAnimationFrame(raf); if (reduced && seconds !== undefined) fill(seconds); draw(); },
    };
  })();
  wave.size();
  let resizeT = 0;
  addEventListener('resize', () => { clearTimeout(resizeT); resizeT = setTimeout(wave.size, 120); });

  // ---------- one dictation ----------
  function press() { key.classList.add('down'); setMode('rec'); setStatus(S.rec); resetSteps(); setStep(0, 'live'); wave.start(); }
  function release(dur) { key.classList.remove('down'); wave.stop(dur); timer.textContent = sec(dur); }

  function recFor(seconds, my) {
    const start = performance.now();
    return new Promise((res, rej) => {
      const f = (now) => {
        if (my !== token) return rej(STOP);
        const e = Math.min(seconds, (now - start) / 1000);
        timer.textContent = sec(e);
        if (e >= seconds) res(); else requestAnimationFrame(f);
      };
      requestAnimationFrame(f);
    });
  }

  async function tooShort(dur, my) {
    setMode('short');
    setStep(0, 'skip', `${sec(dur)} < 0.5 s`);
    for (let i = 1; i < 5; i++) setStep(i, 'idle', i === 4 ? U[2].rows[4] : '—');
    setWire(S.nothingSent);
    note.innerHTML = U[2].note;
    setStatus(S.short);
    await wait(10, my);
  }

  async function typeLine(u, my) {
    for (const l of $$('.line.is-new', doc)) l.classList.remove('is-new');
    const p = document.createElement('p');
    p.className = 'line is-new';
    if (caret) caret.remove(); else { caret = document.createElement('span'); caret.className = 'caret'; caret.setAttribute('aria-hidden', 'true'); }
    p.appendChild(caret);
    doc.appendChild(p);
    desk.dataset.typing = '';
    try {
      for (const s of u.segs) {
        let box = null;
        if (s.was || s.heard) {
          box = document.createElement('span');
          box.className = s.was ? 'fix' : 'heard';
          if (s.was) box.dataset.was = s.was;
          p.insertBefore(box, caret);
        }
        for (const ch of s.t) {
          if (box) box.textContent += ch;
          else if ('，。？！：；'.includes(ch)) { const pm = document.createElement('span'); pm.className = 'pm'; pm.textContent = ch; p.insertBefore(pm, caret); }
          else p.insertBefore(document.createTextNode(ch), caret);
          doc.scrollTop = doc.scrollHeight;
          if (!reduced) await wait(30, my);
        }
      }
    } finally { delete desk.dataset.typing; p.normalize(); }
  }

  async function clearDoc(my) {
    for (const l of $$('.line', doc)) l.remove();
    doc.scrollTop = 0;
    await wait(300, my);
  }

  async function transcribe(i, dur, my) {
    const u = U[i];
    if (u.ignored) return tooShort(dur, my);
    setMode('work');
    setStep(0, 'done', `${sec(dur)} · 16 kHz mono`);
    setStep(1, 'live', S.transcribing);
    setWire(u.wire.split('\n')[0]);
    await wait(620, my);
    setStep(1, 'done', u.rows[1]);
    setStep(2, 'live', '…');
    await wait(460, my);
    setStep(2, 'done', u.rows[2]);
    setStep(3, 'live', '…');
    await wait(340, my);
    setStep(3, 'done', u.rows[3]);
    setStep(4, 'live', '…');
    setWire(u.wire);
    await typeLine(u, my);
    setStep(4, 'done', u.rows[4]);
    note.innerHTML = u.note;
    setLive(u, dur);
    setMode('idle');
    if (u.teach) {
      await wait(1500, my);
      setMode('teach');
      setStatus(S.teach);
      vals[2].innerHTML = valHTML(u.rows[2]) + `<span class="taught">${pair(u.teach[0] + ' → ' + u.teach[1])} · ${esc(S.taught)}</span>`;
      setWire(u.teachWire);
    }
  }

  const nextSpoken = (i) => { let j = i; do { j = (j + 1) % U.length; } while (U[j].ignored); return j; };

  // ---------- autoplay: the demo dictates until someone takes over ----------
  let visible = true, wake = null;
  if ('IntersectionObserver' in window) {
    new IntersectionObserver((es) => {
      visible = es[0].isIntersecting;
      if (visible && wake) { wake(); wake = null; }
    }).observe(desk);
  }
  const whenVisible = () => visible ? Promise.resolve() : new Promise((r) => { wake = r; });

  async function autoplay(my) {
    await wait(2600, my);            // let the first frame be read
    for (;;) {
      await whenVisible();
      if (my !== token) throw STOP;
      idx = (idx + 1) % U.length;
      if (idx === 0) await clearDoc(my);
      const u = U[idx];
      setMode('idle'); setStatus(S.auto);
      await wait(900, my);
      press();
      setStatus(S.auto);
      await recFor(u.dur, my);
      release(u.dur);
      await transcribe(idx, u.dur, my);
      await wait(u.teach ? 3000 : 2600, my);
    }
  }

  // ---------- your turn: hold the key ----------
  function down(e) {
    if (e.type === 'keydown') {
      if (e.key !== ' ' && e.key !== 'Enter') return;
      e.preventDefault();
      if (e.repeat || holding) return;
    } else {
      if (e.button !== 0 || holding) return;
      e.preventDefault();
      if (key.setPointerCapture && e.pointerId !== undefined) { try { key.setPointerCapture(e.pointerId); } catch (_) { /* fine */ } }
      key.focus({ preventScroll: true });
    }
    token++;
    statusEl.setAttribute('aria-live', 'polite');
    holding = true;
    t0 = performance.now();
    press();
    const my = token;
    const f = () => { if (!holding || my !== token) return; timer.textContent = sec((performance.now() - t0) / 1000); requestAnimationFrame(f); };
    requestAnimationFrame(f);
  }
  function up(e) {
    if (!holding) return;
    if (e.type === 'keyup' && e.key !== ' ' && e.key !== 'Enter') return;
    holding = false;
    const dur = (performance.now() - t0) / 1000;
    release(dur);
    const my = ++token;
    if (dur < 0.5) { quiet(tooShort(dur, my)); return; }
    const j = nextSpoken(idx);
    quiet((async () => {
      if (j <= idx || j === 0) await clearDoc(my);
      idx = j;
      setStatus(S.you);
      await transcribe(j, dur, my);
      if (!U[j].teach) setStatus(S.idle);
    })());
  }
  key.addEventListener('pointerdown', down);
  key.addEventListener('pointerup', up);
  key.addEventListener('pointercancel', up);
  key.addEventListener('keydown', down);
  key.addEventListener('keyup', up);
  key.addEventListener('blur', up);
  key.addEventListener('contextmenu', (e) => e.preventDefault());

  if (reduced) setStatus(S.idle);
  else { setStatus(S.auto); quiet(autoplay(++token)); }
})();

// ---------- page utilities ----------
function wireCopyButtons() {
  let T = {};
  try { T = JSON.parse(document.getElementById('od-demo').textContent).s || {}; } catch (e) { T = {}; }
  for (const b of document.querySelectorAll('button.copy[data-copy]')) {
    b.addEventListener('click', async () => {
      const el = document.getElementById(b.dataset.copy); if (!el) return;
      const text = el.innerText.replace(/\n$/, '');
      try { await navigator.clipboard.writeText(text); }
      catch (e) {
        const r = document.createRange(); r.selectNodeContents(el);
        const s = getSelection(); s.removeAllRanges(); s.addRange(r);
        try { document.execCommand('copy'); } catch (e2) { /* the selection stays for a manual copy */ }
      }
      b.textContent = T.copied || 'Copied'; b.dataset.done = '';
      setTimeout(() => { b.textContent = T.copy || 'Copy'; delete b.dataset.done; }, 1600);
    });
  }
}

// switching language keeps your place: the same section ids exist on both pages
function wireLanguageSwitch() {
  const current = () => {
    if (scrollY < 120) return '';
    let id = '';
    for (const s of document.querySelectorAll('main > section[id]')) if (s.getBoundingClientRect().top <= innerHeight * 0.35) id = s.id;
    return id;
  };
  for (const a of document.querySelectorAll('.langs a[data-lang]')) {
    a.addEventListener('click', () => {
      try { localStorage.setItem('od.lang', a.dataset.lang); } catch (e) { /* private mode */ }
      const id = current();
      a.setAttribute('href', a.getAttribute('href').split('#')[0] + (id ? '#' + id : ''));
    });
  }
}
