"""Download what the fine-tune needs: the public datasets (into validation/data/, git-ignored)
and the desklib classifier it starts from (into the Hugging Face cache). Needs internet once.

Then: python validation/build_eval_set.py && python validation/finetune_classifier.py
"""
import os

os.environ["HF_HUB_OFFLINE"] = "0"
from huggingface_hub import hf_hub_download, snapshot_download  # noqa: E402

OUT = os.path.join(os.path.dirname(os.path.abspath(__file__)), "data")
FILES = [
    ("yaful/MAGE", "test.csv"),  # Apache-2.0
    ("yaful/MAGE", "test_ood_set_gpt.csv"),
    ("yaful/MAGE", "test_ood_set_gpt_para.csv"),
    ("Hello-SimpleAI/HC3", "medicine.jsonl"),  # CC BY-SA 4.0
    ("Hello-SimpleAI/HC3", "wiki_csai.jsonl"),
    ("Yunij/kaggle-comp-daigt", "data/train-00000-of-00001.parquet"),  # no licence stated: local evaluation only
    # v2: current (2025-26) models
    ("rasbt/human-vs-ai-50k", "data/train-00000-of-00001.parquet"),  # per-text CC BY / CC BY-SA / CC0
    ("rasbt/human-vs-ai-50k", "data/test-00000-of-00001.parquet"),
    ("mild-rgb/eli5-human-vs-ai", "train.jsonl"),  # CC BY 4.0; AI answers from 7 models
    ("mild-rgb/eli5-human-vs-ai", "test.jsonl"),
    ("SLoonker/RL-Claude-Creative-Writing-SFT", "data/train.parquet"),  # MIT; Claude fiction
    ("browndw/human-ai-parallel-corpus", "text_data/hape-text_human-chunk-2.parquet"),  # MIT
    ("browndw/human-ai-parallel-corpus", "text_data/hape-text_gpt-4o-2024-08-06.parquet"),
    ("browndw/human-ai-parallel-corpus", "text_data/hape-text_gpt-4o-mini-2024-07-18.parquet"),
    ("textsightai/ai-text-detection-benchmark-2026", "data/ai_generated_2026-09-29.jsonl"),  # CC BY-SA 4.0; test only
    ("textsightai/ai-text-detection-benchmark-2026", "data/human_wikipedia_pre2022.jsonl"),
    ("textsightai/ai-text-detection-benchmark-2026", "data/human_pmc_ccby_nonanglophone.jsonl"),
    ("textsightai/ai-text-detection-benchmark-2026", "data/human_literature_publicdomain.jsonl"),
]
for repo, name in FILES:
    path = hf_hub_download(repo, name, repo_type="dataset", local_dir=os.path.join(OUT, repo.split("/")[1]))
    print(f"{repo}/{name}: {os.path.getsize(path) / 1e6:.1f} MB")
print("model:", snapshot_download("desklib/ai-text-detector-v1.01"))
