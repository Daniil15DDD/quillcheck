#!/bin/sh
# Start the Word add-in server at https://localhost:3000 (offline, local only).
cd "$(dirname "$0")/.." || exit 1
export HF_HUB_OFFLINE=1 TRANSFORMERS_OFFLINE=1
exec .venv/bin/python addin/server.py
