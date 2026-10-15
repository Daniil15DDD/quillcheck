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
