"""Local AI-assisted editing for the Rewrite tab.

Uses the already-downloaded Qwen/Qwen2.5-1.5B-Instruct model (the Binoculars performer),
offline. Text is processed in chunks (whole paragraphs, or groups of sentences for long
paragraphs), so nothing is truncated. Every chunk is checked after generation: if a number,
citation or link is lost or added, or the output looks cut off, the original wording is kept
and the model's suggestion is shown for reference only.

The output is AI-assisted editing that needs human review. It is not tuned against, or
tested with, any AI detector.
"""
from __future__ import annotations

import difflib
import html
import os
import re
from collections import Counter
from dataclasses import dataclass, field
from typing import Callable

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"

import detector  # noqa: E402  (split_spans, DEVICE; also enforces offline mode)

MODEL_ID = detector.PERFORMER_ID  # same downloaded Qwen2.5-1.5B-Instruct snapshot
CHUNK_TOKENS = 300  # input tokens per generation call; longer paragraphs are split by sentence

_RULES = (
    "You are a careful copy editor. Use Australian English spelling and conventions. "
    "Preserve the meaning and every fact, number, name, quotation and citation exactly as written. "
    "Do not add information, examples, references, evidence or personal experiences. Do not remove points. "
    "Output only the edited text, with no introduction, notes or quotation marks around it."
)
MODES = {
    "light": ("Light proofreading", "Fix only spelling, grammar and punctuation errors. Change as few words as possible and keep the author's wording and sentence structure. Do not delete words, phrases or sentences unless they are an accidental repeat."),
    "plain": ("Plain English", "Rewrite in plain English: shorter sentences, common words and active voice where it reads better. Keep every point."),
    "academic": ("Natural academic", "Rewrite in a clear, natural academic style suitable for a university business assignment: precise and formal but not stiff. Avoid filler, clichés and inflated wording."),
}
_LENGTH_LIMITS = {"light": (0.8, 1.25), "plain": (0.6, 1.6), "academic": (0.6, 1.6)}

# US spellings the model may introduce. Only replaced when the US form was not already in the
# user's own text (so quotations, titles and names the user wrote are left alone).
_IZE_STEMS = ["analy", "organ", "recogn", "real", "priorit", "emphas", "summar", "minim", "maxim", "util", "optim",
              "standard", "character", "special", "critic", "categor", "capital", "general", "final", "author", "visual"]
_US_TO_AU = {}
for _stem in _IZE_STEMS:
    _z = "z" if _stem == "analy" else "iz"  # analy+ze, organ+ize
    _s = "s" if _stem == "analy" else "is"
    for _end in ("e", "ed", "es", "ing", "ation", "ations", "ational"):
        if _stem == "analy" and _end.startswith("ation"):
            continue
        _US_TO_AU[_stem + _z + _end] = _stem + _s + _end
_US_TO_AU.update({
    "behavior": "behaviour", "behaviors": "behaviours", "behavioral": "behavioural", "color": "colour", "colors": "colours",
    "favor": "favour", "favorable": "favourable", "favorite": "favourite", "honor": "honour", "labor": "labour",
    "neighbor": "neighbour", "neighbors": "neighbours", "center": "centre", "centers": "centres", "centered": "centred",
    "defense": "defence", "offense": "offence", "traveled": "travelled", "traveling": "travelling", "traveler": "traveller",
    "modeled": "modelled", "modeling": "modelling", "labeled": "labelled", "labeling": "labelling", "canceled": "cancelled",
    "canceling": "cancelling", "catalog": "catalogue", "enrollment": "enrolment", "fulfill": "fulfil", "fulfillment": "fulfilment",
    "aging": "ageing", "judgment": "judgement",
})
_US_PATTERN = re.compile(r"\b(" + "|".join(sorted(map(re.escape, _US_TO_AU), key=len, reverse=True)) + r")\b", re.I)

_NUMBER = re.compile(r"\d+(?:[.,]\d+)*")
_PAREN_CITATION = re.compile(r"\((?=[^()]*\b(?:1[5-9]|20)\d{2}[a-z]?\b)[^()]{2,200}\)|\[\d+(?:\s*[-–,]\s*\d+)*\]")
_NARRATIVE_CITATION = re.compile(
    r"\b[A-Z][A-Za-z'’\-]+(?: (?:and|&) [A-Z][A-Za-z'’\-]+| et al\.)? \((?:1[5-9]|20)\d{2}[a-z]?(?:, pp?\. ?\d+(?:[-–]\d+)?)?\)"
)
_URL = re.compile(r"https?://[^\s)\]]+|\bdoi\.org/[^\s)\]]+|\b10\.\d{4,9}/[^\s)\]]+")
_NAME = re.compile(r"\b[A-Z][a-zA-Z'’&\-]*[a-zA-Z]\b|\b[A-Z]{2,}\b")
_FIRST_PERSON = re.compile(r"\b(?:I|me|my|mine|we|us|our|ours)\b", re.I)
_PREAMBLE = re.compile(r"^\s*(?:here(?:'s| is| are)[^\n:]{0,80}:|(?:edited|revised|rewritten|proofread|corrected) (?:text|version|paragraph)\s*:)\s*", re.I)


@dataclass
class Chunk:
    paragraph: int
    original: str
    suggested: str = ""
    final: str = ""
    status: str = ""  # "edited" | "review" | "kept-original" | "unchanged"
    issues: list[tuple[str, str]] = field(default_factory=list)  # (severity: "error" | "warning" | "info", message)


@dataclass
class RewriteResult:
    mode: str
    chunks: list[Chunk]
    words_in: int
    paragraphs: int
    output: str


def _norm(s: str) -> str:
    return re.sub(r"\s+", " ", s.replace("’", "'").replace("–", "-").replace("—", "-")).strip()


def _numbers(s: str) -> Counter:
    return Counter(n.replace(",", "") for n in _NUMBER.findall(s))


def _citations(s: str) -> Counter:
    s = _norm(s)
    return Counter(_PAREN_CITATION.findall(s) + _NARRATIVE_CITATION.findall(s))


def _urls(s: str) -> Counter:
    return Counter(u.rstrip(".,;:") for u in _URL.findall(s))


def _names(s: str) -> set[str]:
    found = set()
    for m in _NAME.finditer(s):
        before = s[: m.start()].rstrip()
        if m.group(0) == "I" or not before or before[-1] in ".!?:\"“(\n":
            continue  # sentence-initial capitals are not evidence of a name
        found.add(m.group(0))
    return found


def _fmt(counter: Counter) -> str:
    return ", ".join(sorted(counter.elements()))


def australianise(text: str, original: str) -> tuple[str, list[str]]:
    """Replace US spellings the model introduced (not ones already in the user's text)."""
    own = {w.lower() for w in re.findall(r"[A-Za-z]+", original)}
    changes: list[str] = []

    def fix(m: re.Match) -> str:
        word = m.group(0)
        low = word.lower()
        if low in own or (low.startswith("labor") and word[0].isupper()):  # keep e.g. "Labor Party"
            return word
        au = _US_TO_AU[low]
        new = au.upper() if word.isupper() and len(word) > 1 else (au[0].upper() + au[1:] if word[0].isupper() else au)
        changes.append(f"{word} → {new}")
        return new

    return _US_PATTERN.sub(fix, text), changes


def check(original: str, suggested: str, mode: str, finished: bool) -> list[tuple[str, str]]:
    """Compare a suggestion with its source. Errors mean the original wording must be kept."""
    issues: list[tuple[str, str]] = []
    if not suggested.strip():
        return [("error", "The model returned no text.")]
    if not finished:
        issues.append(("error", "The suggestion looks cut off (the model hit its length limit)."))
    for label, extract in (("Number", _numbers), ("Citation", _citations), ("Link/DOI", _urls)):
        before, after = extract(original), extract(suggested)
        if before - after:
            issues.append(("error", f"{label}(s) missing from the suggestion: {_fmt(before - after)}"))
        if after - before:
            issues.append(("error", f"{label}(s) added by the suggestion: {_fmt(after - before)}"))
    lo, hi = _LENGTH_LIMITS[mode]
    ratio = len(suggested.split()) / max(1, len(original.split()))
    if not lo <= ratio <= hi:
        issues.append(("warning", f"Length changed a lot ({ratio:.0%} of the original). Check nothing was added or left out."))
    missing = sorted(n for n in _names(original) if n not in suggested)
    if missing:
        issues.append(("warning", f"Possible names or terms no longer present: {', '.join(missing)}"))
    lower_original = original.lower()
    added = sorted(n for n in _names(suggested) if n.lower() not in lower_original)
    if added:
        issues.append(("warning", f"New capitalised words (check for invented names or claims): {', '.join(added)}"))
    if len(_FIRST_PERSON.findall(suggested)) > len(_FIRST_PERSON.findall(original)):
        issues.append(("warning", "Adds first-person wording (I/my/we). Check no personal experience was invented."))
    return issues


def _chunks(text: str, tokenizer) -> list[Chunk]:
    paragraphs, sentences = detector.split_spans(text)
    count = lambda s: len(tokenizer(s, add_special_tokens=False)["input_ids"])  # noqa: E731
    chunks: list[Chunk] = []
    for p in paragraphs:
        if count(p.text) <= CHUNK_TOKENS:
            chunks.append(Chunk(p.index, p.text))
            continue
        inside = [s for s in sentences if p.start <= s.start < p.end]
        start, size = inside[0].start, 0
        for s in inside:
            n = count(s.text)
            if size and size + n > CHUNK_TOKENS:
                chunks.append(Chunk(p.index, text[start:prev_end]))
                start, size = s.start, 0
            size += n
            prev_end = s.end
        chunks.append(Chunk(p.index, text[start:prev_end]))
    return chunks


def rewrite(text: str, mode: str, progress: Callable[[str], None] = lambda _: None) -> RewriteResult:
    import torch
    from transformers import AutoModelForCausalLM, AutoTokenizer

    if mode not in MODES:
        raise ValueError(f"Unknown mode {mode!r}")
    progress("Loading the editing model (Qwen2.5-1.5B-Instruct)…")
    tokenizer = AutoTokenizer.from_pretrained(MODEL_ID, local_files_only=True)
    model = AutoModelForCausalLM.from_pretrained(MODEL_ID, dtype=torch.bfloat16, local_files_only=True).to(detector.DEVICE).eval()
    eos = model.generation_config.eos_token_id
    eos = set(eos if isinstance(eos, list) else [eos])

    chunks = _chunks(text, tokenizer)
    for i, chunk in enumerate(chunks, 1):
        progress(f"Editing chunk {i} of {len(chunks)}…")
        messages = [{"role": "system", "content": f"{_RULES} {MODES[mode][1]}"}, {"role": "user", "content": chunk.original}]
        enc = tokenizer.apply_chat_template(messages, add_generation_prompt=True, return_tensors="pt", return_dict=True)
        ids = enc["input_ids"].to(detector.DEVICE)
        budget = 2 * len(tokenizer(chunk.original, add_special_tokens=False)["input_ids"]) + 64
        with torch.inference_mode():
            out = model.generate(
                ids, attention_mask=enc["attention_mask"].to(detector.DEVICE), max_new_tokens=budget,
                do_sample=False, temperature=None, top_p=None, top_k=None, repetition_penalty=1.05,
            )
        new = out[0, ids.shape[1]:].tolist()
        finished = any(t in eos for t in new)
        suggestion = _PREAMBLE.sub("", tokenizer.decode(new, skip_special_tokens=True)).strip()
        if len(suggestion) > 1 and suggestion[0] in "\"“" and suggestion[-1] in "\"”" and chunk.original.strip()[:1] not in "\"“":
            suggestion = suggestion[1:-1].strip()
        joined = "\n" not in chunk.original and "\n" in suggestion
        if joined:  # a chunk never spans paragraphs, so line breaks the model adds are dropped
            suggestion = re.sub(r"\s*\n\s*", " ", suggestion)
        suggestion, spelling = australianise(suggestion, chunk.original)
        chunk.suggested = suggestion
        chunk.issues = check(chunk.original, suggestion, mode, finished)
        if joined:
            chunk.issues.append(("info", "Line breaks added by the model were removed to keep your paragraph structure."))
        if spelling:
            chunk.issues.append(("info", "Australian spelling applied: " + ", ".join(spelling)))
        if any(sev == "error" for sev, _ in chunk.issues):
            chunk.status, chunk.final = "kept-original", chunk.original
        elif _norm(suggestion) == _norm(chunk.original):
            chunk.status, chunk.final = "unchanged", chunk.original
        else:
            chunk.status = "review" if any(sev == "warning" for sev, _ in chunk.issues) else "edited"
            chunk.final = suggestion

    by_paragraph: dict[int, list[str]] = {}
    for chunk in chunks:
        by_paragraph.setdefault(chunk.paragraph, []).append(chunk.final)
    output = "\n\n".join(" ".join(parts) for _, parts in sorted(by_paragraph.items()))
    return RewriteResult(mode, chunks, len(text.split()), len(by_paragraph), output)


def rewrite_in_subprocess(text: str, mode: str, progress: Callable[[str], None] = lambda _: None) -> RewriteResult:
    """Run rewrite() in a short-lived worker process (see worker.py)."""
    import worker

    return worker.call("rewriter", "rewrite", {"text": text, "mode": mode}, progress)


def diff_html(original: str, suggested: str) -> tuple[str, str]:
    """Word-level diff: removed/replaced words struck through on the left, new words marked on the right."""
    split = lambda s: re.findall(r"\s+|[\w'’\-]+|[^\w\s]", s)  # noqa: E731
    a, b = split(original), split(suggested)
    left, right = [], []
    for op, i1, i2, j1, j2 in difflib.SequenceMatcher(a=a, b=b, autojunk=False).get_opcodes():
        old, new = html.escape("".join(a[i1:i2])), html.escape("".join(b[j1:j2]))
        if op == "equal":
            left.append(old)
            right.append(new)
            continue
        if old.strip():
            left.append(f'<del class="rw-del">{old}</del>')
        elif old:
            left.append(old)
        if new.strip():
            right.append(f'<ins class="rw-ins">{new}</ins>')
        elif new:
            right.append(new)
    return "".join(left), "".join(right)
