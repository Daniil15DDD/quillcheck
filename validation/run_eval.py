"""Held-out evaluation. Threshold chosen on calibration humans only; evaluation rows never used for any choice."""
import json, math, sys, time
sys.path.insert(0, sys.argv[1]); import detector
E = sys.argv[2]
split = json.load(open(f"{E}/hc3_split.json"))
cal = split["calibration_human"]
ev = split["eval"] + json.load(open(f"{E}/modern_ai.json"))

def bino_scores(b, items):
    out = []
    for it in items:
        ppl, xppl, starts = b.token_scores(it["text"])
        out.append(sum(ppl) / sum(xppl))
    return out

t0 = time.time()
b = detector.Binoculars()
cal_b = bino_scores(b, cal); ev_b = bino_scores(b, ev)
b.close()
c = detector.Classifier()
ev_c = []
for it in ev:
    paragraphs, _ = detector.split_spans(it["text"])
    ev_c.append(c.score_document(it["text"], paragraphs, detailed=False)[0])
c.close()

cal_sorted = sorted(cal_b)
thr = cal_sorted[max(0, math.floor(0.05 * len(cal_sorted)) - 1)]  # ~5th percentile of calibration humans

def wilson(k, n, z=1.96):
    if n == 0: return (float("nan"), float("nan"))
    p = k / n; d = 1 + z * z / n; c0 = (p + z * z / (2 * n)) / d; h = z * math.sqrt(p * (1 - p) / n + z * z / (4 * n * n)) / d
    return (max(0, c0 - h), min(1, c0 + h))
def auroc(pos, neg):
    if not pos or not neg: return None
    return sum((p > n) + 0.5 * (p == n) for p in pos for n in neg) / (len(pos) * len(neg))

report = {"threshold_binoculars": thr, "calibration_n": len(cal), "calibration_range": [cal_sorted[0], cal_sorted[-1]], "sets": {}, "seconds": time.time() - t0}
sets = sorted({it["set"] for it in ev})
for s in sets + ["ALL"]:
    idx = [i for i, it in enumerate(ev) if s == "ALL" or it["set"] == s]
    H = [i for i in idx if ev[i]["label"] == "human"]; A = [i for i in idx if ev[i]["label"] == "ai"]
    r = {"n_human": len(H), "n_ai": len(A)}
    for name, scores, flag in (("binoculars", ev_b, lambda v: v < thr), ("classifier", ev_c, lambda v: v >= 0.5)):
        fp = sum(flag(scores[i]) for i in H); fn = sum(not flag(scores[i]) for i in A)
        r[name] = {"FP": fp, "FPR": fp / len(H) if H else None, "FPR_CI": wilson(fp, len(H)),
                   "FN": fn, "FNR": fn / len(A) if A else None, "FNR_CI": wilson(fn, len(A))}
    r["binoculars"]["AUROC"] = auroc([-ev_b[i] for i in A], [-ev_b[i] for i in H])
    r["classifier"]["AUROC"] = auroc([ev_c[i] for i in A], [ev_c[i] for i in H])
    both = [i for i in idx if (ev_b[i] < thr) == (ev_c[i] >= 0.5)]
    r["models_agree_rate"] = len(both) / len(idx)
    report["sets"][s] = r
report["per_text"] = [{"src": it["src"], "set": it["set"], "label": it["label"], "words": len(it["text"].split()), "binoculars": ev_b[i], "classifier": ev_c[i]} for i, it in enumerate(ev)]
json.dump(report, open(f"{E}/eval_results.json", "w"), indent=1)
print(json.dumps({k: v for k, v in report.items() if k != "per_text"}, indent=1))
