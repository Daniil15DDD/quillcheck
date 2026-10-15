/* Word task pane: scan the document with the local checker, mark the most AI-like
   sentences (relative to this text) and offer local rewrite suggestions to accept or skip.
   Marks are hidden content controls tagged "aitc" plus a yellow highlight; "Clear marks"
   removes both and leaves the text alone. */
"use strict";

const TAG = "aitc";
const SEARCH_LIMIT = 255; // Word's maximum search string length
const POLL_MS = 3000;
const QUIET_MS = 5000; // auto-scan once the text has not changed for this long

const $ = (id) => document.getElementById(id);
let busy = false;
let inWord = false;
let cards = []; // {ccId, text, el, slot, done}
let lastSeen = "", lastScanned = "", changedAt = 0;

const STATUS_LABELS = {
  edited: "Suggested edit",
  review: "Suggested edit (read the notes)",
  "kept-original": "A check failed, so your original is kept. Suggestion shown for reference only",
  unchanged: "No changes suggested",
};

Office.onReady((info) => {
  inWord = info.host === Office.HostType.Word;
  if (!inWord) setStatus("Open this panel inside Word to use it.", true);
  $("scan").onclick = () => scan();
  $("clear").onclick = () => run(async (ctx) => { await clearMarks(ctx); resetCards(); setStatus("Marks cleared."); });
  $("suggestAll").onclick = () => suggest(cards.filter((c) => !c.done));
  setInterval(autoScanTick, POLL_MS);
});

function setStatus(msg, isError = false) {
  $("status").textContent = msg;
  $("status").className = "status" + (isError ? " error" : "");
}

function setBusy(on) {
  busy = on;
  for (const b of document.querySelectorAll("button")) b.disabled = on;
}

async function api(path, body) {
  let res;
  try {
    res = await fetch(path, { method: "POST", headers: { "Content-Type": "application/json" }, body: JSON.stringify(body) });
  } catch {
    throw new Error("Can't reach the local checker. Is addin/run_addin.sh running?");
  }
  const data = await res.json().catch(() => ({}));
  if (!res.ok) throw new Error(data.error || `Local server error ${res.status}`);
  return data;
}

async function run(fn) {
  if (busy || !inWord) return;
  setBusy(true);
  try {
    await Word.run(fn);
  } catch (e) {
    setStatus(e.message || String(e), true);
  } finally {
    setBusy(false);
  }
}

const clean = (t) => t.replace(/[\u000b\r\n\t]+/g, " ").replace(/ {2,}/g, " ").trim();
const escapeSearch = (t) => t.replace(/\^/g, "^^");

async function clearMarks(ctx) {
  const ccs = ctx.document.contentControls.getByTag(TAG);
  ccs.load("items");
  await ctx.sync();
  for (const cc of ccs.items) {
    cc.font.highlightColor = null;
    cc.delete(true); // keep the text, remove only the mark
  }
  await ctx.sync();
}

function resetCards() {
  cards = [];
  $("cards").replaceChildren();
  $("flagged").hidden = true;
}

function scan() {
  return run(async (ctx) => {
    const selectionOnly = $("selectionOnly").checked;
    const scope = selectionOnly ? ctx.document.getSelection() : ctx.document.body;
    const paras = scope.paragraphs;
    paras.load("items/text");
    const body = ctx.document.body;
    body.load("text");
    await ctx.sync();

    const kept = [];
    paras.items.forEach((p, i) => { const t = clean(p.text); if (t) kept.push({ i, t }); });
    if (!kept.length) throw new Error(selectionOnly ? "Select some text first." : "The document is empty.");

    await clearMarks(ctx);
    resetCards();
    $("summary").replaceChildren();
    lastScanned = lastSeen = body.text;
    const words = kept.reduce((n, k) => n + k.t.split(/\s+/).length, 0);
    setStatus(`Scanning ${words} words locally… (about 20–30 seconds)`);
    const result = await api("/api/scan", { text: kept.map((k) => k.t).join("\n\n") });
    showSummary(result);

    // Find each flagged sentence inside its own paragraph.
    const flagged = result.sentences.filter((s) => s.flagged);
    const opts = { matchCase: true };
    const found = flagged.map((s) => {
      const para = paras.items[kept[s.paragraph].i];
      if (s.text.length <= SEARCH_LIMIT) {
        const r = para.search(escapeSearch(s.text), opts);
        r.load("items");
        return { s, whole: r };
      }
      const head = para.search(escapeSearch(s.text.slice(0, 200).trim()), opts);
      const tail = para.search(escapeSearch(s.text.slice(-200).trim()), opts);
      head.load("items");
      tail.load("items");
      return { s, head, tail };
    });
    await ctx.sync();

    const marked = [];
    let missing = 0;
    for (const f of found) {
      let range = null;
      if (f.whole && f.whole.items.length) range = f.whole.items[0];
      else if (f.head && f.head.items.length && f.tail.items.length) range = f.head.items[0].expandTo(f.tail.items[0]);
      if (!range) { missing++; continue; }
      const cc = range.insertContentControl();
      cc.tag = TAG;
      cc.title = `AI-like here: rank ${f.s.rank} of ${result.scored}`;
      cc.appearance = "Hidden";
      cc.font.highlightColor = "#FFF2A8";
      cc.load("id");
      marked.push({ s: f.s, cc });
    }
    await ctx.sync();

    for (const m of marked) addCard(m.cc.id, m.s, result.scored);
    $("flagged").hidden = !marked.length;
    let msg = marked.length
      ? `Marked ${marked.length} sentence${marked.length === 1 ? "" : "s"} in yellow.`
      : "Nothing stands out: no paragraph looked AI-generated, so no sentences are marked.";
    if (missing) msg += ` ${missing} couldn't be located in the document (special formatting or line breaks).`;
    setStatus(msg);
  });
}

function showSummary(r) {
  const stat = document.createElement("div");
  stat.className = "stat";
  const L = r.likelihood;
  if (L) {
    stat.innerHTML = `<div class="likely-label">Likelihood this was AI-generated</div>
      <div class="likely ${L.severity}"><b>${Number(L.percent)}%</b> <span></span></div>
      <div class="bar"><div style="width:${Number(L.percent)}%"></div></div>`;
    stat.querySelector(".likely span").textContent = L.label;
  } else {
    stat.innerHTML = `<div class="likely-label">No estimate: the text was too short to score.</div>`;
  }
  const extra = [`${Number(r.words)} words checked. ${L ? L.caveat : ""}`, ...r.warnings];
  for (const t of extra) {
    const p = document.createElement("div");
    p.className = "note";
    p.textContent = t;
    stat.appendChild(p);
  }
  $("summary").replaceChildren(stat);
}

function addCard(ccId, s, scored) {
  const li = document.createElement("li");
  li.className = "card";
  const sentence = document.createElement("span");
  sentence.className = "sentence";
  sentence.textContent = s.text;
  sentence.title = "Show in document";
  const meta = document.createElement("div");
  meta.className = "meta";
  meta.textContent = `Ranked ${s.rank} of ${scored} sentences in this text for machine-likeness (1 = most). A ranking, not a verdict on this sentence.`;
  const actions = document.createElement("div");
  actions.className = "actions row";
  const btn = document.createElement("button");
  btn.textContent = "Suggest rewrite";
  actions.appendChild(btn);
  const slot = document.createElement("div");
  li.append(sentence, meta, actions, slot);
  $("cards").appendChild(li);

  const card = { ccId, text: s.text, el: li, slot, done: false };
  sentence.onclick = () => run(async (ctx) => { ctx.document.contentControls.getById(ccId).select(); await ctx.sync(); });
  btn.onclick = () => suggest([card]);
  cards.push(card);
}

function suggest(list) {
  if (!list.length) return;
  return run(async (ctx) => {
    const ccs = list.map((c) => { const cc = ctx.document.contentControls.getById(c.ccId); cc.load("text"); return cc; });
    await ctx.sync();
    const texts = ccs.map((cc) => clean(cc.text));
    setStatus(`Writing ${list.length} suggestion${list.length === 1 ? "" : "s"} locally…`);
    const res = await api("/api/rewrite", { texts, mode: $("mode").value });
    res.items.forEach((item, i) => showSuggestion(list[i], item));
    setStatus("Review each suggestion, then Replace or Skip.");
  });
}

function showSuggestion(card, item) {
  const box = document.createElement("div");
  box.className = "suggestion";
  const label = document.createElement("div");
  label.className = "meta";
  label.textContent = STATUS_LABELS[item.status] || item.status;
  const diff = document.createElement("p");
  diff.innerHTML = item.diff[1]; // built server-side from html.escape()d text
  box.append(label, diff);
  if (item.issues.length) {
    const ul = document.createElement("ul");
    ul.className = "issues";
    for (const i of item.issues) {
      const li = document.createElement("li");
      li.className = i.severity;
      li.textContent = i.message;
      ul.appendChild(li);
    }
    box.appendChild(ul);
  }
  const row = document.createElement("div");
  row.className = "row";
  const replace = document.createElement("button");
  replace.className = "primary";
  replace.textContent = "Replace";
  const blocked = item.status === "kept-original" || item.status === "unchanged";
  const skip = document.createElement("button");
  skip.textContent = blocked ? "Remove mark" : "Skip";
  row.append(...(blocked ? [] : [replace]), skip);
  box.appendChild(row);
  card.slot.replaceChildren(box);

  replace.onclick = () => run(async (ctx) => {
    const cc = ctx.document.contentControls.getById(card.ccId);
    cc.insertText(item.final, "Replace");
    cc.font.highlightColor = null;
    cc.delete(true);
    const body = ctx.document.body;
    body.load("text");
    await ctx.sync();
    lastScanned = lastSeen = body.text; // our own edit should not trigger an auto-scan
    finish(card, "Replaced.");
  });
  skip.onclick = () => run(async (ctx) => {
    const cc = ctx.document.contentControls.getById(card.ccId);
    cc.font.highlightColor = null;
    cc.delete(true);
    await ctx.sync();
    finish(card, "Original kept.");
  });
}

function finish(card, msg) {
  card.done = true;
  card.el.classList.add("done");
  card.slot.replaceChildren(Object.assign(document.createElement("div"), { className: "meta", textContent: msg }));
  card.el.querySelector(".actions").remove();
  card.el.querySelector(".sentence").onclick = null;
}

async function autoScanTick() {
  if (!inWord || busy || !$("auto").checked) return;
  try {
    let text = "";
    await Word.run(async (ctx) => {
      const body = ctx.document.body;
      body.load("text");
      await ctx.sync();
      text = body.text;
    });
    const now = Date.now();
    if (text !== lastSeen) { lastSeen = text; changedAt = now; return; }
    if (text !== lastScanned && text.trim() && now - changedAt >= QUIET_MS && !busy) await scan();
  } catch { /* the document may be busy; try again next tick */ }
}
