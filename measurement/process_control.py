from __future__ import annotations

import subprocess
import threading
import time
from dataclasses import dataclass


@dataclass(frozen=True)
class ProcessResult:
    returncode: int
    stdout: str
    stderr: str
    cancelled: bool = False
    timed_out: bool = False


def run_cancellable(
    command: list[str],
    timeout_s: float,
    stop_event: threading.Event | None = None,
) -> ProcessResult:
    process = subprocess.Popen(
        command,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
    )
    deadline = time.monotonic() + timeout_s
    while True:
        if stop_event is not None and stop_event.is_set():
            stdout, stderr = _terminate(process)
            return ProcessResult(_returncode(process), stdout, stderr, cancelled=True)
        remaining = deadline - time.monotonic()
        if remaining <= 0:
            stdout, stderr = _terminate(process)
            return ProcessResult(_returncode(process), stdout, stderr, timed_out=True)
        try:
            stdout, stderr = process.communicate(timeout=min(0.2, remaining))
            return ProcessResult(process.returncode or 0, stdout or "", stderr or "")
        except subprocess.TimeoutExpired:
            continue


def _terminate(process: subprocess.Popen[str]) -> tuple[str, str]:
    process.terminate()
    try:
        stdout, stderr = process.communicate(timeout=2)
    except subprocess.TimeoutExpired:
        process.kill()
        stdout, stderr = process.communicate()
    return stdout or "", stderr or ""


def _returncode(process: subprocess.Popen[str]) -> int:
    return process.returncode if process.returncode is not None else -1
