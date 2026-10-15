"""Desktop window for the local AI-text checker (personal use on this Mac).

Starts the existing Streamlit app on 127.0.0.1 through server_guard.py, waits until it
answers, then shows it in a native window. Quitting (Cmd+Q or closing the window) stops
only the process group this launch started: the server and its model workers.

Depends on this project's .venv and the Hugging Face cache already on this Mac;
it is not a standalone distribution. run.sh still works as a browser fallback.
"""
from __future__ import annotations

import atexit
import fcntl
import html
import os
import signal
import socket
import subprocess
import sys
import threading
import time
import urllib.request
from pathlib import Path

import webview

APP_NAME = "AI Text Checker"
PROJECT = Path(__file__).resolve().parent.parent
PYTHON = PROJECT / ".venv" / "bin" / "python"
SUPPORT = Path.home() / "Library" / "Application Support" / APP_NAME
LOG = Path.home() / "Library" / "Logs" / APP_NAME / "server.log"
ICON = Path(__file__).with_name("icon.png")
PREFERRED_PORT = 8502  # same as run.sh; the next free port is used if it is taken
STARTUP_TIMEOUT = 90
MODELS = ["Qwen/Qwen2.5-1.5B", "Qwen/Qwen2.5-1.5B-Instruct", "desklib/ai-text-detector-v1.01"]  # detector.py IDs

guard: subprocess.Popen | None = None
window: webview.Window | None = None
_lock_file = None


# ---------- pages shown in the window before / instead of the app ----------

PAGE = """<!doctype html><html><head><meta charset="utf-8"><style>
:root{{color-scheme:light dark;--bg:#fff;--fg:#1d1d1f;--muted:#6e6e73;--box:#f5f5f7;--err:#c0392b}}
@media (prefers-color-scheme:dark){{:root{{--bg:#1e1e1e;--fg:#f5f5f7;--muted:#a1a1a6;--box:#2c2c2e;--err:#ff6b5e}}}}
body{{background:var(--bg);color:var(--fg);font:15px -apple-system,system-ui,sans-serif;margin:0;padding:48px 40px;line-height:1.5}}
h1{{font-size:22px;margin:0 0 8px}} p{{color:var(--muted);margin:6px 0}} .err h1{{color:var(--err)}}
pre{{background:var(--box);padding:12px;border-radius:8px;white-space:pre-wrap;font-size:12px;max-height:50vh;overflow:auto}}
.spin{{width:28px;height:28px;border:3px solid var(--box);border-top-color:var(--muted);border-radius:50%;animation:s 1s linear infinite;margin-bottom:20px}}
@keyframes s{{to{{transform:rotate(360deg)}}}} a{{color:inherit}}
</style></head><body class="{cls}">{body}</body></html>"""


def loading_page() -> str:
    return PAGE.format(cls="", body='<div class="spin"></div><h1>Starting the local checker…</h1>'
                       "<p>Everything runs on this Mac. This usually takes a few seconds.</p>")


def error_page(title: str, detail: str, extra: str = "") -> str:
    return PAGE.format(cls="err", body=f"<h1>{html.escape(title)}</h1><p>{html.escape(detail)}</p>{extra}"
                       f"<p>Fallback: run <code>{html.escape(str(PROJECT / 'run.sh'))}</code> in Terminal. "
                       f"Server log: <code>{html.escape(str(LOG))}</code></p>")


def log_tail(lines: int = 40) -> str:
    try:
        return "<pre>" + html.escape("\n".join(LOG.read_text(errors="replace").splitlines()[-lines:])) + "</pre>"
    except OSError:
        return ""


# ---------- single instance, ports, models ----------

def acquire_single_instance() -> bool:
    """Only one launcher at a time (Finder already reuses a running app; this covers Terminal launches)."""
    global _lock_file
    SUPPORT.mkdir(parents=True, exist_ok=True)
    _lock_file = open(SUPPORT / "app.lock", "w")
    try:
        fcntl.flock(_lock_file, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except BlockingIOError:
        return False
    return True


def stop_stale_server() -> None:
    """If a previous launch was killed together with its guard, stop what it left behind."""
    pid_file = SUPPORT / "server.pgid"
    try:
        pgid = int(pid_file.read_text())
    except (OSError, ValueError):
        return
    ps = subprocess.run(["ps", "-ax", "-o", "pgid=,command="], capture_output=True, text=True).stdout
    ours = [line for line in ps.splitlines() if line.split(None, 1)[0] == str(pgid) and str(PROJECT) in line]
    if ours:
        try:
            os.killpg(pgid, signal.SIGTERM)
        except ProcessLookupError:
            pass
    pid_file.unlink(missing_ok=True)


def free_port() -> int:
    for port in range(PREFERRED_PORT, PREFERRED_PORT + 50):
        with socket.socket() as s:
            try:
                s.bind(("127.0.0.1", port))
                return port
            except OSError:
                continue
    raise RuntimeError("No free local port between 8502 and 8551.")


def missing_models() -> list[str]:
    hub = Path(os.environ.get("HF_HUB_CACHE") or Path(os.environ.get("HF_HOME", Path.home() / ".cache" / "huggingface")) / "hub")
    missing = []
    for model in MODELS:
        snapshots = hub / f"models--{model.replace('/', '--')}" / "snapshots"
        if not (snapshots.is_dir() and any(snapshots.iterdir())):
            missing.append(model)
    return missing


# ---------- server lifecycle ----------

def start_server(port: int) -> subprocess.Popen:
    LOG.parent.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ, HF_HUB_OFFLINE="1", TRANSFORMERS_OFFLINE="1", HF_HUB_DISABLE_TELEMETRY="1")
    log = open(LOG, "w")
    proc = subprocess.Popen(
        [str(PYTHON), str(Path(__file__).with_name("server_guard.py")), str(os.getpid()), str(port)],
        cwd=PROJECT, env=env, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
    )
    (SUPPORT / "server.pgid").write_text(str(proc.pid))  # guard calls setsid, so its pid is the group id
    return proc


def wait_until_ready(proc: subprocess.Popen, port: int) -> bool:
    deadline = time.time() + STARTUP_TIMEOUT
    while time.time() < deadline:
        if proc.poll() is not None:
            return False
        try:
            with urllib.request.urlopen(f"http://127.0.0.1:{port}/_stcore/health", timeout=2) as r:
                if r.read().strip() == b"ok":
                    return True
        except OSError:
            pass
        time.sleep(0.3)
    return False


_stopped = threading.Event()


def stop_server(*_) -> None:
    """Stop only the process group this launch started (server + model workers). Safe to call twice."""
    if _stopped.is_set() or guard is None:
        return
    _stopped.set()
    try:
        os.killpg(guard.pid, signal.SIGTERM)
        try:
            guard.wait(timeout=5)
        except subprocess.TimeoutExpired:
            pass
        os.killpg(guard.pid, signal.SIGKILL)  # anything that ignored SIGTERM
    except ProcessLookupError:
        pass
    (SUPPORT / "server.pgid").unlink(missing_ok=True)


def boot() -> None:
    """Runs on pywebview's background thread once the window exists."""
    global guard
    missing = missing_models()
    if missing:
        window.load_html(error_page(
            "Model files are missing from the local cache",
            "The checker is offline by design and will not download anything. Missing: " + ", ".join(missing)
            + ". Re-run the one-time model download from Terminal, then reopen the app."))
        return
    try:
        port = free_port()
    except RuntimeError as e:
        window.load_html(error_page("Could not start the local server", str(e)))
        return
    guard = start_server(port)
    if wait_until_ready(guard, port):
        window.load_url(f"http://127.0.0.1:{port}/")
    else:
        stop_server()
        window.load_html(error_page("The local server did not start",
                                    f"Streamlit did not answer on 127.0.0.1:{port}. Last lines of the server log:", log_tail()))


def watch_for_quit() -> None:
    """Cmd+Q ends the Cocoa run loop without returning to Python, so hook the terminate notification."""
    import AppKit
    import Foundation

    class QuitObserver(Foundation.NSObject):
        def appWillTerminate_(self, _note):
            stop_server()

    global _observer
    _observer = QuitObserver.alloc().init()
    Foundation.NSNotificationCenter.defaultCenter().addObserver_selector_name_object_(
        _observer, "appWillTerminate:", AppKit.NSApplicationWillTerminateNotification, None)
    try:  # menu bar shows "Quit AI Text Checker" rather than "Quit Python"
        Foundation.NSBundle.mainBundle().infoDictionary()["CFBundleName"] = APP_NAME
    except Exception:
        pass


def main() -> None:
    global window
    if not acquire_single_instance():
        print(f"{APP_NAME} is already running.", file=sys.stderr)
        return
    stop_stale_server()
    atexit.register(stop_server)
    signal.signal(signal.SIGTERM, lambda *_: (stop_server(), sys.exit(0)))
    watch_for_quit()

    webview.settings["ALLOW_DOWNLOADS"] = True
    window = webview.create_window(APP_NAME, html=loading_page(), width=1280, height=900, min_size=(800, 600),
                                   text_select=True)  # pywebview blocks selecting text by default
    window.events.closed += stop_server
    webview.start(boot, icon=str(ICON) if ICON.exists() else None, private_mode=True)
    stop_server()


if __name__ == "__main__":
    main()
