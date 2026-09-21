"""Own only the backend we launch, and stop it if the desktop disappears.

This small supervisor is intentionally separate from the EPUB server. Reusing an
already healthy server never invokes it, so closing the desktop cannot stop a
server that was opened by the browser launcher or a developer.
"""
from __future__ import annotations

import argparse
import os
from pathlib import Path
import signal
import subprocess
import sys
import threading


def run(project: Path, parent_pid: int) -> int:
    # An arbitrary PID is never used as a termination target. The parent must be
    # this supervisor's actual creator; the only signal recipient is our child.
    if parent_pid <= 1 or os.getppid() != parent_pid:
        return 2
    stopping = threading.Event()
    for sig in (signal.SIGTERM, signal.SIGINT, signal.SIGHUP):
        signal.signal(sig, lambda _signum, _frame: stopping.set())
    child = subprocess.Popen(
        [sys.executable, "-u", str(project / "server.py")],
        cwd=project,
        stdin=subprocess.DEVNULL,
    )
    try:
        while child.poll() is None:
            if stopping.wait(0.25) or os.getppid() != parent_pid:
                break
        if child.poll() is not None:
            return child.returncode
        child.terminate()
        try:
            return child.wait(timeout=8)
        except subprocess.TimeoutExpired:
            child.kill()
            return child.wait(timeout=2)
    finally:
        # Also applies to unexpected Python exceptions. No process-name or port-based kill.
        if child.poll() is None:
            child.kill()
            child.wait(timeout=2)


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--project", required=True, type=Path)
    parser.add_argument("--parent", required=True, type=int)
    args = parser.parse_args()
    return run(args.project.resolve(), args.parent)


if __name__ == "__main__":
    raise SystemExit(main())
