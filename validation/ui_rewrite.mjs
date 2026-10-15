const S = process.argv[2], APP = process.argv[3] || 'http://127.0.0.1:8502';
const fs = await import('fs');
const tab = await (await fetch('http://127.0.0.1:9222/json/new?' + APP, { method: 'PUT' })).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl); let id = 0; const pending = {};
ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending[m.id]) pending[m.id](m); };
const send = (method, params = {}) => new Promise(r => { pending[++id] = r; ws.send(JSON.stringify({ id, method, params })); });
await new Promise(r => ws.onopen = r);
const ev = async x => (await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true })).result?.result?.value;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = []; const log = (n, p, d) => { results.push({ n, p, d }); console.log(`${p ? 'PASS' : 'FAIL'}  ${n}  ${d ?? ''}`); };
const VIS = `[...document.querySelectorAll('[role=tabpanel]')].find(p => p.offsetParent && !p.hidden)`;
const idle = async (t = 600000) => { const t0 = Date.now(); await sleep(1200); while (Date.now() - t0 < t) { const busy = await ev(`!!document.querySelector('[data-testid="stStatusWidget"]') || [...document.querySelectorAll('[data-testid="stExpander"] summary, [data-testid="stStatus"]')].some(e => /Editing|Loading|chunk|Analysing/.test(e.innerText))`); if (!busy) return; await sleep(800); } throw new Error('timeout'); };
const setText = async txt => { await ev(`(()=>{const ta=(${VIS}).querySelector('textarea'); ta.focus(); ta.select(); return 1})()`); await send('Input.insertText', { text: txt }); await ev(`(${VIS}).querySelector('textarea').blur()`); await idle(30000); await sleep(800); };
const btn = l => ev(`(()=>{const b=[...(${VIS}).querySelectorAll('button')].find(b=>b.innerText.trim()===${JSON.stringify(l)}); return b ? (b.disabled ? 'disabled' : 'enabled') : 'missing'})()`);
const click = async l => { await ev(`[...(${VIS}).querySelectorAll('button')].find(b=>b.innerText.trim()===${JSON.stringify(l)}).click()`); await idle(); };
const mode = async label => { await ev(`[...(${VIS}).querySelectorAll('label')].find(l=>l.innerText.trim()===${JSON.stringify(label)}).click()`); await idle(30000); await sleep(800); };
const panel = () => ev(`(${VIS}).innerText`);
const exc = () => ev(`document.querySelectorAll('[data-testid="stException"]').length`);
const output = () => ev(`(()=>{const c=(${VIS}).querySelector('[data-testid="stCode"] code, [data-testid="stCode"] pre'); return c ? c.innerText : null})()`);
const table = () => ev(`(()=>{const t=(${VIS}).querySelector('table.rw'); if(!t) return null; return {rows: t.querySelectorAll('tr').length-1, del: t.querySelectorAll('del.rw-del').length, ins: t.querySelectorAll('ins.rw-ins').length, statuses: [...t.querySelectorAll('.rw-status')].map(s=>s.innerText)}})()`);
const norm = s => (s || '').replace(/\s+/g, ' ').trim();

await sleep(5000); await idle(60000);
await ev(`[...document.querySelectorAll('[role=tab]')].find(t=>t.innerText.trim()==='Rewrite').click()`); await sleep(1000);
let txt = await panel();
log('Rewrite tab present with review notice', /AI-assisted editing — review every change/.test(txt));
log('notice disclaims detector results / GPTZero, no pass promise', /makes no claim about GPTZero/.test(txt) && !/(will pass|undetectable|bypass|evade)/i.test(txt));
log('three styles offered', ['Light proofreading', 'Plain English', 'Natural academic'].every(s => txt.includes(s)));
log('empty input: Rewrite disabled', (await btn('Rewrite')) === 'disabled');

// sample, light
const sample = fs.readFileSync(`${S}/rw_sample.txt`, 'utf8');
await setText(sample); let t0 = Date.now(); await click('Rewrite'); const tLight = (Date.now() - t0) / 1000;
let tb = await table(), out = await output(); txt = await panel();
log('light: side-by-side table, one row per chunk', tb && tb.rows === 2, JSON.stringify(tb));
log('light: changes highlighted (struck-through + inserted words)', tb && tb.del > 0 && tb.ins > 0);
log('light: facts, numbers and citations preserved in output', ['$64.3 billion', '5.6%', '(Woolworths Group, 2023)', 'Porter (1985)', '13.1'].every(s => (out || '').includes(s)), `${tLight.toFixed(0)}s`);
log('light: coverage line says nothing truncated', /all 2 paragraph\(s\).*Nothing was truncated/s.test(txt));
log('output labelled AI-assisted, review before use', /Rewritten text — AI-assisted, review before use/.test(txt));
log('no exception after light run', (await exc()) === 0);

// copy button (intercepted, real clipboard untouched)
await ev(`(()=>{window.__copied=[]; const orig=navigator.clipboard&&navigator.clipboard.writeText; if(navigator.clipboard){navigator.clipboard.writeText=t=>{window.__copied.push(t); return Promise.resolve();}} const ex=document.execCommand.bind(document); document.execCommand=(c,...a)=>{ if(c==='copy'){window.__copied.push(String(window.getSelection()));} return true; }; return 1})()`);
const copyInfo = await ev(`(()=>{const b=(${VIS}).querySelector('[data-testid="stCode"] button, [data-testid="stCodeCopyButton"]'); if(!b) return 'missing'; b.click(); return b.getAttribute('title') || b.getAttribute('aria-label') || b.dataset.testid})()`);
await sleep(800); const copied = await ev('window.__copied');
log('copy button present and copies the rewritten text', copyInfo !== 'missing' && copied && copied.length > 0 && norm(copied[copied.length - 1]) === norm(out), `button: ${copyInfo}; captured ${copied ? copied.length : 0} copy event(s)`);

// download keeps results on screen
log('download button present', (await btn('Download as .txt')) === 'enabled');
await ev(`[...(${VIS}).querySelectorAll('button')].find(b=>b.innerText.trim()==='Download as .txt').click()`); await sleep(2500);
log('results still shown after download click', (await table()) !== null);

// stale results hidden when mode changes
await mode('Plain English');
log('switching style hides results from the other style', (await table()) === null);

// safety path: spelled-out numbers in plain mode
await setText(fs.readFileSync(`${S}/rw_words.txt`, 'utf8')); await click('Rewrite');
tb = await table(); out = await output(); txt = await panel();
log('plain: number change -> original kept, warning shown', tb && tb.statuses.some(s => /Kept your original/.test(s)) && /keep your original wording because a check failed/.test(txt), JSON.stringify(tb && tb.statuses));
log('plain: output equals the original sentence', norm(out) === norm(fs.readFileSync(`${S}/rw_words.txt`, 'utf8')));
log('plain: failed check explained in notes', /Number\(s\) added by the suggestion: 10, 25, 3/.test(txt));

// academic mode on the same text
await mode('Natural academic'); await click('Rewrite'); tb = await table();
log('academic: runs and shows status', tb && tb.rows === 1, JSON.stringify(tb && tb.statuses));

// long input, light
await mode('Light proofreading');
const long = fs.readFileSync(`${S}/rw_long_clean.txt`, 'utf8');
await setText(long); t0 = Date.now(); await click('Rewrite'); const tLong = (Date.now() - t0) / 1000;
tb = await table(); out = await output(); txt = await panel();
const cov = (txt.match(/What was processed:[^\n]*/) || [''])[0];
const outParas = (out || '').split(/\n\s*\n/).filter(p => p.trim()).length;
const ratio = (out || '').split(/\s+/).length / long.split(/\s+/).length;
log('long: all paragraphs processed, big paragraph split into several chunks', /all 37 paragraph\(s\)/.test(cov) && tb && tb.rows > 37, `${cov.slice(0, 170)} | rows=${tb && tb.rows}`);
log('long: output keeps 37 paragraphs and ~all words (no truncation)', outParas === 37 && ratio > 0.9, `paragraphs=${outParas}, words ratio=${ratio.toFixed(2)}, ${tLong.toFixed(0)}s`);
log('long: statuses', true, JSON.stringify(Object.entries((tb?.statuses || []).reduce((a, s) => (a[s] = (a[s] || 0) + 1, a), {}))));
log('no Streamlit exception at end', (await exc()) === 0);
await send('Target.closeTarget', { targetId: tab.id }).catch(() => {});
fs.writeFileSync(`${S}/ui_rewrite_results.json`, JSON.stringify(results, null, 1));
console.log(`\n${results.filter(r => r.p).length}/${results.length} passed`); process.exit(0);
