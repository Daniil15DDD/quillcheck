#!/bin/sh
# One-time setup: create .venv, install the packages and download the three models
# (about 7.5 GB in the Hugging Face cache). This is the only step that needs the internet;
# the app itself runs offline afterwards.
set -eu
cd "$(dirname "$0")"

if command -v uv >/dev/null 2>&1; then
  uv venv --python 3.12 .venv
  uv pip install --python .venv/bin/python -r requirements.txt
else
  PY="$(command -v python3.12 || true)"
  [ -n "$PY" ] || { echo "Needs Python 3.12 (or uv: https://docs.astral.sh/uv/)." >&2; exit 1; }
  "$PY" -m venv .venv
  .venv/bin/python -m pip install --upgrade pip
  .venv/bin/python -m pip install -r requirements.txt
fi

echo "Downloading models (first time only)…"
HF_HUB_OFFLINE=0 TRANSFORMERS_OFFLINE=0 .venv/bin/python - <<'EOF'
from huggingface_hub import snapshot_download

for repo in ("Qwen/Qwen2.5-1.5B", "Qwen/Qwen2.5-1.5B-Instruct", "desklib/ai-text-detector-v1.01"):
    print("  ", repo)
    snapshot_download(repo)
EOF

echo "Done. Start the app with ./run.sh, then open http://127.0.0.1:8502"
