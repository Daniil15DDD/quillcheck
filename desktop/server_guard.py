"""Run the Streamlit server in its own process group and tie its life to the desktop app.

Started by desktop.py as: python server_guard.py <app_pid> <port>
This process leads a new session, so Streamlit and every model worker it spawns share
one process group. If the desktop app disappears without cleaning up (crash, force quit),
the guard notices and stops the whole group, so no server or worker is left behind.
"""
import os
import signal
import subprocess
import sys
import time
from pathlib import Path

PROJECT = Path(__file__).resolve().parent.parent


def main() -> None:
    app_pid, port = int(sys.argv[1]), sys.argv[2]
    os.setsid()  # new process group led by this guard; children inherit it
    server = subprocess.Popen(
        [
            sys.executable, "-m", "streamlit", "run", str(PROJECT / "app.py"),
            "--server.address", "127.0.0.1",
            "--server.port", port,
            "--server.headless", "true",
            "--server.fileWatcherType", "none",  # no hot reload needed; avoids scanning torch/transformers modules
            "--client.toolbarMode", "minimal",  # no "Deploy" button, which would navigate the window off this Mac
            "--browser.gatherUsageStats", "false",
            "--browser.serverAddress", "127.0.0.1",
        ],
        cwd=PROJECT,  # picks up .streamlit/config.toml like run.sh does
    )

    def stop_group(*_):
        signal.signal(signal.SIGTERM, signal.SIG_DFL)
        os.killpg(0, signal.SIGTERM)

    signal.signal(signal.SIGTERM, stop_group)
    while server.poll() is None:
        try:
            os.kill(app_pid, 0)
        except ProcessLookupError:  # desktop app is gone
            stop_group()
        time.sleep(1)
    sys.exit(server.returncode)


if __name__ == "__main__":
    main()
