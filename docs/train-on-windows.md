# Fine-tune the classifier on a Windows PC with an NVIDIA GPU

Takes about 45–60 minutes on an RTX 3080 (v2: about 10,000 training texts, including 2025–26 models and Claude). The new model is kept only if it beats the current one
on held-out tests (rule in `validation/finetune_classifier.py`).

## 1. Install once

1. Update the NVIDIA driver (NVIDIA App or GeForce Experience).
2. Python 3.12 from python.org. Tick **"Add python.exe to PATH"**.
3. Git for Windows from git-scm.com.

## 2. Run these in PowerShell, one at a time

```powershell
git clone https://github.com/Daniil15DDD/quillcheck
cd quillcheck
py -3.12 -m venv .venv
.venv\Scripts\activate
pip install -r requirements.txt
```

The last line installs a CPU-only PyTorch on Windows. Replace it with the GPU version: go to
**pytorch.org → Get Started**, choose *Stable · Windows · Pip · Python · newest CUDA*, copy the
command and add `--force-reinstall` at the end, for example:

```powershell
pip install torch --index-url https://download.pytorch.org/whl/cu128 --force-reinstall
```

Check the GPU is used. This should print `True NVIDIA GeForce RTX 3080`:

```powershell
python -c "import torch; print(torch.cuda.is_available(), torch.cuda.get_device_name(0))"
```

Download the data, build the set and train:

```powershell
python validation\download_data.py
python validation\build_eval_set.py
python validation\finetune_classifier.py
```

It ends with a comparison table and **ADOPTED** or **NOT ADOPTED**.
If it fails with "CUDA out of memory", the settings need to be smaller.

## 3. Copy the result back to the Mac

Use a USB stick, OneDrive or Google Drive.

- Always: `validation\finetune_results.json` → `~/ai-text-checker/validation/`
- Only if ADOPTED: the whole `models\quillcheck-classifier` folder (about 1.7 GB) → `~/ai-text-checker/models/`,
  then restart the app and the Word add-in server.
