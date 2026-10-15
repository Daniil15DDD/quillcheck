import { execSync } from 'child_process';
const S = process.argv[2], APP = 'http://127.0.0.1:8502', PID = process.argv[3];
const fs = await import('fs');
const tab = await (await fetch('http://127.0.0.1:9222/json/new?' + APP, { method: 'PUT' })).json();
const ws = new WebSocket(tab.webSocketDebuggerUrl);
let id = 0; const pending = {};
ws.onmessage = e => { const m = JSON.parse(e.data); if (m.id && pending[m.id]) pending[m.id](m); };
const send = (method, params = {}) => new Promise(r => { pending[++id] = r; ws.send(JSON.stringify({ id, method, params })); });
await new Promise(r => ws.onopen = r);
const ev = async x => (await send('Runtime.evaluate', { expression: x, returnByValue: true, awaitPromise: true })).result?.result?.value;
const sleep = ms => new Promise(r => setTimeout(r, ms));
const results = [];
const log = (name, pass, detail) => { results.push({ name, pass, detail }); console.log(`${pass ? 'PASS' : 'FAIL'}  ${name}  ${detail ?? ''}`); };
const VIS = `[...document.querySelectorAll('[role=tabpanel]')].find(p => p.offsetParent && !p.hidden)`;
const panelText = () => ev(`(${VIS})?.innerText || ''`);
const footprint = () => { const o = execSync(`footprint -p ${PID}`).toString(); return { now: o.match(/phys_footprint: ([\d.]+ [KMG]B)/)?.[1], peak: o.match(/phys_footprint_peak: ([\d.]+ [KMG]B)/)?.[1] }; };
const idle = async (t = 300000) => { const t0 = Date.now(); await sleep(1200); while (Date.now() - t0 < t) { const busy = await ev(`!!document.querySelector('[data-testid="stStatusWidget"]') || [...document.querySelectorAll('[data-testid="stExpander"] summary, [data-testid="stStatus"]')].some(e => /Analysing|Loading|Scoring/.test(e.innerText))`); if (!busy) return true; await sleep(700); } throw new Error('timeout waiting for idle'); };
const setText = async txt => {
  await ev(`(()=>{const ta=(${VIS}).querySelector('textarea'); ta.focus(); ta.select(); return 1})()`);
  if (txt === '') { await send('Input.dispatchKeyEvent', { type: 'keyDown', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 }); await send('Input.dispatchKeyEvent', { type: 'keyUp', key: 'Backspace', code: 'Backspace', windowsVirtualKeyCode: 8 }); }
  else await send('Input.insertText', { text: txt });
  await ev(`(()=>{(${VIS}).querySelector('textarea').blur(); return 1})()`); await idle(30000); await sleep(800);
};
const btn = label => ev(`(()=>{const b=[...(${VIS}).querySelectorAll('button')].find(b=>b.innerText.trim()===${JSON.stringify(label)}); return b ? (b.disabled ? 'disabled' : 'enabled') : 'missing'})()`);
const exc = () => ev(`[...document.querySelectorAll('[data-testid="stException"]')].map(e=>e.innerText.slice(0,200))`);
const calib = () => { try { return JSON.parse(fs.readFileSync(new URL("../calibration.json", import.meta.url), 'utf8')).samples; } catch { return null; } };
const click = async label => { const st = await btn(label); if (st !== 'enabled') console.log(`   (button "${label}" is ${st})`); await ev(`(()=>{[...(${VIS}).querySelectorAll('button')].find(b=>b.innerText.trim()===${JSON.stringify(label)}).click(); return 1})()`); await idle(); };
const metrics = () => ev(`[...(${VIS}).querySelectorAll('[data-testid="stMetricValue"]')].map(m=>m.innerText.trim())`);
const alerts = () => ev(`[...(${VIS}).querySelectorAll('[data-testid="stAlert"], [data-testid="stException"]')].map(a=>a.innerText.trim().slice(0,220))`);
const gotoTab = async name => { await ev(`(()=>{[...document.querySelectorAll('[role=tab]')].find(t=>t.innerText.trim()===${JSON.stringify(name)}).click(); return 1})()`); await sleep(800); };
const upload = async path => { const { root } = (await send('DOM.getDocument', { depth: -1, pierce: true })).result; const ids = (await send('DOM.querySelectorAll', { nodeId: root.nodeId, selector: 'input[type=file]' })).result.nodeIds; await send('DOM.setFileInputFiles', { nodeId: ids[0], files: [path] }); await idle(60000); };

await send('Page.enable'); await send('DOM.enable'); await sleep(4000); await idle(60000);
log('page loads, disclaimer shown', /not evidence of who wrote/.test(await ev('document.body.innerText')));

// 1 empty + whitespace
log('empty input: Analyse disabled', (await btn('Analyse')) === 'disabled');
await setText('   \n\n   '); log('whitespace-only input: Analyse disabled', (await btn('Analyse')) === 'disabled');

// 2 short
await setText(fs.readFileSync(`${S}/ui_short.txt`, 'utf8')); await click('Analyse');
let a = await alerts(), m = await metrics();
log('short input: short-text warning', a.some(x => /Only \d+ words/.test(x)), JSON.stringify(m));
log('short input: uncalibrated note shown', a.some(x => /Uncalibrated/.test(x)));
log('short input: experimental label', /EXPERIMENTAL/.test(await panelText()));

// 3 long
await setText(fs.readFileSync(`${S}/ui_long.txt`, 'utf8')); let f0 = footprint(); await click('Analyse');
let txt = await panelText(); m = await metrics(); log('long input: no exception', (await exc()).length === 0, (await exc()).join(' | '));
log('long input: both metrics shown', m.length === 2, JSON.stringify(m));
const cov = txt.match(/What was scored:[^\n]*/)?.[0] || '';
log('long input: coverage line, multiple windows, no truncation', /in [2-9] window/.test(cov) && /No text was truncated/.test(cov), cov.slice(0, 260));
log('long input: no short-text warning', !(await alerts()).some(x => /Only \d+ words/.test(x)));
const marks = await ev(`(()=>{const ms=[...(${VIS}).querySelectorAll('mark')]; return {n: ms.length, title: ms[0]?.title}})()`);
log('highlights labelled relative (tooltip rank, "Not a probability")', marks.n > 0 && /Relative rank .* Not a probability/.test(marks.title), `${marks.n} marked; tooltip: ${marks.title}`);
log('highlight section heading says relative', /Sentences — relative highlights/.test(txt));
const rows = await ev(`(${VIS}).querySelector('[data-testid="stDataFrame"]') ? 'present' : 'missing'`);
log('paragraph table rendered', rows === 'present');
let f1 = footprint(); console.log('   footprint before/after first long run:', f0, f1);

// 4 repeated checks
const first = JSON.stringify(m); const fps = [];
for (let i = 0; i < 5; i++) { await click('Analyse'); fps.push(footprint()); }
const again = JSON.stringify(await metrics());
log('repeated checks (5x): identical scores', again === first, `${first} vs ${again}`);
console.log('   footprint after each repeat:', JSON.stringify(fps));

// 5 calibration add + effect + delete
await gotoTab('Calibrate');
for (const s of JSON.parse(fs.readFileSync(`${S}/ui_calib.json`, 'utf8'))) { await setText(s); await click('Score and add'); }
const calRows = calib() || [];
log('calibration: 3 samples stored', calRows.length === 3, JSON.stringify(calRows.map(r => ({ l: r.label, b: r.binoculars && +r.binoculars.toFixed(3), c: r.classifier && +r.classifier.toFixed(3), w: r.words }))));
const raw = fs.readFileSync(new URL("../calibration.json", import.meta.url), 'utf8');
log('calibration: no text stored in file', calRows.length > 0 && !JSON.parse(fs.readFileSync(`${S}/ui_calib.json`, 'utf8')).some(s => raw.includes(s.slice(0, 40))) && Object.keys(calRows[0] || {}).join() === 'label,binoculars,classifier,words,added');
await gotoTab('Check'); await click('Analyse');
a = await alerts();
log('check after calibration: personal comparison shown', a.some(x => /of your 3 own writing samples/.test(x)), a.find(x => /own writing/.test(x)));
await gotoTab('Calibrate'); await click('Delete all calibration samples');
log('delete: calibration.json emptied', (calib() || ['missing']).length === 0);
await gotoTab('Check'); await click('Analyse');
log('check after delete: back to uncalibrated', (await alerts()).some(x => /Uncalibrated/.test(x)));

// 6 error handling: valid docx, corrupt docx
await send('Page.reload'); await sleep(4000); await idle(60000);
await upload(`${S}/ui_valid.docx`); txt = await panelText();
log('valid .docx: words read + skipped-content note', /Using \d+ words from \*?\*?ui_valid.docx|Using \d+ words from ui_valid.docx/.test(txt) && /tables, text boxes/.test(txt), (txt.match(/Using \d+ words[^\n]*/) || [''])[0].slice(0, 120));
await click('Analyse'); log('valid .docx: analysis runs, table text excluded', /What was scored/.test(await panelText()) && !/TABLE CELL TEXT/.test(await panelText()));
await send('Page.reload'); await sleep(4000); await idle(60000);
await upload(`${S}/ui_corrupt.docx`); a = await alerts();
log('corrupt .docx: friendly error, no crash', a.some(x => /Could not read ui_corrupt.docx/.test(x)) && !(await ev(`!!document.querySelector('[data-testid="stException"]')`)), a[0]);
log('corrupt .docx: Analyse disabled', (await btn('Analyse')) === 'disabled');

const ex = await exc(); log('no Streamlit exception on page at end', ex.length === 0, ex.join(' | '));
await send('Target.closeTarget', { targetId: tab.id }).catch(() => {});
fs.writeFileSync(`${S}/ui_results.json`, JSON.stringify(results, null, 1));
console.log(`\n${results.filter(r => r.pass).length}/${results.length} passed`);
process.exit(0);
