"""Score every text in validation/data/evalset.jsonl with both detectors (each model loaded once).

Writes validation/data/scores.jsonl: {"id", "classifier", "binoculars"} per text. Offline, like the app.
"""
from __future__ import annotations

import json
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))
import detector  # noqa: E402

rows = [json.loads(line) for line in open(HERE / "data/evalset.jsonl")]
scores = {r["id"]: {"id": r["id"]} for r in rows}
t0 = time.time()

bino = detector.Binoculars()
try:
    for i, r in enumerate(rows, 1):
        ppl, xppl, _ = bino.token_scores(r["text"])
        scores[r["id"]]["binoculars"] = sum(ppl) / sum(xppl) if ppl else None
        if i % 100 == 0:
            print(f"binoculars {i}/{len(rows)}  {time.time() - t0:.0f}s", flush=True)
finally:
    bino.close()

clf = detector.Classifier()
try:
    for i, r in enumerate(rows, 1):
        paragraphs, _ = detector.split_spans(r["text"])
        scores[r["id"]]["classifier"] = clf.score_document(r["text"], paragraphs, detailed=False)[0]
        if i % 100 == 0:
            print(f"classifier {i}/{len(rows)}  {time.time() - t0:.0f}s", flush=True)
finally:
    clf.close()

with open(HERE / "data/scores.jsonl", "w") as f:
    for s in scores.values():
        f.write(json.dumps(s) + "\n")
print(f"done: {len(rows)} texts in {time.time() - t0:.0f}s")
