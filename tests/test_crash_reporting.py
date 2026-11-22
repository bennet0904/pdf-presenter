"""App-Seite der Absturzberichte: Bericht bauen, Datenschutz, Supervisor."""

import json
import sys
import threading
from pathlib import Path

import pytest
from PySide6.QtCore import QSettings, QStandardPaths

from pdf_presenter import crashreport, supervisor

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import crash_collector as cc  # noqa: E402


@pytest.fixture
def isolated(qapp, tmp_path, monkeypatch):
    """Eigene Einstellungen und Datenordner, Server auf einem freien Port."""
    QStandardPaths.setTestModeEnabled(True)
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    qapp.setOrganizationName("pdf-presenter-crash-tests")
    monkeypatch.setattr(crashreport, "data_dir", lambda: _mk(tmp_path / "data"))

    store = cc.Store(str(tmp_path / "server.sqlite3"))
    srv = cc.make_server("127.0.0.1:0", store, cc.RateLimiter(100, 1000), trust_proxy=False)
    threading.Thread(target=srv.serve_forever, daemon=True).start()
    monkeypatch.setenv(crashreport.ENV_ENDPOINT, f"http://127.0.0.1:{srv.server_address[1]}{cc.API_PATH}")
    yield store
    srv.shutdown()
    srv.server_close()
    QStandardPaths.setTestModeEnabled(False)


def _mk(path: Path) -> Path:
    path.mkdir(parents=True, exist_ok=True)
    return path


def stored(store):
    return [json.loads(r["payload"]) for r in store.query("SELECT payload FROM reports")]


def run_child(code: str, argv=("pdf-presenter",)):
    return supervisor.run(list(argv), command=[sys.executable, "-c", code])


def test_disabled_by_default_and_toggle(isolated):
    assert not crashreport.is_enabled()
    assert not supervisor.should_supervise(["x"])
    crashreport.set_enabled(True)
    assert supervisor.should_supervise(["x"])
    assert not supervisor.should_supervise(["x", "--no-supervisor"])
    assert crashreport.install_id() == crashreport.install_id()


def test_sanitize_removes_private_data():
    home = str(Path.home())
    text = f"QPdf: {home}/Vorträge/Geheimprojekt Kunde.pdf und /media/usb/Q3-Zahlen.PDFPRES"
    clean = crashreport.sanitize(text)
    assert home not in clean and "Geheimprojekt" not in clean and "Q3-Zahlen" not in clean
    assert "Vorträge" not in clean
    assert clean == "QPdf: <datei>.pdf und <datei>.pdfpres"
    # Pfade zu Quelltext bleiben lesbar, sie sind für die Fehlersuche wichtig.
    frame = 'File "/media/usb/Kunde X/PDF-Presenter/_internal/pdf_presenter/session.py", line 3 in f'
    assert crashreport.sanitize(frame) == 'File "pdf_presenter/session.py", line 3 in f'
    other = f'File "{home}/geheim/tools/run.py", line 1 in <module>'
    assert crashreport.sanitize(other) == 'File "tools/run.py", line 1 in <module>'


@pytest.mark.parametrize("code, exit_code, crash", [
    (0, 0, False), (1, 1, True), (255, 255, True), (-6, -6, True), (-9, -9, True),
    (-2, -2, False), (-15, -15, False),
])
def test_what_counts_as_crash(code, exit_code, crash):
    assert supervisor.is_crash(exit_code) is crash


def test_python_fatal_error_is_reported(isolated):
    code = (
        "import sys, os\n"
        "sys.stderr.write('Fatal Python error: none_dealloc: deallocating None\\n')\n"
        "sys.stderr.write('  File \"' + os.path.expanduser('~') + '/x/Vortrag.pdf\", line 1 in f\\n')\n"
        "sys.stderr.flush(); os.abort()\n"
    )
    exit_code = run_child(code)
    assert exit_code != 0
    [report] = stored(isolated)
    assert report["kind"] == "python_fatal"
    assert "none_dealloc" in report["stderr_tail"]
    assert "Vortrag" not in report["stderr_tail"]
    assert list(crashreport.pending_dir().glob("*.json")) == []  # gesendet = gelöscht


def test_environment_from_child_is_included(isolated):
    code = (
        "import json, os, sys\n"
        f"p = os.environ['{crashreport.ENV_SESSION_INFO}']\n"
        "open(p, 'w').write(json.dumps({'app_version': '9.9.9', 'qpa': 'wayland', 'presenting': True}))\n"
        "sys.stderr.write('The Wayland connection experienced a fatal error: Protocol error\\n')\n"
        "sys.exit(255)\n"
    )
    assert run_child(code) == 255
    [report] = stored(isolated)
    assert report["kind"] == "wayland" and report["app_version"] == "9.9.9"
    assert report["environment"] == {"qpa": "wayland", "presenting": True}


def test_clean_exit_sends_nothing(isolated):
    assert run_child("import sys; sys.stderr.write('nur eine Warnung\\n')") == 0
    assert stored(isolated) == []


def test_offline_reports_are_kept_and_sent_later(isolated, monkeypatch):
    monkeypatch.setenv(crashreport.ENV_ENDPOINT, "http://127.0.0.1:9/nirgends")
    run_child("import sys; sys.exit(3)")
    assert len(list(crashreport.pending_dir().glob("*.json"))) == 1
    assert stored(isolated) == []


def test_pending_reports_are_sent_on_next_start(isolated):
    report = crashreport.build_report(
        exit_code=-11, stderr="Segmentation fault", started_at="x", uptime_s=1.0,
        environment=None, install=crashreport.install_id(),
    )
    crashreport.store(report)
    assert crashreport.send_pending() == 1
    [got] = stored(isolated)
    assert got["report_id"] == report["report_id"] and got["kind"] == "signal"
    if sys.platform != "win32":
        assert got["signal"] == "SIGSEGV"
