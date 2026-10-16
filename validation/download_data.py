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
]
for repo, name in FILES:
    path = hf_hub_download(repo, name, repo_type="dataset", local_dir=os.path.join(OUT, repo.split("/")[1]))
    print(f"{repo}/{name}: {os.path.getsize(path) / 1e6:.1f} MB")
print("model:", snapshot_download("desklib/ai-text-detector-v1.01"))
