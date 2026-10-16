"""Build the labelled evaluation set for the score combiner from public datasets.

Reads the downloads in validation/data/ (git-ignored) and writes validation/data/evalset.jsonl.
Splits are fixed by a seed before any scoring, so nothing here can be tuned on results:
- train / test_in: MAGE (stronger generators only), HC3 medicine + wiki_csai (split by question),
  DAIGT student essays. Each group is split 50/50.
- test_ood (never trained on): MAGE GPT-4 set, GPT-4 paraphrases, the 16 newer-model passages.
Labels: MAGE uses 1 = human, 0 = AI (checked against its source names); DAIGT uses 1 = AI.
"""
from __future__ import annotations

import json
import random
from pathlib import Path

import pandas as pd

D = Path(__file__).with_name("data")
SEED = 20261016
MIN_WORDS, MAX_WORDS = 80, 450
STRONG = ("gpt", "davinci", "65b", "30b", "flan_t5_xxl", "glm130b", "bloom_7b")

rng = random.Random(SEED)
rows: list[dict] = []


def keep(text: str) -> bool:
    return isinstance(text, str) and MIN_WORDS <= len(text.split()) <= MAX_WORDS


def add(source: str, group: str, label: str, text: str, split: str) -> None:
    rows.append({"id": f"{source}-{len(rows)}", "source": source, "group": group, "label": label,
                 "split": split, "words": len(text.split()), "text": text.strip()})


def sample(df: pd.DataFrame, n: int) -> pd.DataFrame:
    return df.sample(n=min(n, len(df)), random_state=SEED)


def halves(items: list) -> list[tuple[object, str]]:
    items = list(items)
    rng.shuffle(items)
    cut = len(items) // 2
    return [(x, "train") for x in items[:cut]] + [(x, "test_in") for x in items[cut:]]


# MAGE main test set: 200 human + 200 AI from stronger generators, mixed domains.
mage = pd.read_csv(D / "MAGE/test.csv")
mage = mage[mage.text.map(keep)]
human = sample(mage[mage.label == 1], 200)
ai = sample(mage[(mage.label == 0) & mage.src.str.lower().str.contains("|".join(STRONG))], 200)
for df, label in ((human, "human"), (ai, "ai")):
    for (_, r), split in halves(list(df.iterrows())):
        add("mage", r.src.split("_")[0], label, r.text, split)

# HC3 medicine + wiki_csai: split by question so a question's answers never straddle splits.
for name in ("medicine", "wiki_csai"):
    qs = [json.loads(line) for line in open(D / f"HC3/{name}.jsonl")]
    qs = [q for q in qs if any(map(keep, q["human_answers"])) and any(map(keep, q["chatgpt_answers"]))]
    rng.shuffle(qs)
    for q, split in halves(qs[:40]):
        add("hc3", name, "human", next(a for a in q["human_answers"] if keep(a)), split)
        add("hc3", name, "ai", next(a for a in q["chatgpt_answers"] if keep(a)), split)

# DAIGT student essays: 150 human (PERSUADE corpus) + 150 AI across generators.
daigt = pd.read_parquet(D / "kaggle-comp-daigt/data/train-00000-of-00001.parquet", columns=["text", "label", "source"])
daigt = daigt[daigt.text.map(keep)]
for df, label in ((sample(daigt[daigt.label == 0], 150), "human"), (sample(daigt[daigt.label == 1], 150), "ai")):
    for (_, r), split in halves(list(df.iterrows())):
        add("daigt", r.source, label, r.text, split)

# Out-of-domain tests: MAGE GPT-4 (human + AI), GPT-4 paraphrases (AI), newer-model passages (AI).
ood = pd.read_csv(D / "MAGE/test_ood_set_gpt.csv")
ood = ood[ood.text.map(keep)]
for df, label in ((sample(ood[ood.label == 1], 100), "human"), (sample(ood[ood.label == 0], 100), "ai")):
    for _, r in df.iterrows():
        add("mage_gpt4", r.src.split("_")[0], label, r.text, "test_ood")
para = pd.read_csv(D / "MAGE/test_ood_set_gpt_para.csv")
para = para[para.text.map(keep) & para.src.str.contains("gpt4_para")]
for _, r in sample(para, 100).iterrows():
    add("mage_gpt4_para", r.src.split("_")[0], "ai", r.text, "test_ood")
for item in json.load(open(Path(__file__).with_name("modern_ai.json"))):
    add("newer_model", "business", "ai", item["text"], "test_ood")

with open(D / "evalset.jsonl", "w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")
counts = pd.DataFrame(rows).groupby(["split", "source", "label"]).size()
print(counts.to_string())
print("total", len(rows))
