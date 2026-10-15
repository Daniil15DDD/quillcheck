"""Personal calibration samples. Only scores are stored, never the text itself."""
from __future__ import annotations

import json
from datetime import date
from pathlib import Path

PATH = Path(__file__).with_name("calibration.json")


def load() -> list[dict]:
    try:
        return json.loads(PATH.read_text())["samples"]
    except (FileNotFoundError, KeyError, json.JSONDecodeError):
        return []


def save(samples: list[dict]) -> None:
    PATH.write_text(json.dumps({"samples": samples}, indent=1))


def add(label: str, binoculars: float | None, classifier: float | None, words: int) -> None:
    samples = load()
    samples.append({"label": label, "binoculars": binoculars, "classifier": classifier, "words": words, "added": date.today().isoformat()})
    save(samples)


def scores(label: str, key: str) -> list[float]:
    return sorted(s[key] for s in load() if s["label"] == label and s[key] is not None)


def binoculars_context(score: float) -> tuple[str, str] | None:
    """Place a Binoculars score against the user's own samples. Returns (severity, message) or None if uncalibrated.

    Lower Binoculars scores look more machine-like, so the question is how many of
    the user's known-human samples scored at or below this text.
    """
    human = scores("human", "binoculars")
    if len(human) < 3:
        return None
    lower = sum(h <= score for h in human)
    msg = f"{lower} of your {len(human)} own writing samples scored this low or lower (your range {human[0]:.3f}–{human[-1]:.3f})."
    ai = scores("ai", "binoculars")
    if len(ai) >= 3:
        msg += f" Your AI samples ranged {ai[0]:.3f}–{ai[-1]:.3f}."
    if score < human[0]:
        return "warning", "Below every sample of your own writing. " + msg
    if lower <= max(1, len(human) // 10):
        return "warning", "At the low edge of your own writing. " + msg
    return "info", "Within the range of your own writing. " + msg
