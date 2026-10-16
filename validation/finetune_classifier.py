"""Fine-tune the desklib classifier on validation/data/evalset.jsonl and keep it only if it wins.

Trains on splits "train" + "train_extra" (minus any text that also sits in a test split), with 10%
held back to pick the best epoch. The top half of the encoder and the head are trained; the rest
stays frozen so it fits in 16 GB on Apple Silicon. Both models are then scored on "test_in" and
"test_ood" with the app's own detector.Classifier.score_document, so the comparison is like for like.

Rule (v2, fixed before the v2 run): adopt only if, on "test_new" (current 2025-26 models, incl. Claude),
Brier is lower AND fewer AI texts are missed; AND, summed over all three test sets, no more human texts
are wrongly flagged; AND no more of the 16 newer-model passages are missed. (The v1 rule, Brier/AUROC on
test_in and test_ood only, rejected v1 on 2026-10-16.) If adopted, the model is saved to
models/quillcheck-classifier/ (git-ignored) and detector.py loads it from there.
"""
from __future__ import annotations

import json
import math
import random
import sys
import time
from datetime import date
from pathlib import Path

import numpy as np
import torch
import torch.nn.functional as F

HERE = Path(__file__).resolve().parent
ROOT = HERE.parent
sys.path.insert(0, str(ROOT))
import detector  # noqa: E402

OUT = ROOT / "models" / "quillcheck-classifier"
TESTS = ("test_in", "test_ood", "test_new")
SEED, LR = 20261016, 1e-5
if detector.DEVICE.type == "cuda":  # e.g. RTX 3080 10 GB: full-length texts, two passes
    MAX_LEN, BATCH, ACCUM, EPOCHS, FROZEN_LAYERS = 512, 4, 4, 2, 12
else:  # Apple Silicon 16 GB unified memory: what fits without swapping
    MAX_LEN, BATCH, ACCUM, EPOCHS, FROZEN_LAYERS = 384, 2, 8, 1, 16
random.seed(SEED)
torch.manual_seed(SEED)

rows = [json.loads(line) for line in open(HERE / "data/evalset.jsonl")]
test_texts = {r["text"] for r in rows if r["split"].startswith("test")}
train = [r for r in rows if r["split"] in ("train", "train_extra") and r["text"] not in test_texts]
random.shuffle(train)
n_val = len(train) // 10
val, train = train[:n_val], train[n_val:]
y = lambda rs: [1.0 if r["label"] == "ai" else 0.0 for r in rs]  # noqa: E731
print(f"train {len(train)}  val {len(val)}  (dropped {sum(r['text'] in test_texts for r in rows if r['split'] in ('train', 'train_extra'))} overlapping)", flush=True)

detector.CLASSIFIER_ID = "desklib/ai-text-detector-v1.01"  # baseline is always the original, even if a tuned model exists
clf = detector.Classifier()  # loads desklib onto the app's device; this is also the baseline
tok, model, dev = clf.tokenizer, clf.model, detector.DEVICE


def batches(rs, size):
    for i in range(0, len(rs), size):
        chunk = rs[i:i + size]
        enc = tok([r["text"] for r in chunk], truncation=True, max_length=MAX_LEN, padding=True, return_tensors="pt")
        yield enc["input_ids"].to(dev), enc["attention_mask"].to(dev), torch.tensor(y(chunk), device=dev)


def val_loss() -> float:
    model.eval()
    total = 0.0
    with torch.inference_mode():
        for ids, mask, labels in batches(val, 8):
            total += F.binary_cross_entropy_with_logits(model(ids, mask), labels, reduction="sum").item()
    return total / len(val)


def score_tests() -> dict[str, np.ndarray]:
    """Score test texts exactly as the app does."""
    model.eval()
    out = {}
    for split in TESTS:
        out[split] = np.array([clf.score_document(r["text"], detector.split_spans(r["text"])[0], detailed=False)[0]
                               for r in rows if r["split"] == split])
    return out


t0 = time.time()
baseline = score_tests()
print(f"baseline scored in {time.time() - t0:.0f}s", flush=True)
detector._free()  # hand back scoring caches before training

# Freeze embeddings and the lower layers; train the upper layers and the head.
for p in model.model.embeddings.parameters():
    p.requires_grad = False
for layer in model.model.encoder.layer[:FROZEN_LAYERS]:
    for p in layer.parameters():
        p.requires_grad = False
model.model.gradient_checkpointing_enable()
params = [p for p in model.parameters() if p.requires_grad]
opt = torch.optim.AdamW(params, lr=LR, weight_decay=0.01)
steps = EPOCHS * math.ceil(len(train) / (BATCH * ACCUM))
sched = torch.optim.lr_scheduler.LambdaLR(opt, lambda s: min(1.0, (s + 1) / (0.1 * steps)) * max(0.0, (steps - s) / steps))

best = (val_loss(), None)
print(f"val loss before {best[0]:.4f}", flush=True)
step = 0
for epoch in range(EPOCHS):
    model.train()
    random.shuffle(train)
    for i, (ids, mask, labels) in enumerate(batches(train, BATCH), 1):
        loss = F.binary_cross_entropy_with_logits(model(ids, mask), labels) / ACCUM
        loss.backward()
        if i % ACCUM == 0:
            torch.nn.utils.clip_grad_norm_(params, 1.0)
            opt.step(); sched.step(); opt.zero_grad(); step += 1
            if step % 20 == 0:
                print(f"epoch {epoch + 1} step {step}/{steps}  loss {loss.item() * ACCUM:.4f}  {time.time() - t0:.0f}s", flush=True)
    vl = val_loss()
    print(f"epoch {epoch + 1} val loss {vl:.4f}", flush=True)
    if vl < best[0]:
        best = (vl, {k: v.detach().cpu().clone() for k, v in model.state_dict().items()})
if best[1] is None:
    print("fine-tuning never beat the starting model on validation; nothing to compare", flush=True)
    sys.exit(0)
model.load_state_dict(best[1])
tuned = score_tests()


def auroc(p, t):
    pos, neg = p[t == 1], p[t == 0]
    if not len(pos) or not len(neg):
        return None
    return float(((pos[:, None] > neg[None, :]).sum() + 0.5 * (pos[:, None] == neg[None, :]).sum()) / (len(pos) * len(neg)))


def metrics(p, t):
    flag = p >= 0.5
    return {"n_human": int((t == 0).sum()), "n_ai": int((t == 1).sum()), "FP": int((flag & (t == 0)).sum()),
            "FN": int((~flag & (t == 1)).sum()), "AUROC": auroc(p, t), "Brier": float(((p - t) ** 2).mean())}


results = {"date": date.today().isoformat(), "n_train": len(train), "n_val": len(val), "best_val_loss": best[0],
           "settings": {"max_len": MAX_LEN, "batch": BATCH * ACCUM, "lr": LR, "epochs": EPOCHS, "frozen_layers": FROZEN_LAYERS},
           "sets": {}}
for split in TESTS:
    sel = [r for r in rows if r["split"] == split]
    t = np.array(y(sel))
    res = {}
    for name, p in (("current", baseline[split]), ("finetuned", tuned[split])):
        res[name] = {"ALL": metrics(p, t)}
        key = (lambda r: f'{r["source"]}:{r["group"]}') if split == "test_new" else (lambda r: r["source"])  # per model for new sets
        for src in sorted({key(r) for r in sel}):
            idx = np.array([key(r) == src for r in sel])
            res[name][src] = metrics(p[idx], t[idx])
    results["sets"][split] = res

S = results["sets"]
new_c, new_f = S["test_new"]["current"]["ALL"], S["test_new"]["finetuned"]["ALL"]
adopt = (new_f["Brier"] < new_c["Brier"] and new_f["FN"] < new_c["FN"]
         and sum(S[s]["finetuned"]["ALL"]["FP"] for s in TESTS) <= sum(S[s]["current"]["ALL"]["FP"] for s in TESTS)
         and S["test_ood"]["finetuned"]["newer_model"]["FN"] <= S["test_ood"]["current"]["newer_model"]["FN"])
results["adopted"] = adopt
json.dump(results, open(HERE / "finetune_results.json", "w"), indent=1)
if adopt:
    OUT.mkdir(parents=True, exist_ok=True)
    model.save_pretrained(OUT)
    tok.save_pretrained(OUT)

for s in TESTS:
    print(f"\n== {s}")
    for name in ("current", "finetuned"):
        m = S[s][name]["ALL"]
        print(f"  {name:9} AUROC {m['AUROC']:.3f}  Brier {m['Brier']:.3f}  FP {m['FP']}/{m['n_human']}  FN {m['FN']}/{m['n_ai']}")
    for src in [k for k in S[s]["current"] if k != "ALL"]:
        c, f = S[s]["current"][src], S[s]["finetuned"][src]
        print(f"    {src:15} current FP {c['FP']}/{c['n_human']} FN {c['FN']}/{c['n_ai']}   finetuned FP {f['FP']}/{f['n_human']} FN {f['FN']}/{f['n_ai']}")
print(f"\n{'ADOPTED: saved to ' + str(OUT) if adopt else 'NOT ADOPTED: the app keeps the current classifier'}  ({time.time() - t0:.0f}s)")
