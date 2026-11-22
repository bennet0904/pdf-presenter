"""Startet die App als Kindprozess und meldet unerwartete Beendigungen.

Warum ein eigener Prozess? Die häufigsten Abstürze lassen im abstürzenden
Prozess keinen Python-Code mehr laufen:

* ``Fatal Python error`` (Referenzzähler-Fehler in C-Erweiterungen) bricht
  sofort ab und schreibt seine Meldung nur auf stderr.
* Bei einem Wayland-Protokollfehler beendet Qt das Programm per ``exit()``.
* Segfaults und der OOM-Killer (SIGKILL) beenden den Prozess hart.

Der Supervisor sieht all das von außen: Er reicht stderr unverändert an das
Terminal durch, behält das Ende davon und wertet den Exit-Code aus.
"""

from __future__ import annotations

import json
import os
import signal
import subprocess
import sys
import threading
import time
from collections.abc import Sequence
from pathlib import Path

from pdf_presenter import crashreport

# Diese Beendigungen sind gewollt (Strg+C im Terminal, Abmelden, kill).
_USER_SIGNALS = {getattr(signal, n) for n in ("SIGINT", "SIGTERM", "SIGHUP") if hasattr(signal, n)}


def should_supervise(argv: Sequence[str]) -> bool:
    if crashreport.is_child() or "--no-supervisor" in argv:
        return False
    return crashreport.is_enabled()


def child_command(argv: Sequence[str]) -> list[str]:
    args = [a for a in argv[1:] if a != "--no-supervisor"]
    if getattr(sys, "frozen", False):
        return [sys.executable, *args]  # PyInstaller: die Programmdatei selbst
    return [sys.executable, "-m", "pdf_presenter", *args]


def is_crash(exit_code: int) -> bool:
    if exit_code == 0:
        return False
    if exit_code < 0 and -exit_code in _USER_SIGNALS:
        return False
    return True


class _Tail:
    """Reicht Bytes an ein Ziel durch und behält die letzten ``limit`` Bytes."""

    def __init__(self, sink, limit: int):
        self._sink = sink
        self._limit = limit
        self._buf = bytearray()

    def feed(self, chunk: bytes) -> None:
        if self._sink is not None:
            try:
                self._sink.write(chunk)
                self._sink.flush()
            except (OSError, ValueError):
                self._sink = None  # Terminal weg: trotzdem weiter mitschreiben
        self._buf += chunk
        if len(self._buf) > self._limit:
            del self._buf[: len(self._buf) - self._limit]

    def text(self) -> str:
        return self._buf.decode("utf-8", errors="replace")


def run(argv: Sequence[str], command: Sequence[str] | None = None) -> int:
    """Startet ``command`` (Standard: die App) und gibt dessen Exit-Code zurück."""
    work = crashreport.data_dir()
    session_file = work / f"session-{os.getpid()}.json"
    install = crashreport.install_id()

    # Alte, noch nicht gesendete Berichte im Hintergrund nachreichen.
    threading.Thread(target=crashreport.send_pending, daemon=True).start()

    env = dict(os.environ)
    env[crashreport.ENV_CHILD] = "1"
    env[crashreport.ENV_SESSION_INFO] = str(session_file)
    kwargs = {}
    if sys.platform == "win32":
        kwargs["creationflags"] = getattr(subprocess, "CREATE_NO_WINDOW", 0)

    started_at = crashreport._now()
    start = time.monotonic()
    try:
        proc = subprocess.Popen(
            list(command or child_command(argv)), env=env, stderr=subprocess.PIPE, **kwargs
        )
    except OSError:
        return _run_inline(argv)

    # Strg+C trifft im Terminal beide Prozesse. Der Supervisor wartet auf das Kind.
    previous = signal.signal(signal.SIGINT, signal.SIG_IGN)
    if hasattr(signal, "SIGTERM"):
        signal.signal(signal.SIGTERM, lambda *_: proc.terminate())

    sink = getattr(sys.stderr, "buffer", None)
    tail = _Tail(sink, crashreport.STDERR_LIMIT)
    assert proc.stderr is not None
    read = getattr(proc.stderr, "read1", proc.stderr.read)
    while chunk := read(4096):
        tail.feed(chunk)
    exit_code = proc.wait()
    signal.signal(signal.SIGINT, previous)

    try:
        if is_crash(exit_code):
            environment = _read_json(session_file)
            report = crashreport.build_report(
                exit_code=exit_code,
                stderr=tail.text(),
                started_at=started_at,
                uptime_s=time.monotonic() - start,
                environment=environment,
                install=install,
            )
            crashreport.store(report)
            crashreport.send_pending()
    except Exception:  # noqa: BLE001 - der Bericht darf den Exit-Code nie verfälschen
        pass
    finally:
        session_file.unlink(missing_ok=True)

    if exit_code < 0:
        return 128 - exit_code  # Konvention der Shell für „durch Signal beendet“
    return exit_code


def _read_json(path: Path) -> dict | None:
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
    except (OSError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _run_inline(argv: Sequence[str]) -> int:
    """Notlösung, falls kein Kindprozess gestartet werden kann."""
    from pdf_presenter.__main__ import main

    os.environ[crashreport.ENV_CHILD] = "1"  # verhindert erneutes Überwachen
    return main(list(argv))
