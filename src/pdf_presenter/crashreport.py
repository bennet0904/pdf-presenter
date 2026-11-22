"""Absturzberichte: Einstellungen, Bericht erstellen, speichern und senden.

Ablauf (nur wenn der Nutzer die Funktion eingeschaltet hat):

* ``supervisor.run`` startet die eigentliche App als Kindprozess und liest
  dessen stderr mit. Viele Abstürze hinterlassen ihre Ursache nur dort
  (``Fatal Python error``, Wayland-Protokollfehler, Segfault-Traceback).
* Das Kind schreibt beim Start und bei Änderungen eine kleine
  ``session.json`` mit Versionen und Bildschirmdaten (``init_child``).
* Endet das Kind unerwartet, wird daraus ein Bericht gebaut, unter
  ``pending/`` abgelegt und an den Server geschickt. Was nicht ankommt
  (z. B. offline), wird beim nächsten Start erneut versucht.

Gesendet werden keine Dateinamen, Pfade im Home-Verzeichnis oder Notizen.
"""

from __future__ import annotations

import json
import os
import platform
import re
import ssl
import sys
import time
import urllib.error
import urllib.request
import uuid
from datetime import datetime, timezone
from pathlib import Path

from PySide6.QtCore import QSettings, QStandardPaths

from pdf_presenter import __version__

DEFAULT_ENDPOINT = "https://crash.bekl.dev/api/v1/reports"
SCHEMA = 1

KEY_ENABLED = "crash_reports/enabled"
KEY_INSTALL_ID = "crash_reports/install_id"

# Umgebungsvariablen zwischen Supervisor und Kindprozess
ENV_CHILD = "PDF_PRESENTER_SUPERVISED"
ENV_SESSION_INFO = "PDF_PRESENTER_SESSION_INFO"
ENV_ENDPOINT = "PDF_PRESENTER_CRASH_URL"  # zum Testen, sonst DEFAULT_ENDPOINT

STDERR_LIMIT = 64 * 1024     # so viel vom Ende der Fehlerausgabe wird gesendet
MAX_PENDING = 20             # mehr ungesendete Berichte werden nicht aufgehoben
PENDING_MAX_AGE_S = 30 * 24 * 3600
SEND_TIMEOUT_S = 10


# ------------------------------------------------------------ Einstellungen
def is_enabled() -> bool:
    return QSettings().value(KEY_ENABLED, False, type=bool)


def set_enabled(enabled: bool) -> None:
    QSettings().setValue(KEY_ENABLED, enabled)


def install_id() -> str:
    """Zufällige ID dieser Installation, um Mehrfachberichte zuzuordnen.

    Sie wird lokal erzeugt und hängt an keinen persönlichen Daten.
    """
    settings = QSettings()
    value = settings.value(KEY_INSTALL_ID, "")
    if not isinstance(value, str) or not _is_uuid(value):
        value = str(uuid.uuid4())
        settings.setValue(KEY_INSTALL_ID, value)
    return value


def endpoint() -> str:
    return os.environ.get(ENV_ENDPOINT) or DEFAULT_ENDPOINT


def data_dir() -> Path:
    base = QStandardPaths.writableLocation(QStandardPaths.StandardLocation.AppLocalDataLocation)
    path = Path(base or Path.home() / ".pdf-presenter") / "crash-reports"
    path.mkdir(parents=True, exist_ok=True)
    return path


def _is_uuid(value: str) -> bool:
    try:
        uuid.UUID(value)
    except (ValueError, TypeError):
        return False
    return True


def _now() -> str:
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


# ------------------------------------------------------------- Datenschutz
# Ganzer Pfad bis zu einer Präsentation (Ordnernamen können ebenso privat
# sein wie der Dateiname), danach einzelne Dateinamen ohne Pfad.
_PATH_RE = re.compile(r"(?:[A-Za-z]:)?[~\\/][^\n\"']*?\.(pdfpres|pdf)\b", re.IGNORECASE)
_FILE_RE = re.compile(r"[^\s\"'/\\]+\.(pdfpres|pdf)\b", re.IGNORECASE)
_FRAME_RE = re.compile(r'File "([^"\n]+)"')


def _short_source_path(match: re.Match) -> str:
    """Kürzt Quelltextpfade in Tracebacks, damit kein Installationsort sichtbar wird."""
    parts = re.split(r"[\\/]", match.group(1))
    for anchor in ("pdf_presenter", "site-packages", "PySide6", "shiboken6"):
        if anchor in parts[:-1]:
            return 'File "' + "/".join(parts[parts.index(anchor):]) + '"'
    return 'File "' + "/".join(parts[-2:]) + '"'


def sanitize(text: str) -> str:
    """Entfernt Home-Verzeichnis, Benutzername und Namen von Präsentationen."""
    home = str(Path.home())
    if len(home) > 1:
        text = text.replace(home, "~")
    try:
        user = os.getlogin()
    except OSError:
        user = os.environ.get("USER") or os.environ.get("USERNAME") or ""
    if len(user) > 1:
        text = re.sub(rf"(?<=[\\/]){re.escape(user)}(?=[\\/])", "<user>", text)
    text = _FRAME_RE.sub(_short_source_path, text)
    text = _PATH_RE.sub(lambda m: f"<datei>.{m.group(1).lower()}", text)
    return _FILE_RE.sub(lambda m: f"<datei>.{m.group(1).lower()}", text)


# ---------------------------------------------------- Kindprozess-Seite
def is_child() -> bool:
    return os.environ.get(ENV_CHILD) == "1"


def _distro() -> str:
    try:
        for line in Path("/etc/os-release").read_text(encoding="utf-8").splitlines():
            if line.startswith("PRETTY_NAME="):
                return line.split("=", 1)[1].strip().strip('"')
    except OSError:
        pass
    return ""


def collect_environment(app=None, presenting: bool | None = None) -> dict:
    import PySide6
    from PySide6.QtCore import qVersion

    info = {
        "app_version": __version__,
        "python": platform.python_version(),
        "pyside": PySide6.__version__,
        "qt": qVersion(),
        "os": platform.system(),
        "os_release": platform.release(),
        "distro": _distro(),
        "machine": platform.machine(),
        "frozen": bool(getattr(sys, "frozen", False)),
        "desktop": os.environ.get("XDG_CURRENT_DESKTOP", ""),
        "session_type": os.environ.get("XDG_SESSION_TYPE", ""),
    }
    if app is not None:
        info["qpa"] = app.platformName()
        info["screens"] = [
            {
                "w": s.geometry().width(),
                "h": s.geometry().height(),
                "dpr": round(s.devicePixelRatio(), 2),
                "hz": round(s.refreshRate()),
            }
            for s in app.screens()
        ]
    if presenting is not None:
        info["presenting"] = presenting
    return info


class _SessionInfo:
    """Hält ``session.json`` des Kindprozesses aktuell."""

    def __init__(self, app, path: Path):
        self._app = app
        self._path = path
        self._presenting = False
        self.write()
        app.screenAdded.connect(lambda _s: self.write())
        app.screenRemoved.connect(lambda _s: self.write())

    def set_presenting(self, presenting: bool) -> None:
        self._presenting = presenting
        self.write()

    def write(self) -> None:
        try:
            info = collect_environment(self._app, self._presenting)
            tmp = self._path.with_suffix(".tmp")
            tmp.write_text(json.dumps(info), encoding="utf-8")
            os.replace(tmp, self._path)
        except OSError:
            pass  # Absturzberichte dürfen die App nie stören


_session: _SessionInfo | None = None


def init_child(app) -> None:
    """Im überwachten Kindprozess aufrufen, sobald die QApplication existiert."""
    global _session
    import faulthandler

    if sys.stderr is not None:
        try:
            faulthandler.enable()  # Traceback bei Segfault & Co. auf stderr
        except (RuntimeError, ValueError, OSError):
            pass
    path = os.environ.get(ENV_SESSION_INFO)
    if path:
        _session = _SessionInfo(app, Path(path))


def note_presenting(presenting: bool) -> None:
    """Merkt sich, ob gerade präsentiert wird (für den Bericht)."""
    if _session is not None:
        _session.set_presenting(presenting)


# --------------------------------------------------------- Bericht bauen
def classify(exit_code: int, stderr: str) -> tuple[str, str | None]:
    """Liefert (Art des Absturzes, Signalname oder None)."""
    sig_name = None
    if exit_code < 0:
        try:
            import signal

            sig_name = signal.Signals(-exit_code).name
        except (ValueError, AttributeError):
            sig_name = f"SIG{-exit_code}"
    if "Fatal Python error" in stderr:
        kind = "python_fatal"
    elif "Wayland connection" in stderr or "wl_display" in stderr:
        kind = "wayland"
    elif sig_name == "SIGKILL":
        kind = "killed"  # z. B. OOM-Killer / systemd-oomd
    elif sig_name is not None:
        kind = "signal"
    else:
        kind = "exit"
    return kind, sig_name


def build_report(
    *,
    exit_code: int,
    stderr: str,
    started_at: str,
    uptime_s: float,
    environment: dict | None,
    install: str,
) -> dict:
    kind, sig_name = classify(exit_code, stderr)
    env = {k: v for k, v in (environment or {}).items() if k != "app_version"}
    return {
        "schema": SCHEMA,
        "report_id": str(uuid.uuid4()),
        "install_id": install,
        "app_version": (environment or {}).get("app_version", __version__),
        "kind": kind,
        "exit_code": exit_code,
        "signal": sig_name,
        "started_at": started_at,
        "crashed_at": _now(),
        "uptime_s": round(uptime_s, 1),
        "environment": json.loads(sanitize(json.dumps(env))),
        "stderr_tail": sanitize(stderr[-STDERR_LIMIT:]),
    }


# ------------------------------------------------- speichern und senden
def pending_dir() -> Path:
    path = data_dir() / "pending"
    path.mkdir(parents=True, exist_ok=True)
    return path


def store(report: dict) -> Path:
    path = pending_dir() / f"{report['report_id']}.json"
    path.write_text(json.dumps(report, ensure_ascii=False), encoding="utf-8")
    _prune()
    return path


def _prune() -> None:
    files = sorted(pending_dir().glob("*.json"), key=lambda p: p.stat().st_mtime, reverse=True)
    cutoff = time.time() - PENDING_MAX_AGE_S
    for i, path in enumerate(files):
        if i >= MAX_PENDING or path.stat().st_mtime < cutoff:
            path.unlink(missing_ok=True)


# Typische CA-Bündel. Wichtig für die PyInstaller-Builds: Deren OpenSSL sucht
# Zertifikate am Ort des Build-Systems (Ubuntu), auf Fedora & Co. liegen sie woanders.
_CA_BUNDLES = (
    "/etc/ssl/certs/ca-certificates.crt",
    "/etc/pki/tls/certs/ca-bundle.crt",
    "/etc/ssl/ca-bundle.pem",
    "/etc/ssl/cert.pem",
)


def _ssl_context() -> ssl.SSLContext:
    ctx = ssl.create_default_context()
    if sys.platform.startswith("linux"):
        for bundle in _CA_BUNDLES:
            if os.path.isfile(bundle):
                try:
                    ctx.load_verify_locations(bundle)
                    break
                except (ssl.SSLError, OSError):
                    continue
    return ctx


def send(report: dict, url: str | None = None) -> bool:
    """Schickt einen Bericht. True = angenommen (oder dort schon vorhanden)."""
    url = url or endpoint()
    body = json.dumps(report, ensure_ascii=False).encode("utf-8")
    request = urllib.request.Request(
        url,
        data=body,
        method="POST",
        headers={"Content-Type": "application/json", "User-Agent": f"pdf-presenter/{__version__}"},
    )
    context = _ssl_context() if url.startswith("https:") else None
    try:
        with urllib.request.urlopen(request, timeout=SEND_TIMEOUT_S, context=context) as resp:
            return 200 <= resp.status < 300
    except urllib.error.HTTPError as exc:
        # 4xx außer 429: Der Server will diesen Bericht nicht, also nicht ewig wiederholen.
        return 400 <= exc.code < 500 and exc.code != 429
    except (urllib.error.URLError, OSError, ValueError):
        return False


def send_pending(url: str | None = None) -> int:
    """Versucht alle gespeicherten Berichte zu senden. Liefert die Anzahl gesendeter."""
    sent = 0
    for path in sorted(pending_dir().glob("*.json")):
        try:
            report = json.loads(path.read_text(encoding="utf-8"))
        except (OSError, ValueError):
            path.unlink(missing_ok=True)
            continue
        if send(report, url):
            path.unlink(missing_ok=True)
            sent += 1
        else:
            break  # offline oder Server nicht erreichbar: später erneut
    return sent
