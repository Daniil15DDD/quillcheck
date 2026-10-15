"""Run one model task in a short-lived Python process.

The process loads its models, does the work, sends the result back and exits, so all
model and allocator memory returns to the OS after every task. A crash (for example
macOS killing the process when memory runs out) is reported instead of taking the app
down. Messages between the processes are pickled; both ends are this app's own code.
"""
from __future__ import annotations

from typing import Any, Callable


class WorkerError(Exception):
    def __init__(self, kind: str, message: str) -> None:
        super().__init__(message)
        self.kind = kind  # "models-missing", "runtime", or "crashed"


def serve() -> None:
    """Worker entry point: read one pickled (module, function, kwargs) request on stdin,
    stream pickled ("progress" | "result" | "error", payload) messages on stdout, exit."""
    import importlib
    import pickle
    import sys

    out = sys.stdout.buffer
    sys.stdout = sys.stderr  # keep stray prints off the message pipe
    module, function, kwargs = pickle.load(sys.stdin.buffer)

    def send(message) -> None:
        pickle.dump(message, out)
        out.flush()

    try:
        func = getattr(importlib.import_module(module), function)
        send(("result", func(**kwargs, progress=lambda m: send(("progress", m)))))
    except OSError as e:
        send(("error", ("models-missing", f"{type(e).__name__}: {str(e)[:300]}")))
    except Exception as e:
        send(("error", ("runtime", f"{type(e).__name__}: {str(e)[:300]}")))


def call(module: str, function: str, kwargs: dict, progress: Callable[[str], None] = lambda _: None) -> Any:
    """Run module.function(**kwargs, progress=...) in a fresh process and return its result."""
    import pickle
    import subprocess
    import sys
    from pathlib import Path

    here = str(Path(__file__).resolve().parent)
    proc = subprocess.Popen(
        [sys.executable, "-c", f"import sys; sys.path.insert(0, {here!r}); import worker; worker.serve()"],
        stdin=subprocess.PIPE, stdout=subprocess.PIPE, stderr=subprocess.DEVNULL,
    )
    try:
        pickle.dump((module, function, kwargs), proc.stdin)
        proc.stdin.close()
        while True:
            try:
                kind, payload = pickle.load(proc.stdout)
            except EOFError:
                raise WorkerError("crashed", f"The model process stopped unexpectedly (exit code {proc.wait()}).") from None
            if kind == "progress":
                progress(payload)
            elif kind == "result":
                return payload
            else:
                raise WorkerError(*payload)
    finally:
        try:
            proc.wait(timeout=30)
        except subprocess.TimeoutExpired:
            proc.kill()
