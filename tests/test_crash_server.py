"""Server für Absturzberichte (server/crash_collector.py)."""

import json
import sys
import threading
import urllib.error
import urllib.request
import uuid
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "server"))
import crash_collector as cc  # noqa: E402


def make_report(**overrides):
    report = {
        "schema": 1,
        "report_id": str(uuid.uuid4()),
        "install_id": str(uuid.uuid4()),
        "app_version": "0.1.0",
        "kind": "python_fatal",
        "exit_code": -6,
        "signal": "SIGABRT",
        "started_at": "2026-11-22T10:00:00+00:00",
        "crashed_at": "2026-11-22T10:05:00+00:00",
        "uptime_s": 300.0,
        "environment": {"python": "3.11.9", "pyside": "6.12.0", "os": "Linux",
                        "screens": [{"w": 1920, "h": 1080, "dpr": 1.0, "hz": 60, "name": "x"}]},
        "stderr_tail": 'Fatal Python error: none_dealloc: deallocating None\n'
                       '  File "/x/session.py", line 210 in _handle_key\n',
    }
    report.update(overrides)
    return report


@pytest.fixture
def server(tmp_path):
    store = cc.Store(str(tmp_path / "r.sqlite3"))
    srv = cc.make_server("127.0.0.1:0", store, cc.RateLimiter(5, 100), trust_proxy=False)
    thread = threading.Thread(target=srv.serve_forever, daemon=True)
    thread.start()
    srv.url = f"http://127.0.0.1:{srv.server_address[1]}"
    srv.store = store
    yield srv
    srv.shutdown()
    srv.server_close()


def post(url, body, content_type="application/json"):
    data = body if isinstance(body, bytes) else json.dumps(body).encode()
    req = urllib.request.Request(url, data=data, method="POST", headers={"Content-Type": content_type})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status, json.loads(resp.read())
    except urllib.error.HTTPError as exc:
        return exc.code, json.loads(exc.read() or b"{}")


def test_accepts_and_stores_report(server):
    report = make_report()
    status, body = post(server.url + cc.API_PATH, report)
    assert status == 201
    rows = server.store.query("SELECT * FROM reports")
    assert len(rows) == 1
    stored = json.loads(rows[0]["payload"])
    assert stored["report_id"] == report["report_id"]
    assert stored["environment"]["screens"] == [{"w": 1920, "h": 1080, "dpr": 1.0, "hz": 60}]
    assert rows[0]["pyside"] == "6.12.0"

    # Wiederholtes Senden (z. B. nach Zeitüberschreitung) legt nichts doppelt an.
    status, again = post(server.url + cc.API_PATH, report)
    assert status == 200 and again["id"] == body["id"]
    assert len(server.store.query("SELECT id FROM reports")) == 1


@pytest.mark.parametrize("bad", [
    {"schema": 2},
    {"report_id": "keine-uuid"},
    {"exit_code": "1"},
    {"kind": "x" * 100},
    {"environment": {"nested": {"a": 1}}},
    {"stderr_tail": "x" * (200 * 1024)},
])
def test_rejects_invalid_reports(server, bad):
    status, _ = post(server.url + cc.API_PATH, make_report(**bad))
    assert status in (400, 413)
    assert server.store.query("SELECT COUNT(*) AS n FROM reports")[0]["n"] == 0


def test_rejects_garbage_and_wrong_paths(server):
    assert post(server.url + cc.API_PATH, b"{kaputt")[0] == 400
    assert post(server.url + cc.API_PATH, make_report(), "text/plain")[0] == 415
    assert post(server.url + "/anderswo", make_report())[0] == 404
    assert post(server.url + cc.API_PATH, b"x" * (cc.MAX_BODY + 1))[0] == 413
    with urllib.request.urlopen(server.url + "/healthz", timeout=5) as resp:
        assert json.loads(resp.read()) == {"ok": True}


def test_rate_limit(server):
    codes = [post(server.url + cc.API_PATH, make_report())[0] for _ in range(7)]
    assert codes == [201] * 5 + [429] * 2


def test_rate_limiter_windows():
    limiter = cc.RateLimiter(per_ip_hour=2, per_day=3)
    assert limiter.allow("a", 0) and limiter.allow("a", 1)
    assert not limiter.allow("a", 2)          # IP-Limit
    assert limiter.allow("b", 3)
    assert not limiter.allow("c", 4)          # Tageslimit
    assert limiter.allow("a", 3700) is False  # Tageslimit gilt weiter
    assert limiter.allow("a", 90000)          # neuer Tag


def test_signature_ignores_line_numbers():
    a = make_report(stderr_tail='Fatal Python error: bool_dealloc: x\n  File "/a/b.py", line 1 in f\n')
    b = make_report(stderr_tail='Fatal Python error: bool_dealloc: y\n  File "/c/b.py", line 99 in f\n')
    c = make_report(stderr_tail='Fatal Python error: none_dealloc: y\n  File "/c/b.py", line 99 in f\n')
    assert cc.signature(a) == cc.signature(b) != cc.signature(c)
