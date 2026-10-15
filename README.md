# Local AI-text checker

A private, offline tool for checking your own writing. It gives model estimates of how
machine-like a text reads, highlights the sentences that stand out most *within that text*,
and can suggest plain-English or academic-style edits. Everything runs on your own computer.
No text is uploaded, there is no account, and it costs nothing to run.

> **Estimates only, not evidence of who wrote a text.** These models make mistakes in both
> directions (see [VALIDATION.md](VALIDATION.md)). Scores are not comparable with GPTZero,
> Turnitin or any other detector. The editing feature is not designed or tested to change
> detector results. If you use it for assessed work, follow your institution's rules on AI use.

## What's in it

- **Check:** two independent estimates.
  - [Binoculars](https://arxiv.org/abs/2401.12070) (zero-shot, with a Qwen2.5-1.5B base/instruct pair).
  - The desklib DeBERTa classifier.

  It also shows sentence highlights relative to the rest of the text.
- **Calibrate:** add a few samples of your own past writing so the Binoculars score has a personal baseline. Only scores are saved, never text.
- **Rewrite:** suggested edits from a small local model (light proofreading, plain English or natural academic), shown as a diff. Numbers, citations and links are checked so they aren't lost or invented.
- **Word add-in:** the same checks in a side panel inside Word (Mac desktop or Word in the browser). See [addin/README.md](addin/README.md).

## Requirements

- An Apple Silicon Mac (tested on an M4 with 16 GB RAM) or a machine with a CUDA GPU. It also runs on CPU only, but a check can take minutes.
- About 8 GB of free disk space for the models, and about 8 GB of free RAM while a check runs.
- Python 3.12. [uv](https://docs.astral.sh/uv/) is recommended.

## Setup (once)

```
git clone <this repo> ai-text-checker
cd ai-text-checker
./setup.sh
```

`setup.sh` creates `.venv`, installs the packages in `requirements.txt` and downloads the
three models (about 7.5 GB). This is the only step that uses the internet.

## Run

```
./run.sh
```

Then open http://127.0.0.1:8502. The app only listens on your own machine and runs with
Hugging Face offline mode on.

For the Word add-in, follow [addin/README.md](addin/README.md).

## Models

| Model | Used for | Licence |
|---|---|---|
| [Qwen/Qwen2.5-1.5B](https://huggingface.co/Qwen/Qwen2.5-1.5B) | Binoculars observer | Apache-2.0 |
| [Qwen/Qwen2.5-1.5B-Instruct](https://huggingface.co/Qwen/Qwen2.5-1.5B-Instruct) | Binoculars performer, Rewrite | Apache-2.0 |
| [desklib/ai-text-detector-v1.01](https://huggingface.co/desklib/ai-text-detector-v1.01) | Classifier | MIT |

Models are downloaded from Hugging Face by `setup.sh`. They are not included in this repository.

## Known limits

- Binoculars thresholds were published for Falcon-7B, not this Qwen pair. The raw score only means something next to your own calibration samples.
- Both models score formulaic writing, writing by non-native English speakers, and well-known older texts as more machine-like.
- Highlights are relative. Every text has a "most machine-like quarter", even one written entirely by a person.
- On held-out tests, Binoculars missed most passages from newer AI models and the classifier flagged some human texts. Full results are in [VALIDATION.md](VALIDATION.md).

## Project layout

```
app.py            Streamlit UI (Check, Rewrite, Calibrate, About)
detector.py       Binoculars + classifier scoring
rewriter.py       Local editing model and its safety checks
worker.py         Runs each model task in a short-lived process
calibration.py    Personal calibration (scores only)
addin/            Word add-in (local HTTPS server + task pane)
desktop/          Optional macOS app wrapper (needs pywebview)
validation/       Test scripts and score-only results
```
