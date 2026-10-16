"""Fit and evaluate the score combiner (logistic regression on both detectors + length).

Trains on split "train" only, then compares three scorers on "test_in" and "test_ood":
  raw         desklib classifier score as-is (what the app showed before)
  recal       classifier recalibrated (logistic on its logit only)
  combined    logistic on [classifier logit, Binoculars, log words]
Decision rule, fixed before the first run: adopt "combined" only if its Brier score is lower than
raw's on BOTH test sets and its AUROC is not more than 0.01 below raw's on either.
Writes combiner.json (weights only, used by the app if adopted) and validation/combiner_results.json.
"""
from __future__ import annotations

import json
import math
from datetime import date
from pathlib import Path

import numpy as np

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
FEATURES = ["classifier_logit", "binoculars", "log_words"]
L2 = 1.0

rows = {json.loads(l)["id"]: json.loads(l) for l in open(HERE / "data/evalset.jsonl")}
for line in open(HERE / "data/scores.jsonl"):
    s = json.loads(line)
    rows[s["id"]].update(classifier=s["classifier"], binoculars=s["binoculars"])
rows = [r for r in rows.values() if r.get("classifier") is not None and r.get("binoculars") is not None]


def features(r: dict) -> list[float]:
    c = min(max(r["classifier"], 1e-4), 1 - 1e-4)
    return [math.log(c / (1 - c)), r["binoculars"], math.log(r["words"])]


def fit(X: np.ndarray, y: np.ndarray) -> np.ndarray:
    """L2-regularised logistic regression by Newton's method. Returns [bias, w...]."""
    Xb = np.hstack([np.ones((len(X), 1)), X])
    w = np.zeros(Xb.shape[1])
    reg = np.eye(len(w)) * L2
    reg[0, 0] = 0  # no penalty on the bias
    for _ in range(50):
        p = 1 / (1 + np.exp(-Xb @ w))
        g = Xb.T @ (p - y) + reg @ w
        H = Xb.T @ (Xb * (p * (1 - p))[:, None]) + reg
        step = np.linalg.solve(H, g)
        w -= step
        if np.abs(step).max() < 1e-8:
            break
    return w


def auroc(p: np.ndarray, y: np.ndarray) -> float | None:
    pos, neg = p[y == 1], p[y == 0]
    if not len(pos) or not len(neg):
        return None
    return float(((pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()) / (len(pos) * len(neg)))


def metrics(p: np.ndarray, y: np.ndarray) -> dict:
    flag = p >= 0.5
    n_h, n_a = int((y == 0).sum()), int((y == 1).sum())
    fp, fn = int((flag & (y == 0)).sum()), int((~flag & (y == 1)).sum())
    bins = np.minimum((p * 10).astype(int), 9)
    ece = sum(abs(p[bins == b].mean() - y[bins == b].mean()) * (bins == b).sum() for b in range(10) if (bins == b).any()) / len(p)
    return {"n_human": n_h, "n_ai": n_a, "FP": fp, "FPR": fp / n_h if n_h else None, "FN": fn, "FNR": fn / n_a if n_a else None,
            "AUROC": auroc(p, y), "Brier": float(((p - y) ** 2).mean()), "ECE": float(ece)}


def split(name: str):
    sel = [r for r in rows if r["split"] == name]
    return sel, np.array([features(r) for r in sel]), np.array([1.0 if r["label"] == "ai" else 0.0 for r in sel])


train, Xtr, ytr = split("train")
mean, std = Xtr.mean(0), Xtr.std(0)
w_comb = fit((Xtr - mean) / std, ytr)
w_recal = fit(((Xtr - mean) / std)[:, :1], ytr)


def predict(X: np.ndarray, which: str, raw: np.ndarray) -> np.ndarray:
    Z = (X - mean) / std
    if which == "raw":
        return raw
    if which == "recal":
        return 1 / (1 + np.exp(-(w_recal[0] + Z[:, :1] @ w_recal[1:])))
    return 1 / (1 + np.exp(-(w_comb[0] + Z @ w_comb[1:])))


results = {"date": date.today().isoformat(), "n_train": len(train), "features": FEATURES, "sets": {}}
for name in ("test_in", "test_ood"):
    sel, X, y = split(name)
    raw = np.array([r["classifier"] for r in sel])
    out = {}
    for which in ("raw", "recal", "combined"):
        p = predict(X, which, raw)
        out[which] = {"ALL": metrics(p, y)}
        for src in sorted({r["source"] for r in sel}):
            idx = np.array([r["source"] == src for r in sel])
            out[which][src] = metrics(p[idx], y[idx])
    results["sets"][name] = out

adopt = all(
    results["sets"][s]["combined"]["ALL"]["Brier"] < results["sets"][s]["raw"]["ALL"]["Brier"]
    and (results["sets"][s]["combined"]["ALL"]["AUROC"] or 0) >= (results["sets"][s]["raw"]["ALL"]["AUROC"] or 0) - 0.01
    for s in ("test_in", "test_ood")
)
results["adopted"] = adopt
results["weights"] = {"bias": float(w_comb[0]), **{f: float(v) for f, v in zip(FEATURES, w_comb[1:])}}
json.dump(results, open(HERE / "combiner_results.json", "w"), indent=1)
if adopt:
    json.dump({"features": FEATURES, "mean": mean.tolist(), "std": std.tolist(), "bias": float(w_comb[0]),
               "weights": w_comb[1:].tolist(), "trained": results["date"], "n_train": len(train)},
              open(ROOT / "combiner.json", "w"), indent=1)

for s in ("test_in", "test_ood"):
    print(f"\n== {s}")
    for which in ("raw", "recal", "combined"):
        m = results["sets"][s][which]["ALL"]
        print(f"  {which:9} AUROC {m['AUROC'] or float('nan'):.3f}  Brier {m['Brier']:.3f}  ECE {m['ECE']:.3f}  "
              f"FP {m['FP']}/{m['n_human']}  FN {m['FN']}/{m['n_ai']}")
    for src in [k for k in results["sets"][s]["raw"] if k != "ALL"]:
        r_, c_ = results["sets"][s]["raw"][src], results["sets"][s]["combined"][src]
        print(f"    {src:15} raw FP {r_['FP']}/{r_['n_human']} FN {r_['FN']}/{r_['n_ai']}   combined FP {c_['FP']}/{c_['n_human']} FN {c_['FN']}/{c_['n_ai']}")
print("\nweights", results["weights"])
print("ADOPT" if adopt else "NOT ADOPTED (keeps the raw classifier)")
