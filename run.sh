#!/bin/sh
# Start QuillCheck at http://127.0.0.1:8502 (offline, local only).
cd "$(dirname "$0")" || exit 1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec .venv/bin/streamlit run app.py
