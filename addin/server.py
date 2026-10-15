"""Local HTTPS server for the Word add-in (task pane + JSON API). Serves on localhost only.

Word for the web loads add-ins over HTTPS, so this uses the certificate made by
`npx office-addin-dev-certs install` (~/.office-addin-dev-certs). Model work runs in the
same short-lived worker processes as the Streamlit app, one task at a time. Text sent to
the API is processed in memory and never saved or logged.
"""
from __future__ import annotations

import json
import os
import ssl
import sys
import threading
from http.server import SimpleHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

os.environ["HF_HUB_OFFLINE"] = "1"
os.environ["TRANSFORMERS_OFFLINE"] = "1"

HERE = Path(__file__).resolve().parent
sys.path.insert(0, str(HERE.parent))

import calibration  # noqa: E402
import worker  # noqa: E402

HOST, PORT = "localhost", int(os.environ.get("AITC_PORT", 3000))
CERT_DIR = Path(os.environ.get("AITC_CERT_DIR", Path.home() / ".office-addin-dev-certs"))
MAX_WORDS = 6000
_STATUS_ORDER = ["unchanged", "edited", "review", "kept-original"]
_model_lock = threading.Lock()  # one model task at a time keeps memory use flat


def scan(text: str) -> dict:
    result = worker.call("detector", "analyse", {"text": text, "detailed": True})
    # Same relative highlighting as the Check tab: the most machine-like quarter *within this text*.
    scored = sorted(s.binoculars for s in result.sentences if s.binoculars is not None)
    cutoff = scored[len(scored) // 4] if len(scored) >= 4 else None
    rank = {v: i + 1 for i, v in reversed(list(enumerate(scored)))}
    sentences = []
    for s in result.sentences:
        p = next(p for p in result.paragraphs if p.start <= s.start < p.end)
        sentences.append({
            "index": s.index,
            "paragraph": p.index,
            "text": s.text,
            "binoculars": s.binoculars,
            "rank": rank.get(s.binoculars) if s.binoculars is not None else None,
            "flagged": cutoff is not None and s.binoculars is not None and s.binoculars <= cutoff,
        })
    context = calibration.binoculars_context(result.binoculars) if result.binoculars is not None else None
    return {
        "words": result.words,
        "binoculars": result.binoculars,
        "classifier": result.classifier,
        "warnings": result.warnings,
        "calibration": {"severity": context[0], "message": context[1]} if context else None,
        "scored": len(scored),
        "sentences": sentences,
    }


def rewrite(texts: list[str], mode: str) -> dict:
    """Rewrite each text separately: each becomes its own paragraph, so chunks map back by index."""
    import rewriter

    clean = [" ".join(t.split()) for t in texts]
    res = worker.call("rewriter", "rewrite", {"text": "\n\n".join(clean), "mode": mode})
    merged: dict[int, dict] = {}
    for c in res.chunks:
        item = merged.setdefault(c.paragraph, {"original": "", "suggested": "", "final": "", "status": "unchanged", "issues": []})
        for key in ("original", "suggested", "final"):
            item[key] = (item[key] + " " + getattr(c, key)).strip()
        item["issues"] += [{"severity": s, "message": m} for s, m in c.issues]
        if _STATUS_ORDER.index(c.status) > _STATUS_ORDER.index(item["status"]):
            item["status"] = c.status
    items = [merged.get(i, {"original": t, "suggested": t, "final": t, "status": "unchanged", "issues": []}) for i, t in enumerate(clean)]
    for item in items:
        item["diff"] = rewriter.diff_html(item["original"], item["suggested"] or item["original"])
    return {"mode": mode, "items": items}


class Handler(SimpleHTTPRequestHandler):
    def __init__(self, *args, **kwargs):
        super().__init__(*args, directory=str(HERE / "web"), **kwargs)

    def log_message(self, fmt, *args):  # request lines only, never bodies
        sys.stderr.write("%s %s\n" % (self.command, self.path.split("?")[0]))

    def end_headers(self):
        self.send_header("Cache-Control", "no-store")
        super().end_headers()

    def _json(self, status: int, payload: dict) -> None:
        body = json.dumps(payload).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(body)))
        self.end_headers()
        self.wfile.write(body)

    def do_GET(self):
        if self.path == "/api/health":
            return self._json(200, {"ok": True, "busy": _model_lock.locked()})
        return super().do_GET()

    def do_POST(self):
        if self.path not in ("/api/scan", "/api/rewrite"):
            return self._json(404, {"error": "Not found"})
        try:
            req = json.loads(self.rfile.read(int(self.headers.get("Content-Length", 0))) or b"{}")
        except ValueError:
            return self._json(400, {"error": "Bad request"})
        if not _model_lock.acquire(blocking=False):
            return self._json(409, {"error": "Busy with another scan or rewrite. Try again in a moment."})
        try:
            if self.path == "/api/scan":
                text = str(req.get("text", "")).strip()
                if not text:
                    return self._json(400, {"error": "The document is empty."})
                if len(text.split()) > MAX_WORDS:
                    return self._json(413, {"error": f"Over {MAX_WORDS} words. Select a section and scan that instead."})
                return self._json(200, scan(text))
            texts = [str(t) for t in req.get("texts", []) if str(t).strip()]
            if not texts:
                return self._json(400, {"error": "Nothing to rewrite."})
            return self._json(200, rewrite(texts, str(req.get("mode", "academic"))))
        except worker.WorkerError as e:
            hint = {"crashed": " This usually means macOS ran out of memory. Close other apps and try again.",
                    "models-missing": " Model files could not be loaded from the local cache."}.get(e.kind, "")
            return self._json(500, {"error": f"{e}{hint}"})
        except ValueError as e:
            return self._json(400, {"error": str(e)})
        finally:
            _model_lock.release()


def main() -> None:
    cert, key = CERT_DIR / "localhost.crt", CERT_DIR / "localhost.key"
    if not cert.exists() or not key.exists():
        sys.exit(f"No HTTPS certificate in {CERT_DIR}.\nRun this once, then start again:\n  npx office-addin-dev-certs install")
    ctx = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
    ctx.load_cert_chain(cert, key)
    server = ThreadingHTTPServer((HOST, PORT), Handler)
    server.socket = ctx.wrap_socket(server.socket, server_side=True)
    print(f"Word add-in server on https://{HOST}:{PORT} (local only). Ctrl+C to stop.", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass


if __name__ == "__main__":
    main()
