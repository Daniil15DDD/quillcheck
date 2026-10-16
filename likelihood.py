"""One plain-language reading of how likely a text is AI-generated.

Kept free of torch/transformers so the add-in server can use it without loading models.
The headline uses the classifier: it is the stronger signal in VALIDATION.md (Binoculars
missed 12 of 16 newer-model texts) and, unlike the raw Binoculars ratio, needs no calibration.
"""
from __future__ import annotations

CAVEAT = (
    "This is the classifier model's estimate, not proof. In held-out tests it wrongly flagged "
    "about 1 in 7 human texts (22 of 155) and missed 3 of 16 texts from newer AI models."
)


PASSAGE_THRESHOLD = 0.5  # a paragraph "looks AI-generated" from this classifier score up
HUMAN_BELOW = 0.3  # the "Leans human-written" band of reading()
FALLBACK_TOP = 3  # most sentences marked when no paragraph looks AI-generated (and never over a quarter)
HIGHLIGHT_NOTE = (
    "When the whole text is not rated \"Leans human-written\", yellow marks the more machine-like half of the "
    "sentences inside paragraphs the classifier rates 50%+ likely AI-generated. Otherwise it marks up to "
    f"{FALLBACK_TOP} of the most machine-like sentences, never more than a quarter of them (by Binoculars score), so you can see what stands out most, "
    "even in a text that reads as human."
)


def flag_sentences(result) -> set[int]:
    """Indexes of sentences to highlight.

    When the whole-text score is at least "Unclear", a paragraph counts as AI-looking from its
    own classifier score, or, when it is too short to have one, from the whole-text score, and
    the lower-Binoculars half of the scored sentences in AI-looking paragraphs is marked (all of
    them when there are three or fewer). Short paragraphs scored alone are much noisier than the
    whole text (a 1% text had 40-word paragraphs at 50-57%), so they are not used below 30%.
    If that marks nothing, the lowest-Binoculars sentences are marked instead: FALLBACK_TOP at most,
    and never more than a quarter of the scored sentences (none in texts of three or fewer).
    """
    scored = sorted((s for s in result.sentences if s.binoculars is not None), key=lambda s: s.binoculars)
    # At most a quarter of the sentences: in a 4-sentence text, 3 marks covered most of the text.
    fallback = {s.index for s in scored[:min(FALLBACK_TOP, len(scored) // 4)]}
    if result.classifier is None or round(result.classifier * 100) < HUMAN_BELOW * 100:
        return fallback
    looks_ai = {}
    for p in result.paragraphs:
        score = p.classifier if p.classifier is not None else result.classifier
        looks_ai[p.index] = score is not None and score >= PASSAGE_THRESHOLD
    candidates = [
        s for s in result.sentences
        if s.binoculars is not None
        and looks_ai[next(p.index for p in result.paragraphs if p.start <= s.start < p.end)]
    ]
    if len(candidates) > 3:
        candidates = sorted(candidates, key=lambda s: s.binoculars)[: (len(candidates) + 1) // 2]
    return {s.index for s in candidates} or fallback


def reading(classifier: float | None) -> tuple[int, str, str] | None:
    """(percent, label, severity) for a classifier score, or None if the text was not scored."""
    if classifier is None:
        return None
    pct = round(classifier * 100)
    if pct >= 70:
        return pct, "Leans AI-generated", "error"
    if pct >= 30:
        return pct, "Unclear: could be either", "warning"
    return pct, "Leans human-written", "success"
