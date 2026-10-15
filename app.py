"""QuillCheck UI. Run with ./run.sh; serves on 127.0.0.1 only."""
import os

os.environ["HF_HUB_OFFLINE"] = "1"  # detector.py enforces this too
os.environ["TRANSFORMERS_OFFLINE"] = "1"

import html
import io
from collections import Counter

import streamlit as st
from docx import Document

import calibration
import detector
import likelihood
import rewriter
import worker

DISCLAIMER = (
    "**Model estimates only — not evidence of who wrote a text.** "
    "Everything runs on this Mac. Text is not saved or sent anywhere. "
    "Scores are not comparable with GPTZero or any other detector."
)

st.set_page_config(page_title="QuillCheck", page_icon="🪶", layout="wide")
st.title("🪶 QuillCheck")
st.caption(DISCLAIMER)


def read_input(key: str) -> str:
    text = st.text_area("Paste text", height=260, key=f"{key}_text")
    upload = st.file_uploader("…or upload a .docx or .txt file", type=["docx", "txt"], key=f"{key}_file")
    if upload is not None:
        try:
            if upload.name.lower().endswith(".docx"):
                doc = Document(io.BytesIO(upload.getvalue()))
                text = "\n\n".join(p.text for p in doc.paragraphs if p.text.strip())
            else:
                text = upload.getvalue().decode("utf-8", errors="replace")
        except Exception as e:  # corrupt or mislabelled file
            st.error(f"Could not read {upload.name}: {type(e).__name__}. Paste the text instead.")
            return ""
        st.caption(
            f"Using {len(text.split())} words from **{upload.name}** instead of the text box. "
            "From .docx only body paragraphs are read; tables, text boxes, headers, footers and footnotes are skipped."
        )
    return text.strip()


def run(text: str, detailed: bool) -> detector.Result | None:
    return run_in_worker("Analysing locally…", lambda p: detector.analyse_in_subprocess(text, detailed=detailed, progress=p))


def run_in_worker(label: str, call):
    error = None
    with st.status(label, expanded=False) as status:
        try:
            result = call(lambda msg: status.update(label=msg))
            status.update(label="Done", state="complete")
        except worker.WorkerError as e:
            status.update(label="Failed", state="error")
            error, result = e, None
    # Shown outside the collapsed status box so the explanation is always visible.
    if error is not None:
        if error.kind == "models-missing":
            st.error(f"Model files could not be loaded from the local cache ({error}). The app is offline by design; re-run the one-time model download, then try again.")
        elif error.kind == "crashed":
            st.error(f"{error} This usually means macOS ran out of memory. Close other apps or check a shorter text.")
        else:
            st.error(f"The model run failed ({error}). If this mentions memory, close other apps or check a shorter text.")
    return result


def sentence_view(result: detector.Result) -> str:
    """Highlight sentences in AI-looking paragraphs (see likelihood.flag_sentences)."""
    scored = sorted(s.binoculars for s in result.sentences if s.binoculars is not None)
    flags = likelihood.flag_sentences(result)
    rank = {v: i + 1 for i, v in reversed(list(enumerate(scored)))}
    parts, last_paragraph = [], 0
    for s in result.sentences:
        paragraph = next(p.index for p in result.paragraphs if p.start <= s.start < p.end)
        if paragraph != last_paragraph:
            parts.append("<br><br>")
            last_paragraph = paragraph
        body = html.escape(s.text)
        if s.binoculars is None:
            parts.append(f'<span style="opacity:.6" title="too short to score">{body}</span> ')
        else:
            tip = f"Relative rank {rank[s.binoculars]} of {len(scored)} in this text (1 = most AI-like here). Raw Binoculars {s.binoculars:.3f}. Not a probability."
            if s.index in flags:
                parts.append(f'<mark style="background:#f6d365;color:#000;border-radius:3px" title="{tip}">{body}</mark> ')
            else:
                parts.append(f'<span title="{tip}">{body}</span> ')
    return '<div style="line-height:1.7">' + "".join(parts) + "</div>"


REWRITE_NOTICE = (
    "**AI-assisted editing — review every change before you use it.** A small local model makes these suggestions; "
    "it can still change meaning or make mistakes. It is not designed, tuned or tested to change AI-detector results "
    "and makes no claim about GPTZero or any other detector. If you use it for assessed work, follow your university's "
    "rules on AI use and acknowledge it where required."
)
STATUS_LABELS = {
    "edited": "Edited",
    "review": "Edited — read the notes",
    "kept-original": "Kept your original (a check failed) — suggestion shown for reference only",
    "unchanged": "No changes suggested",
}
REWRITE_CSS = """<style>
table.rw{width:100%;border-collapse:collapse;table-layout:fixed}
table.rw th,table.rw td{vertical-align:top;padding:.6rem;border-top:1px solid rgba(128,128,128,.3);line-height:1.6;word-wrap:break-word}
table.rw th{text-align:left}
del.rw-del{background:rgba(255,80,80,.22);text-decoration:line-through}
ins.rw-ins{background:rgba(40,180,80,.25);text-decoration:none}
.rw-status{margin-top:.4rem;font-size:.85em;font-weight:600}
.rw-kept-original{color:#c0392b}.rw-review{color:#b9770e}
ul.rw-notes{margin:.2rem 0 0 1rem;font-size:.85em}
</style>"""


def rewrite_view(res: rewriter.RewriteResult) -> None:
    counts = Counter(c.status for c in res.chunks)
    st.caption(
        f"**What was processed:** all {res.paragraphs} paragraph(s), {res.words_in} words, in {len(res.chunks)} chunk(s) of up to "
        f"{rewriter.CHUNK_TOKENS} tokens (long paragraphs are split between sentences). Nothing was truncated. "
        f"Style: {rewriter.MODES[res.mode][0]}."
    )
    st.write(
        f"Edited: **{counts['edited']}** · Edited, read the notes: **{counts['review']}** · "
        f"Kept original: **{counts['kept-original']}** · No changes: **{counts['unchanged']}**"
    )
    if counts["kept-original"]:
        st.warning(
            f"{counts['kept-original']} chunk(s) keep your original wording because a check failed "
            "(a number, citation or link was lost or added, or the output was cut off). The model's version is shown for reference only."
        )
    rows = []
    for c in res.chunks:
        left, right = rewriter.diff_html(c.original, c.suggested or c.original)
        notes = "".join(f"<li>{html.escape(msg)}</li>" for _, msg in c.issues)
        rows.append(
            f"<tr><td>{left}</td><td>{right}<div class='rw-status rw-{c.status}'>{STATUS_LABELS[c.status]}</div>"
            f"{f'<ul class=rw-notes>{notes}</ul>' if notes else ''}</td></tr>"
        )
    st.subheader("Side by side")
    st.caption("Left: your text, with removed or replaced words struck through. Right: the suggestion, with new words highlighted.")
    st.markdown(REWRITE_CSS + "<table class='rw'><tr><th>Original</th><th>Suggestion</th></tr>" + "".join(rows) + "</table>", unsafe_allow_html=True)
    st.subheader("Rewritten text — AI-assisted, review before use")
    st.caption("Copy with the icon at the top right of the box, or download it. Chunks that failed a check keep your original wording here.")
    st.code(res.output, language=None, wrap_lines=True)
    st.download_button("Download as .txt", res.output, file_name="rewritten.txt", mime="text/plain", on_click="ignore")


check, rewrite_tab, calibrate, about = st.tabs(["Check", "Rewrite", "Calibrate", "About"])

with rewrite_tab:
    st.info(REWRITE_NOTICE)
    mode = st.radio("Editing style", list(rewriter.MODES), format_func=lambda k: rewriter.MODES[k][0], horizontal=True, key="rewrite_mode")
    st.caption(rewriter.MODES[mode][1] + " Australian English. Facts, numbers, names and citations must stay unchanged.")
    source = read_input("rewrite")
    if st.button("Rewrite", type="primary", disabled=not source):
        res = run_in_worker("Editing locally…", lambda p: rewriter.rewrite_in_subprocess(source, mode, progress=p))
        if res is not None:
            st.session_state["rewrite_result"] = (source, mode, res)
    stored = st.session_state.get("rewrite_result")
    if stored and stored[0] == source and stored[1] == mode:
        rewrite_view(stored[2])

with check:
    text = read_input("check")
    if st.button("Analyse", type="primary", disabled=not text):
        result = run(text, detailed=True)
        if result is None:
            st.stop()
        for warning in result.warnings:
            st.warning(warning)

        reading = likelihood.reading(result.classifier)
        if reading is None:
            st.info("No estimate: the text was too short to score.")
        else:
            pct, label, severity = reading
            st.metric("Likelihood this was AI-generated", f"{pct}%")
            getattr(st, severity)(f"**{label}**")
            st.progress(pct / 100)
            st.caption(likelihood.CAVEAT)
            human_clf = calibration.scores("human", "classifier")
            if len(human_clf) >= 3:
                st.caption(f"For comparison, your own writing samples scored {human_clf[0] * 100:.0f}%–{human_clf[-1] * 100:.0f}%.")

        with st.expander("How this was worked out (technical details)"):
            left, right = st.columns(2)
            with left:
                st.metric("Classifier score (desklib)", "—" if result.classifier is None else f"{result.classifier * 100:.0f}%")
                st.caption("The number shown above. A model trained on news, books, abstracts, reviews, Reddit, recipes, Wikipedia and poetry, not coursework. 50% is the model card's cut-off.")
            with right:
                st.metric("Binoculars raw score — EXPERIMENTAL (lower = more AI-like)", "—" if result.binoculars is None else f"{result.binoculars:.3f}")
                st.caption("A second, independent check. It has no validated threshold for these models, so it is not used for the headline and only means something next to your own samples.")
                context = calibration.binoculars_context(result.binoculars) if result.binoculars is not None else None
                if context is None:
                    st.caption("Uncalibrated: add at least 3 samples of your own writing in **Calibrate** to compare against.")
                else:
                    getattr(st, context[0])(context[1])
            cov = result.coverage
            if cov:
                st.caption(
                    f"**What was scored:** Binoculars read all {cov.get('binoculars_tokens', 0)} tokens in {cov.get('binoculars_windows', 0)} window(s) of up to "
                    f"{detector.WINDOW_TOKENS} tokens (context does not carry across windows). The classifier read {cov.get('classifier_tokens', 0)} of "
                    f"{cov.get('classifier_total', 0)} tokens in {cov.get('classifier_windows', 0)} window(s) of up to {detector.CLASSIFIER_MAX_TOKENS} tokens "
                    "(only whitespace between paragraphs can be skipped). No text was truncated."
                )

        st.subheader("Sentences")
        st.caption(
            likelihood.HIGHLIGHT_NOTE + " Single-sentence scores are noisy, so a mark is a reason to reread, "
            "**not** a verdict. Hover for the rank. Faded = too short to score."
        )
        if not likelihood.flag_sentences(result):
            st.success("Nothing stands out: no paragraph looked AI-generated, so no sentences are marked.")
        st.markdown(sentence_view(result), unsafe_allow_html=True)

        st.subheader("Paragraphs")
        st.dataframe(
            [
                {
                    "#": p.index + 1,
                    "Starts with": " ".join(p.text.split()[:8]) + "…",
                    "Words": len(p.text.split()),
                    "AI likelihood %": None if p.classifier is None else round(p.classifier * 100),
                }
                for p in result.paragraphs
            ],
            hide_index=True,
            width="stretch",
        )

with calibrate:
    st.write(
        "Score texts whose origin you know, so the Binoculars number has a personal reference point. "
        "Best: 5–10 of your own assignments written **before you used AI tools**. Optionally add a few AI-written samples you generate yourself. "
        "Only the scores are saved (in `calibration.json`), never the text."
    )
    label = st.radio("This text is…", ["human", "ai"], format_func={"human": "My own writing", "ai": "AI-generated (made by me for comparison)"}.get, horizontal=True)
    sample = read_input("calibrate")
    if st.button("Score and add", disabled=not sample):
        result = run(sample, detailed=False)
        if result is not None:
            calibration.add(label, result.binoculars, result.classifier, result.words)
            st.success("Added.")
            for warning in result.warnings:
                st.warning(warning)

    samples = calibration.load()
    if samples:
        st.dataframe(samples, hide_index=True, width="stretch")
        if st.button("Delete all calibration samples"):
            calibration.save([])
            st.rerun()

with about:
    st.markdown(
        f"""
**What it runs** (offline after the first download):

- **Binoculars** (Hans et al., 2024; code BSD-3) with `{detector.OBSERVER_ID}` + `{detector.PERFORMER_ID}` (Apache-2.0).
  It compares how surprising the text is to one model against how surprising one model expects the other to find it.
  The published threshold was tuned for Falcon-7B, so with these smaller models the score only means something against your own calibration samples.
  **Experimental:** this Qwen pair has not been independently validated for Binoculars. The paper's code scores only the first 512 tokens;
  this app scores the whole text in 512-token windows and combines them, which is an extension the paper did not test.
- **`{detector.CLASSIFIER_ID}`** (MIT): a DeBERTa-v3-large classifier trained on the RAID benchmark.

**Rewrite tab:** uses the same downloaded `{rewriter.MODEL_ID}` (Apache-2.0) to suggest edits, offline. Each chunk is checked:
if a number, citation or link is lost or added, or the output is cut off, your original wording is kept. The checks cannot catch
every change in meaning (for example "creates a risk" becoming "could create a risk"), so read every change. The Rewrite tab
never runs the detector on its output and is not designed to change detector results.

**Known limits (detector):**

- Formulaic writing (reports, SWOT and other frameworks, executive summaries, reference-heavy text) looks more "AI-like" to these methods, which raises false positives.
- Perplexity-style detectors flag non-native English writers more often (Liang et al., 2023, *Patterns*).
- Short texts are unreliable. Edited, paraphrased or mixed human/AI text is hard for every detector.
- The two models can disagree. Treat disagreement as "uncertain", not as a tie-break.
- None of this is equivalent to GPTZero, Turnitin or any other product, and none of it can prove authorship.

Device: `{detector.DEVICE}`.
"""
    )
