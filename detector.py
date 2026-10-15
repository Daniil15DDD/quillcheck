"""Local AI-text estimators.

Two independent signals, both run offline once the models are downloaded:

- Binoculars (Hans et al., 2024): zero-shot ratio of the performer model's
  log-perplexity to the observer/performer cross-perplexity. Lower scores look
  more machine-like. The published threshold was tuned for Falcon-7B, so with
  the Qwen2.5-1.5B pair used here the raw score needs personal calibration.
  Unlike the reference code (first 512 tokens only), the whole text is scored
  in 512-token windows; that extension is unvalidated, hence "experimental".
- desklib/ai-text-detector-v1.01: a DeBERTa-v3-large classifier trained on the
  RAID benchmark (news, books, abstracts, reviews, Reddit, recipes, Wikipedia,
  poetry). Its output is a classifier score, not a calibrated probability.

Both are model estimates, not evidence of authorship.
"""
from __future__ import annotations

import gc
import math
import os
import re

# Never contact the Hugging Face Hub at runtime, whatever the shell says. Must run before
# transformers/huggingface_hub are imported. Models come only from the local cache.
os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"
os.environ["HF_HUB_DISABLE_TELEMETRY"] = "1"
from dataclasses import dataclass, field
from typing import Callable

import torch
import torch.nn as nn
import torch.nn.functional as F
from transformers import AutoConfig, AutoModel, AutoModelForCausalLM, AutoTokenizer, PreTrainedModel

OBSERVER_ID = "Qwen/Qwen2.5-1.5B"
PERFORMER_ID = "Qwen/Qwen2.5-1.5B-Instruct"
CLASSIFIER_ID = "desklib/ai-text-detector-v1.01"

DEVICE = torch.device("mps" if torch.backends.mps.is_available() else "cpu")
WINDOW_TOKENS = 512  # Binoculars context window; longer texts are scored in consecutive windows
LOGIT_SLICE = 128  # positions converted to float32 at a time when computing Binoculars terms
CLASSIFIER_MAX_TOKENS = 768  # the classifier's training length, per its model card
MIN_SPAN_TOKENS = 8  # below this a sentence score is too noisy to show
MIN_CLASSIFIER_WORDS = 40  # per-paragraph classifier scores are skipped for shorter paragraphs
SHORT_TEXT_WORDS = 150

_PARAGRAPH_BREAK = r"\n\s*\n"
_SENTENCE_BREAK = r"(?:(?<=[.!?])|(?<=[.!?][\"”’)]))\s+(?=[\"“‘(\[A-Z0-9])|\n"


@dataclass
class Span:
    index: int
    start: int
    end: int
    text: str
    binoculars: float | None = None
    classifier: float | None = None


@dataclass
class Result:
    words: int
    binoculars: float | None
    classifier: float | None
    paragraphs: list[Span] = field(default_factory=list)
    sentences: list[Span] = field(default_factory=list)
    warnings: list[str] = field(default_factory=list)
    coverage: dict = field(default_factory=dict)


def _strip(text: str, s: int, e: int):
    while s < e and text[s].isspace():
        s += 1
    while e > s and text[e - 1].isspace():
        e -= 1
    if e > s:
        yield s, e


def _segments(text: str, start: int, end: int, pattern: str):
    pos = start
    for m in re.finditer(pattern, text[start:end]):
        yield from _strip(text, pos, start + m.start())
        pos = start + m.end()
    yield from _strip(text, pos, end)


def split_spans(text: str) -> tuple[list[Span], list[Span]]:
    paragraphs, sentences = [], []
    for ps, pe in _segments(text, 0, len(text), _PARAGRAPH_BREAK):
        paragraphs.append(Span(len(paragraphs), ps, pe, text[ps:pe]))
        for ss, se in _segments(text, ps, pe, _SENTENCE_BREAK):
            sentences.append(Span(len(sentences), ss, se, text[ss:se]))
    return paragraphs, sentences


def _free() -> None:
    gc.collect()
    if DEVICE.type == "mps":
        torch.mps.empty_cache()


class Binoculars:
    def __init__(self) -> None:
        self.tokenizer = AutoTokenizer.from_pretrained(OBSERVER_ID, local_files_only=True)
        self.observer = AutoModelForCausalLM.from_pretrained(OBSERVER_ID, dtype=torch.bfloat16, local_files_only=True).to(DEVICE).eval()
        self.performer = AutoModelForCausalLM.from_pretrained(PERFORMER_ID, dtype=torch.bfloat16, local_files_only=True).to(DEVICE).eval()

    def close(self) -> None:
        self.observer = self.performer = None
        _free()

    @torch.inference_mode()
    def token_scores(self, text: str) -> tuple[list[float], list[float], list[int]]:
        """Per predicted token: performer log-perplexity, cross-perplexity, and the token's start char."""
        enc = self.tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)
        ids, offsets = enc["input_ids"], enc["offset_mapping"]
        ppl, xppl = [], []
        # Windows overlap by one token so every token after the first is predicted exactly once.
        for s in range(0, max(len(ids) - 1, 0), WINDOW_TOKENS - 1):
            window = torch.tensor([ids[s : s + WINDOW_TOKENS]], device=DEVICE)
            obs_all = self.observer(window).logits[0, :-1]
            perf_all = self.performer(window).logits[0, :-1]
            targets = window[0, 1:]
            # Slices keep the float32 copies of the ~150k-wide vocabulary small (peak memory).
            for k in range(0, obs_all.shape[0], LOGIT_SLICE):
                obs = obs_all[k : k + LOGIT_SLICE].float()
                perf = perf_all[k : k + LOGIT_SLICE].float()
                ppl.append(F.cross_entropy(perf, targets[k : k + LOGIT_SLICE], reduction="none"))
                xppl.append(-(F.softmax(obs, -1) * F.log_softmax(perf, -1)).sum(-1))
            del obs_all, perf_all
        if not ppl:
            return [], [], []
        starts = [offsets[i][0] for i in range(1, len(ids))]
        return torch.cat(ppl).cpu().tolist(), torch.cat(xppl).cpu().tolist(), starts


def _ratio(ppl, xppl, starts, s: int, e: int, min_tokens: int) -> float | None:
    picked = [i for i, c in enumerate(starts) if s <= c < e]
    if len(picked) < min_tokens:
        return None
    return sum(ppl[i] for i in picked) / sum(xppl[i] for i in picked)


class _DesklibModel(PreTrainedModel):
    """Architecture from the desklib model card: mean-pooled DeBERTa + one linear logit."""

    config_class = AutoConfig

    def __init__(self, config):
        super().__init__(config)
        self.model = AutoModel.from_config(config)
        self.classifier = nn.Linear(config.hidden_size, 1)
        self.post_init()

    def forward(self, input_ids, attention_mask):
        hidden = self.model(input_ids, attention_mask=attention_mask)[0]
        mask = attention_mask.unsqueeze(-1).to(hidden.dtype)
        pooled = (hidden * mask).sum(1) / mask.sum(1).clamp(min=1e-9)
        return self.classifier(pooled).squeeze(-1)


class Classifier:
    def __init__(self) -> None:
        self.tokenizer = AutoTokenizer.from_pretrained(CLASSIFIER_ID, local_files_only=True)
        self.model = _DesklibModel.from_pretrained(CLASSIFIER_ID, local_files_only=True).to(DEVICE).eval()

    def close(self) -> None:
        self.model = None
        _free()

    @torch.inference_mode()
    def score_ids(self, ids: list[int]) -> float:
        full = [self.tokenizer.cls_token_id, *ids, self.tokenizer.sep_token_id]  # same as the tokenizer's own wrapping
        input_ids = torch.tensor([full], device=DEVICE)
        return torch.sigmoid(self.model(input_ids, torch.ones_like(input_ids))).item()

    def score_document(self, text: str, paragraphs: list[Span], detailed: bool) -> tuple[float | None, int, int, int]:
        """Score every token: whole paragraphs are packed into windows that fit the classifier,
        and a paragraph longer than one window is split across several. Nothing is truncated.

        Returns (token-weighted mean score, tokens scored, tokens in text, windows used).
        Only whitespace tokens between paragraphs can be left unscored. With `detailed`,
        each paragraph of at least MIN_CLASSIFIER_WORDS words also gets its own score.
        """
        limit = CLASSIFIER_MAX_TOKENS - self.tokenizer.num_special_tokens_to_add()
        enc = self.tokenizer(text, return_offsets_mapping=True, add_special_tokens=False)
        ids, offsets = enc["input_ids"], enc["offset_mapping"]
        per_paragraph = [[i for i, (s, _) in enumerate(offsets) if p.start <= s < p.end] for p in paragraphs]

        windows, current = [], []
        for tokens in per_paragraph:
            if current and len(current) + len(tokens) > limit:
                windows.append(current)
                current = []
            for k in range(0, len(tokens), limit):
                piece = tokens[k : k + limit]
                if current and len(current) + len(piece) > limit:
                    windows.append(current)
                    current = []
                current = current + piece
        if current:
            windows.append(current)

        weighted = sum(self.score_ids([ids[i] for i in w]) * len(w) for w in windows)
        scored = sum(len(w) for w in windows)
        if detailed:
            for p, tokens in zip(paragraphs, per_paragraph):
                if len(p.text.split()) >= MIN_CLASSIFIER_WORDS:
                    parts = [tokens[k : k + limit] for k in range(0, len(tokens), limit)]
                    p.classifier = sum(self.score_ids([ids[i] for i in part]) * len(part) for part in parts) / len(tokens)
        return (weighted / scored if scored else None), scored, len(ids), len(windows)


def analyse(text: str, detailed: bool = True, progress: Callable[[str], None] = lambda _: None) -> Result:
    """Score a text with both models. Models are loaded one at a time and freed afterwards."""
    paragraphs, sentences = split_spans(text)
    result = Result(words=len(text.split()), binoculars=None, classifier=None, paragraphs=paragraphs, sentences=sentences)
    if result.words < SHORT_TEXT_WORDS:
        result.warnings.append(f"Only {result.words} words. Both models are unreliable on short texts; {SHORT_TEXT_WORDS}+ words is better.")
    if not paragraphs:
        return result

    progress("Loading Binoculars models (Qwen2.5-1.5B base + instruct)…")
    bino = Binoculars()
    try:
        progress("Scoring with Binoculars…")
        ppl, xppl, starts = bino.token_scores(text)
    finally:
        bino.close()
    result.binoculars = _ratio(ppl, xppl, starts, 0, len(text), 1)
    result.coverage.update(
        binoculars_tokens=len(ppl) + 1 if ppl else 0,
        binoculars_windows=math.ceil(len(ppl) / (WINDOW_TOKENS - 1)) if ppl else 0,
    )
    if detailed:
        for span in paragraphs + sentences:
            span.binoculars = _ratio(ppl, xppl, starts, span.start, span.end, MIN_SPAN_TOKENS)

    progress("Loading classifier (desklib DeBERTa-v3-large)…")
    clf = Classifier()
    try:
        progress("Scoring with classifier…")
        result.classifier, scored, total, windows = clf.score_document(text, paragraphs, detailed)
        result.coverage.update(classifier_tokens=scored, classifier_total=total, classifier_windows=windows)
    finally:
        clf.close()
    return result


# Kept for app.py: errors from the worker process.
from worker import WorkerError as AnalysisError  # noqa: E402


def analyse_in_subprocess(text: str, detailed: bool = True, progress: Callable[[str], None] = lambda _: None) -> Result:
    """Run analyse() in a short-lived worker process (see worker.py)."""
    import worker

    return worker.call("detector", "analyse", {"text": text, "detailed": detailed}, progress)


if __name__ == "__main__":
    import sys

    sample = open(sys.argv[1], encoding="utf-8").read() if len(sys.argv) > 1 else sys.stdin.read()
    r = analyse(sample, progress=lambda m: print(m, file=sys.stderr))
    print(f"words={r.words} binoculars={r.binoculars} classifier={r.classifier}")
    for w in r.warnings:
        print("warning:", w)
