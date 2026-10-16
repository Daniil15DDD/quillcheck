# Validation report: QuillCheck (local AI-text checker)

Date: 2026-10-15. Machine: Apple M4, 16 GB RAM, macOS, PyTorch MPS.
Scripts and score-only results are in `validation/`. No user writing was used anywhere; test texts are public HC3 data or synthetic.

**Scope:** sections 1–7 validate the *detector* (Check, Calibrate). They were written before the Rewrite tab existed, and their findings and limitations still stand. The Rewrite tab was added later and is covered in section 8. It is an editing aid and is **not** linked to the detector.

## Summary

| Item | Result |
|---|---|
| 1. Browser UI | **Pass**: 24/24 automated checks plus a missing-models check, in a real Chrome tab. 3 bugs found and fixed along the way (below) |
| 2. Offline | **Pass**: full checks inside a macOS sandbox that blocks all outbound network; 0 connection attempts by the server or any worker |
| 3. Binoculars audit | Roles and formula match the reference code; tokenizers identical. **Experimental**: Qwen pair unvalidated, raw score only, no Falcon thresholds used |
| 4. Long documents | **Pass** after a fix: every token is scored; the app reports coverage |
| 5. Highlights | Labelled as relative ranks within the text, not probabilities |
| 6. Memory | Peak footprint **8.3 GB** per check (GPU memory included). Memory is fully returned after each check |
| 7. Held-out test | See section 7. **Binoculars missed 12/16 newer-model AI passages**; the classifier wrongly flagged **22/155 human texts** |

## Bugs found by validation (all fixed and re-tested)

1. **Silent truncation.** A single paragraph longer than 768 classifier tokens was cut to its first 768 tokens. Now every paragraph is split into token windows.
2. **Crash with no calibration.** A misplaced `if/else` crashed the Check tab whenever the user had no calibration samples. The first browser run caught it.
3. **Hidden error messages.** Error messages were rendered inside the collapsed status box, so the user only saw "Failed". They now appear outside it.

Also corrected: the earlier memory figure (~0.7 GB) was process RSS only and was wrong. See section 6.

## 1. Browser interface

Method: a script (`validation/ui_test.mjs`) drove a real Chrome tab against the app over the DevTools protocol, while the app ran inside the network-blocking sandbox.

| Area | Checks (all pass in the final run) |
|---|---|
| Empty input | Analyse button disabled; whitespace-only input also disabled |
| Short input (15 words) | "Only 15 words…" warning; both scores shown; "Uncalibrated" note; EXPERIMENTAL label |
| Long input (2,609 words incl. one 1,950-word paragraph) | No exception; both scores; coverage line shows several windows and "No text was truncated"; table and highlights render |
| Repeated checks | 5 consecutive checks give identical scores; server memory flat |
| Calibration | 3 samples stored; file has scores only, no text; Check then shows the personal comparison; "Delete all" empties the file; Check returns to uncalibrated |
| Files | Valid .docx read, with the note that tables/text boxes/headers/footnotes are skipped (table text confirmed absent); corrupt .docx shows "Could not read … BadZipFile" with no crash, and Analyse stays disabled |
| Errors | Missing model files: clear message, no traceback. Worker killed mid-run (as macOS would do on low memory): reported as "stopped unexpectedly", app keeps running (tested via the API, not the browser) |

The calibration test also shows a real risk. After calibrating on just 3 human texts, a different **human** document was labelled "Below every sample of your own writing". A few calibration samples from another style are not a safe reference.

## 2. Offline operation

The whole machine could not be disconnected, because the agent doing the validation needs the network. Instead, each app process ran under `sandbox-exec` with `validation/offline.sb`. That profile denies all outbound network except localhost.

- **Sandbox proof:** inside it, `curl https://huggingface.co` failed (`000`) and Python got `Operation not permitted`. Outside it, the same request returned `200`.
- **Attempt logging:** a Python audit hook (`validation/sitecustomize.py`) recorded every socket connect and DNS lookup in the server and every worker process. A deliberate probe was logged, which shows the hook works in child processes.

| Run | Network attempts |
|---|---|
| Full check, app settings | 0 |
| Full check, shell set `HF_HUB_OFFLINE=0` (after hardening) | 0 |
| Full UI suite, server + all workers (final run) | 0 |
| Control: original code with offline variables unset | 295 attempts to `huggingface.co` (blocked; still worked from cache) |

The control run led to a hardening step. `detector.py` now forces offline mode before any model library loads, and every model load uses `local_files_only=True`. The app can no longer download anything at runtime. Re-downloading the models needs a deliberate manual command.

## 3. Binoculars implementation audit

Checked line by line against `ahans30/Binoculars` (`binoculars/detector.py`, `metrics.py`):

- **Roles match.** Observer = base model (`Qwen2.5-1.5B`), performer = instruct model (`Qwen2.5-1.5B-Instruct`), as in the reference (Falcon-7B / Falcon-7B-Instruct).
- **Formula matches.** Score = mean cross-entropy of the performer on the actual next tokens ÷ mean cross-entropy between the observer's softmax and the performer's log-softmax. Lower = more machine-like.
- **Tokenizers match.** The two Qwen tokenizers have identical 151,665-token vocabularies and give identical IDs on a test string. Neither adds BOS/EOS, so `add_special_tokens=False` is equivalent to the reference. Character offsets rebuild the text exactly, which the sentence mapping relies on.
- **Differences from the reference:**
  1. The reference scores only the **first 512 tokens**. This app scores the **whole text** in 512-token windows and pools them. That is an untested extension.
  2. The reference averages perplexity over T−1 positions and cross-perplexity over T positions. This app uses the same T−1 positions for both.
  3. Float32 maths is done in 128-position slices to save memory. The maths is unchanged, and scores were identical before and after.
- **Thresholds.** The reference thresholds (0.9015 / 0.8536) are for Falcon-7B and **are not used**. The app shows a raw score labelled EXPERIMENTAL and only compares it with the user's own calibration samples.

## 4. Long documents

| | Limit | How longer text is handled |
|---|---|---|
| Binoculars | 512 tokens per window | Whole text split into consecutive windows (1-token overlap so every token after the first is scored once). Context does **not** carry across windows |
| Classifier | 768 tokens (model card) | Whole paragraphs packed into ≤766-token windows; a longer paragraph is split across windows; score = token-weighted mean |
| Sentences / paragraphs | — | Binoculars sentence scores need ≥ 8 tokens; classifier paragraph scores need ≥ 40 words |

Verified on 2,609 words: Binoculars scored all 2,598 tokens (6 windows). The classifier scored 2,562 of 2,566 tokens (4 windows); the 4 unscored tokens are whitespace between paragraphs. The app shows this "What was scored" line on every result.

Not covered: .docx tables, text boxes, headers, footers and footnotes are not read (the app says so when a file is uploaded).

## 5. Sentence highlights

Yellow marks the quarter of sentences with the lowest Binoculars scores **relative to the other sentences in the same text**. Every text with 4+ scored sentences gets a yellow quarter, including fully human text. The heading reads "relative highlights". The tooltip shows "Relative rank N of M in this text … Not a probability."

## 6. Memory (Apple MPS)

Process RSS does not count GPU (Metal) buffers on Apple Silicon. Peak memory footprint (`/usr/bin/time -l`, `footprint`) and MPS driver memory do.

| Measurement | Value |
|---|---|
| Worker peak footprint, 459 words | ~7.8 GB |
| Worker peak footprint, 2,609 words | **8.3 GB** (RSS only 0.71 GB) |
| MPS driver memory peak (in-process run) | 7.24 GiB; tensors 6.32 GiB (≈6.2 GB is the two Qwen models in bf16) |
| Before fixes: one process, 10 checks | footprint crept to 3.1 GB at rest and 8.3 GB peak, ~45 MB/check |
| After fix: each check in a fresh worker process | app server flat at ~332–339 MB at rest across repeats; worker exits and returns all memory |
| Time per check | ~14 s (459 words), ~27 s (2,609 words) |

Close other memory-heavy apps before a check. A check temporarily needs about half of a 16 GB Mac's memory.

## 7. Held-out evaluation

### Design

- **Calibration (threshold only).** HC3 *finance* rows 0–99, human answers ≥ 80 words: 71 texts. The rule was fixed in the script before it ran: threshold = 5th percentile of these human scores (target 5% false positives). Result: **0.9488**. It was computed from calibration scores only and **was not changed after seeing evaluation results**.
- **Evaluation.**
  - HC3 finance rows 100–299 and HC3 open_qa rows 0–199 (human and ChatGPT answers ≥ 80 words).
  - 16 business-coursework-style passages written for this test by Claude (newer-model AI text, all 100% AI).
- **Separation.** Each HC3 row is one question with its answers. Calibration and evaluation use different rows. **Question-text overlap: 0. Exact answer-text overlap: 0.** The two answers to the same evaluation question (human and ChatGPT) both sit in evaluation. The reddit_eli5 rows used during development and the UI tests are excluded entirely.
- **Classifier.** Uses the model card's fixed 0.5 cut-off; no calibration.

### Results

Human false positives = human texts wrongly flagged. AI false negatives = AI texts missed. 95% Wilson intervals.

**HC3 (2023 ChatGPT, Q&A answers, not coursework):**

| Set | Detector | Human false positives | FPR [95% CI] | AI false negatives | FNR [95% CI] | AUROC |
|---|---|---|---|---|---|---|
| HC3 finance | Binoculars | 9 / 131 | 6.9% [3.7–12.5] | 26 / 196 | 13.3% [9.2–18.7] | 0.962 |
| HC3 finance | Classifier | 17 / 131 | 13.0% [8.3–19.8] | 0 / 196 | 0.0% [0.0–1.9] | 0.999 |
| HC3 open_qa | Binoculars | 4 / 24 | 16.7% [6.7–35.9] | 3 / 169 | 1.8% [0.6–5.1] | 0.979 |
| HC3 open_qa | Classifier | 5 / 24 | 20.8% [9.2–40.5] | 0 / 169 | 0.0% [0.0–2.2] | 0.999 |

**Newer-model business passages (16 texts, all AI, 124–144 words):**

| Detector | AI false negatives | FNR [95% CI] |
|---|---|---|
| Binoculars (threshold 0.9488) | **12 / 16** | **75.0% [50.5–89.8]** |
| Classifier (0.5) | 3 / 16 | 18.8% [6.6–43.0] |

These passages have no human counterpart, so no false-positive rate exists for this style. The classifier missed the three reflective, first-person "student" passages (scores 0.16, 0.16, 0.43). The models agreed on only 7 of 16 (44%).

### What this does and does not show

- It does **not** give a general accuracy figure, and especially not one for university coursework. The human texts are finance and general Q&A answers, not student essays. HC3's AI text is 2023 ChatGPT. The newer-AI set is 16 short texts from one model, written by the same system that ran this validation.
- **Binoculars with the small Qwen pair largely fails on newer, polished business text** (75% missed), even though it separates 2023 ChatGPT answers well.
- **The classifier wrongly flags a meaningful share of human answers** (13–21% on HC3). It would likely flag some genuine student work too.
- Sample sizes are small; the open_qa human set has 24 texts. The intervals are wide.
- The 0.9488 threshold was derived from finance Q&A text and is **not** used by the app. The app keeps showing a raw Binoculars score until validated on the user's own writing.

## Not tested

- Any real student coursework, including the user's own writing (excluded on purpose).
- Non-native English writing, which detectors of this kind are known to flag more often.
- Human-edited, paraphrased or mixed human/AI text.
- AI text from other current models (GPT, Gemini, Llama, …) beyond the 16 passages here.
- Texts under 80 words in the held-out test (the app warns below 150).
- .docx content outside body paragraphs.
- Behaviour when memory is already tight (only a simulated worker kill was tested).
- Browsers other than Chrome. Concurrent use by two tabs at once.

## 8. Rewrite tab (added 2026-10-15)

**Model:** `Qwen/Qwen2.5-1.5B-Instruct`, snapshot `989aa7980e4cf806f80c7fef2b1adb7bc71aa306` (Apache-2.0). This is the same file already downloaded for Binoculars; no new download or dependency was needed. It is a `Qwen2ForCausalLM` with a chat template and a 32k context. Decoding is greedy, so the same input and style give the same output.

**How it works**
- Styles: Light proofreading, Plain English, Natural academic. All use Australian English and tell the model to keep facts, numbers, names and citations, and never add references, evidence or personal experiences.
- **No truncation.** Text is processed paragraph by paragraph; paragraphs over 300 tokens are split between sentences. The generation budget per chunk is 2× its input + 64 tokens. A chunk that hits the budget is treated as cut off.
- **Checks on every chunk.**
  - *Error* (the original wording is kept; the suggestion is shown for reference only): a number, citation or link is lost or added; the output is cut off or empty.
  - *Warning* (the suggestion is used, flagged for review): a large length change; possible names lost; new capitalised words; new first-person wording.
  - *Info*: US spellings the model introduced are converted to Australian (only where the US form was not already in the user's own text); line breaks the model added inside a paragraph are removed.
- Shows original and suggestion side by side with word-level highlights, a copy button and a .txt download.
- Labelled "AI-assisted editing — review every change before you use it". It states it is not designed or tested to change detector results and makes no claim about GPTZero.
- It never runs the detector on its output and does not loop rewrite → scan.
- Runs in the same short-lived worker process as the detector (`worker.py`); offline mode is forced.

**Browser test (`validation/ui_rewrite.mjs`), final run: 22/22 passed.** Plus a missing-models check: passed. Detector regression suite: 24/24 passed. Network attempts by the server and all workers inside the sandbox: 0.

| Check | Result |
|---|---|
| Tab, review notice, no detector promise, three styles | Pass |
| Empty input disables Rewrite | Pass |
| Short sample (light): side-by-side table, highlights, `$64.3 billion`, `5.6%`, `13.1`, `(Woolworths Group, 2023)`, `Porter (1985)` all preserved | Pass (~12 s) |
| Copy button copies exactly the rewritten text | Pass (the page's clipboard call was intercepted; the real clipboard was not touched) |
| Download button; results stay on screen after it | Pass |
| Changing style hides results produced in another style | Pass |
| Safety path: "twenty-five… ten percent" in Plain English became digits → original kept, reason shown, output equals the original | Pass |
| Long input: 2,135 words, 37 paragraphs incl. a 548-word one → 39 chunks, 37 paragraphs out, word ratio 1.00 | Pass (~145 s in the browser) |
| Missing model files → clear error, no traceback | Pass |

**Failures found and fixed during testing**
1. US→AU spelling map bug: "analyzed" was not converted. Fixed and unit-tested.
2. The model inserted its own paragraph breaks, so a long text came back with 10 paragraphs instead of 5. Breaks inside a chunk are now removed, with a note.
3. Light proofreading removed 15–25% of words on informal text. The prompt now forbids deletions except accidental repeats. On clean prose the word ratio is now 0.99–1.00. Part of the original drop was an artefact of the pre-tokenised HC3 test text (`do n't`, `" Best Seller "`).

**Memory and speed:** rewrite worker peak footprint **4.2 GB** (RSS 0.86 GB) for 2,135 words, 102 s when run directly. About 17–25 tokens/s on the M4.

**Known limitations (not fixed; read every change)**
- The checks cannot catch every change in meaning. Seen in testing: "This creates a risk" → "This **could** create a risk" (light); "employs" → "employ**ed**" and "last year" → "over the previous year" (academic); "has been focusing on digital sales" → "has increased its online sales penetration" (plain, early prompt).
- The checks are strict about digits. Converting "twenty-five" to "25" is treated as an added number and the original is kept, even though the meaning is the same.
- Name detection is a heuristic based on capital letters. It misses lower-case terms and can flag harmless capitalisation (e.g. "Corporate Social Responsibility").
- Citation detection covers author–year and numeric `[n]` styles. Footnote citations and reference-list entries are not specifically checked.
- Some punctuation follows US conventions, e.g. a full stop inside closing quotation marks.
- A 1.5B model gives modest editing quality. It is an aid for clarity and proofreading, not a replacement for your own revision.
- Not tested: real student writing, non-native English writing, texts with heavy referencing, tables, bullet lists, headings, and texts in other languages.

## 9. Score combiner experiment (2026-10-16): not adopted

**Question.** Can a small model on top of the two detectors (logistic regression on the classifier's logit, the Binoculars score and log word count) give a more trustworthy percentage than the classifier alone?

**Data** (`validation/build_eval_set.py`, fixed seed; texts are downloaded locally and not committed):

- Train and same-kind test (50/50 split): MAGE (Apache-2.0) human texts and texts from its stronger generators, 400; HC3 medicine and wiki_csai, split by question, 160; DAIGT student essays (mirror with no stated licence, used locally for evaluation only), 300.
- New-kind test, never trained on: MAGE GPT-4 set (100 human, 100 AI), GPT-4 paraphrases (100 AI), and the 16 newer-model business passages from section 7.
- The desklib classifier was trained on RAID, so RAID was deliberately not used.

**Rule fixed before the first run:** adopt only if the Brier score is lower on both test sets and AUROC is no more than 0.01 lower on either.

**Results** (`validation/combiner_results.json`; FP = human texts wrongly flagged at 50%, FN = AI texts missed):

| Test set | Scorer | AUROC | Brier | FP | FN |
|---|---|---|---|---|---|
| Same kind (430) | Classifier as-is | 0.985 | 0.054 | 23 / 215 | 6 / 215 |
| Same kind (430) | Combiner | 0.988 | 0.033 | 6 / 215 | 12 / 215 |
| New kind (316) | Classifier as-is | 0.965 | 0.074 | 21 / 100 | 8 / 216 |
| New kind (316) | Combiner | 0.961 | 0.084 | 10 / 100 | 29 / 216 |

On the newer-model passages the combiner missed 8 of 16 (classifier: 3), and on GPT-4 paraphrases 16 of 100 (classifier: 4).

**Decision: not adopted.** It roughly quartered false positives on familiar text, but on new kinds of AI text it mostly learned to answer "human" more often and missed far more. The app keeps the classifier score. A recalibrated classifier alone (logit only) showed the same trade-off.

**Also learned:** the classifier wrongly flagged 21 of 100 human texts in the MAGE GPT-4 set (news, film reviews, abstracts, dialogue summaries), in line with section 7.
