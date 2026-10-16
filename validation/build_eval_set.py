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
import re
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


_DETOK = [(re.compile(r" n't\b"), "n't"), (re.compile(r" ([,.;:!?%)\]])"), r"\1"), (re.compile(r"([(\[$]) "), r"\1"),
           (re.compile(r" (['’](?:s|re|ve|ll|d|m)\b)"), r"\1"), (re.compile(r"``|''"), '"'), (re.compile(r"[ \t]+"), " "),
           (re.compile(r"\n{3,}"), "\n\n")]


def clean(text: str) -> str:
    """Undo tokenisation artefacts (`do n't`, `word ,`). They appear far more in the human texts of
    these datasets, so a model trained on them would learn spacing, not writing."""
    for pat, rep in _DETOK:
        text = pat.sub(rep, text)
    return text.strip()


used: set[str] = set()


def add(source: str, group: str, label: str, text: str, split: str) -> None:
    text = clean(text)
    used.add(text)
    rows.append({"id": f"{source}-{len(rows)}", "source": source, "group": group, "label": label,
                 "split": split, "words": len(text.split()), "text": text})


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
    hc3_rest = globals().setdefault("hc3_rest", [])
    hc3_rest += [(name, q) for q in qs[40:240]]  # never in a test split: used only for fine-tuning

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

# Extra fine-tuning pool ("train_extra"). Built last, so every text already placed in a test split is excluded.
# MAGE's GPT-4 files stay out of training entirely; they are the out-of-domain test.
for name, q in hc3_rest:
    add("hc3", name, "human", next(a for a in q["human_answers"] if keep(a)), "train_extra")
    add("hc3", name, "ai", next(a for a in q["chatgpt_answers"] if keep(a)), "train_extra")
fresh = lambda df: df[~df.text.map(clean).isin(used)]  # noqa: E731
for df, label in ((sample(fresh(mage[mage.label == 1]), 400), "human"),
                  (sample(fresh(mage[(mage.label == 0) & mage.src.str.lower().str.contains("|".join(STRONG))]), 400), "ai")):
    for _, r in df.iterrows():
        add("mage", r.src.split("_")[0], label, r.text, "train_extra")
for df, label in ((sample(fresh(daigt[daigt.label == 0]), 500), "human"), (sample(fresh(daigt[daigt.label == 1]), 500), "ai")):
    for _, r in df.iterrows():
        add("daigt", r.source, label, r.text, "train_extra")

# --- v2: current (2025-26) models. Appended after everything above, so the earlier splits are unchanged. ---
_MARKDOWN = re.compile(r"(^|\n)\s*(#|[-*>•]\s|\d+[.)]\s)|\*\*|```|\|")


def prose(text) -> bool:
    """Plain prose only: headings and bullet lists would be a formatting shortcut, not writing style."""
    return keep(text) and not _MARKDOWN.search(text)


def take(df: pd.DataFrame, n: int, source: str, label: str, split: str, group) -> None:
    df = df[df.text.map(prose) & ~df.text.map(clean).isin(used)]
    for _, r in sample(df, n).iterrows():
        add(source, group(r), label, r.text, split)


# rasbt/human-vs-ai-50k: its own train/test splits; label 1 = AI; generator per row.
for split_file, split, n_h, n_a in (("train", "train_extra", 2900, 1500), ("test", "test_new", 300, 300)):
    rb = pd.read_parquet(D / f"human-vs-ai-50k/data/{split_file}-00000-of-00001.parquet", columns=["text", "label", "generator_model", "source_collection"])
    take(rb[rb.label == 0], n_h, "rasbt", "human", split, lambda r: r.source_collection)
    take(rb[rb.label == 1], n_a, "rasbt", "ai", split, lambda r: r.generator_model)

# ELI5 answers from seven 2026 models (human half not distributed): AI only.
for split_file, split, per_model in (("train", "train_extra", 115), ("test", "test_new", 40)):
    el = pd.read_json(D / f"eli5-human-vs-ai/{split_file}.jsonl", lines=True)
    el = el[(el.label == "ai") & el.text.map(prose)]
    for model, g in el.groupby("model"):
        take(g, per_model, "eli5", "ai", split, lambda r: r.model)

# Claude fiction (training only), cut into essay-length passages at paragraph breaks.
claude = pd.read_parquet(D / "RL-Claude-Creative-Writing-SFT/data/train.parquet")
passages = []
for story in claude.output:
    chunk = []
    for para in (p.strip() for p in story.split("\n\n")):
        if len(para.split()) < 6:  # titles, dates, salutations
            continue
        chunk.append(para)
        if len(" ".join(chunk).split()) >= 180:
            passages.append("\n\n".join(chunk))
            chunk = []
take(pd.DataFrame({"text": passages}), 600, "claude_fiction", "ai", "train_extra", lambda r: "claude")

# HAP-E: human continuations (fiction, news, blogs, academic, spoken, TV) and GPT-4o continuations of the same texts.
def trim(text: str, limit: int = 400) -> str:
    """Cut at the last sentence end within `limit` words. Both sides get the same cut, because
    GPT-4o's continuations run longer than the human ones and length would otherwise give it away."""
    words = text.split()
    if len(words) <= limit:
        return text
    cut = " ".join(words[:limit])
    return cut[: max(cut.rfind(". "), cut.rfind("? "), cut.rfind("! ")) + 1] or cut


hape = lambda f: pd.read_parquet(D / f"human-ai-parallel-corpus/text_data/hape-text_{f}.parquet").assign(text=lambda d: d.text.map(trim))  # noqa: E731
take(hape("human-chunk-2"), 700, "hape", "human", "train_extra", lambda r: r.doc_id.split("_")[0])
take(hape("gpt-4o-2024-08-06"), 350, "hape", "ai", "train_extra", lambda r: "gpt-4o")
take(hape("gpt-4o-mini-2024-07-18"), 350, "hape", "ai", "train_extra", lambda r: "gpt-4o-mini")

# textsightai 2026 benchmark (test only): Claude Opus 5, Claude Haiku 4.5, GPT-4.1, gpt-oss, Qwen vs careful human sets.
for name in ("ai_generated_2026-09-29", "human_wikipedia_pre2022", "human_pmc_ccby_nonanglophone", "human_literature_publicdomain"):
    ts = pd.read_json(D / f"ai-text-detection-benchmark-2026/data/{name}.jsonl", lines=True)
    for _, r in ts[ts.text.map(keep)].iterrows():
        add("textsight", r.source, r.label, r.text, "test_new")

with open(D / "evalset.jsonl", "w") as f:
    for r in rows:
        f.write(json.dumps(r) + "\n")
counts = pd.DataFrame(rows).groupby(["split", "source", "label"]).size()
print(counts.to_string())
print("total", len(rows))
