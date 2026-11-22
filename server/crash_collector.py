#!/usr/bin/env python3
"""Nimmt Absturzberichte von PDF Presenter entgegen und speichert sie in SQLite.

Nur Standardbibliothek, gedacht hinter einem Reverse-Proxy (Caddy/nginx),
der TLS übernimmt. Siehe server/README.md.

    crash_collector.py serve                  Server starten
    crash_collector.py list [--limit N]       letzte Berichte
    crash_collector.py groups                 Berichte nach Ursache gruppiert
    crash_collector.py show <id|report_id>    einen Bericht vollständig anzeigen

Konfiguration über Umgebungsvariablen:

    CRASH_DB            Pfad der Datenbank       (Standard: ./reports.sqlite3)
    CRASH_BIND          Adresse:Port             (Standard: 127.0.0.1:8765)
    CRASH_TRUST_PROXY   1 = X-Forwarded-For vom lokalen Proxy für das Rate-Limit nutzen
    CRASH_PER_IP_HOUR   Berichte pro IP und Stunde   (Standard: 20)
    CRASH_PER_DAY       Berichte insgesamt pro Tag   (Standard: 2000)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
import sqlite3
import sys
import threading
import time
import uuid
from collections import deque
from datetime import datetime, timezone
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_PATH = "/api/v1/reports"
MAX_BODY = 256 * 1024
SCHEMA = 1

SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS reports (
    id           INTEGER PRIMARY KEY AUTOINCREMENT,
    received_at  TEXT NOT NULL,
    report_id    TEXT NOT NULL UNIQUE,
    install_id   TEXT NOT NULL,
    app_version  TEXT NOT NULL,
    kind         TEXT NOT NULL,
    signature    TEXT NOT NULL,
    os           TEXT,
    python       TEXT,
    pyside       TEXT,
    qpa          TEXT,
    payload      TEXT NOT NULL
);
CREATE INDEX IF NOT EXISTS reports_signature ON reports(signature);
CREATE INDEX IF NOT EXISTS reports_received ON reports(received_at);
"""


# ----------------------------------------------------------- Validierung
class Invalid(ValueError):
    pass


def _str(value, name: str, max_len: int, *, optional: bool = False) -> str | None:
    if value is None and optional:
        return None
    if not isinstance(value, str) or len(value) > max_len:
        raise Invalid(f"{name}: Text bis {max_len} Zeichen erwartet")
    return value


def _uuid(value, name: str) -> str:
    value = _str(value, name, 36)
    try:
        return str(uuid.UUID(value))
    except ValueError:
        raise Invalid(f"{name}: UUID erwartet") from None


def _env(value) -> dict:
    """Umgebung: flaches Objekt aus Texten/Zahlen, plus Liste ``screens``."""
    if not isinstance(value, dict) or len(value) > 40:
        raise Invalid("environment: Objekt mit höchstens 40 Feldern erwartet")
    clean: dict = {}
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 40:
            raise Invalid("environment: ungültiger Schlüssel")
        if key == "screens":
            if not isinstance(item, list) or len(item) > 16:
                raise Invalid("environment.screens: Liste erwartet")
            screens = []
            for screen in item:
                if not isinstance(screen, dict):
                    raise Invalid("environment.screens: Objekte erwartet")
                screens.append({
                    k: v for k, v in screen.items()
                    if k in ("w", "h", "dpr", "hz") and isinstance(v, (int, float)) and not isinstance(v, bool)
                })
            clean[key] = screens
        elif isinstance(item, bool) or item is None or isinstance(item, (int, float)):
            clean[key] = item
        elif isinstance(item, str) and len(item) <= 200:
            clean[key] = item
        else:
            raise Invalid(f"environment.{key}: ungültiger Wert")
    return clean


def validate(data) -> dict:
    """Prüft einen Bericht und liefert nur die bekannten Felder zurück."""
    if not isinstance(data, dict):
        raise Invalid("JSON-Objekt erwartet")
    if data.get("schema") != SCHEMA:
        raise Invalid(f"schema {SCHEMA} erwartet")
    exit_code = data.get("exit_code")
    if not isinstance(exit_code, int) or isinstance(exit_code, bool):
        raise Invalid("exit_code: Zahl erwartet")
    uptime = data.get("uptime_s")
    if not isinstance(uptime, (int, float)) or isinstance(uptime, bool) or uptime < 0:
        raise Invalid("uptime_s: Zahl erwartet")
    return {
        "schema": SCHEMA,
        "report_id": _uuid(data.get("report_id"), "report_id"),
        "install_id": _uuid(data.get("install_id"), "install_id"),
        "app_version": _str(data.get("app_version"), "app_version", 32),
        "kind": _str(data.get("kind"), "kind", 32),
        "exit_code": exit_code,
        "signal": _str(data.get("signal"), "signal", 16, optional=True),
        "started_at": _str(data.get("started_at"), "started_at", 40),
        "crashed_at": _str(data.get("crashed_at"), "crashed_at", 40),
        "uptime_s": uptime,
        "environment": _env(data.get("environment") or {}),
        "stderr_tail": _str(data.get("stderr_tail", ""), "stderr_tail", 128 * 1024),
    }


_FRAME_RE = re.compile(r'File "(?:[^"]*[\\/])?([^"\\/]+)", line \d+ in (\S+)')
_FATAL_RE = re.compile(r"Fatal Python error: ([^\n:]+)")


def signature(report: dict) -> str:
    """Kurzer Schlüssel, um gleiche Abstürze zu gruppieren.

    Zeilennummern bleiben bewusst außen vor, damit dieselbe Ursache über
    mehrere Versionen hinweg in einer Gruppe landet.
    """
    text = report["stderr_tail"]
    parts = [report["kind"], report.get("signal") or ""]
    fatal = _FATAL_RE.search(text)
    if fatal:
        parts.append(fatal.group(1).strip())
    parts += [f"{f}:{fn}" for f, fn in _FRAME_RE.findall(text)[:5]]
    return hashlib.sha1("|".join(parts).encode()).hexdigest()[:12]


# --------------------------------------------------------------- Speicher
class Store:
    def __init__(self, path: str):
        self._lock = threading.Lock()
        self._db = sqlite3.connect(path, check_same_thread=False)
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA_SQL)

    def add(self, report: dict) -> tuple[bool, int]:
        """Speichert einen Bericht. Liefert (neu?, Datenbank-ID)."""
        env = report["environment"]
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT id FROM reports WHERE report_id = ?", (report["report_id"],)
            ).fetchone()
            if row:
                return False, row[0]
            cur = self._db.execute(
                "INSERT INTO reports (received_at, report_id, install_id, app_version, kind,"
                " signature, os, python, pyside, qpa, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    datetime.now(timezone.utc).isoformat(timespec="seconds"),
                    report["report_id"], report["install_id"], report["app_version"],
                    report["kind"], signature(report),
                    " ".join(str(env.get(k, "")) for k in ("os", "distro")).strip() or None,
                    env.get("python"), env.get("pyside"), env.get("qpa"),
                    json.dumps(report, ensure_ascii=False),
                ),
            )
            return True, cur.lastrowid

    def query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            self._db.row_factory = sqlite3.Row
            try:
                return self._db.execute(sql, args).fetchall()
            finally:
                self._db.row_factory = None


# ------------------------------------------------------------ Rate-Limit
class RateLimiter:
    def __init__(self, per_ip_hour: int, per_day: int):
        self._per_ip = per_ip_hour
        self._per_day = per_day
        self._ips: dict[str, deque] = {}
        self._all: deque = deque()
        self._lock = threading.Lock()

    def allow(self, ip: str, now: float | None = None) -> bool:
        now = time.monotonic() if now is None else now
        with self._lock:
            while self._all and now - self._all[0] > 86400:
                self._all.popleft()
            hits = self._ips.setdefault(ip, deque())
            while hits and now - hits[0] > 3600:
                hits.popleft()
            if len(hits) >= self._per_ip or len(self._all) >= self._per_day:
                return False
            hits.append(now)
            self._all.append(now)
            if len(self._ips) > 10000:  # Speicher begrenzen
                self._ips = {k: v for k, v in self._ips.items() if v}
            return True


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "crash-collector"
    sys_version = ""
    timeout = 15  # langsame Clients nicht ewig festhalten

    store: Store
    limiter: RateLimiter
    trust_proxy: bool

    def log_message(self, fmt: str, *args) -> None:  # keine IP-Adressen ins Log
        sys.stderr.write(f"{self.command} {self.path} {args[1] if len(args) > 1 else ''}\n")

    def _reply(self, status: HTTPStatus, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _client_ip(self) -> str:
        ip = self.client_address[0]
        if self.trust_proxy and ip in ("127.0.0.1", "::1"):
            forwarded = self.headers.get("X-Forwarded-For", "")
            if forwarded:
                return forwarded.split(",")[-1].strip()
        return ip

    def do_GET(self) -> None:  # noqa: N802
        if self.path == "/healthz":
            self._reply(HTTPStatus.OK, {"ok": True})
        else:
            self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})

    def do_POST(self) -> None:  # noqa: N802
        if self.path != API_PATH:
            return self._reply(HTTPStatus.NOT_FOUND, {"error": "not found"})
        if "json" not in self.headers.get("Content-Type", ""):
            return self._reply(HTTPStatus.UNSUPPORTED_MEDIA_TYPE, {"error": "application/json erwartet"})
        try:
            length = int(self.headers.get("Content-Length", ""))
        except ValueError:
            return self._reply(HTTPStatus.LENGTH_REQUIRED, {"error": "Content-Length fehlt"})
        if length < 0 or length > MAX_BODY:
            return self._reply(HTTPStatus.REQUEST_ENTITY_TOO_LARGE, {"error": "zu groß"})
        if not self.limiter.allow(self._client_ip()):
            return self._reply(HTTPStatus.TOO_MANY_REQUESTS, {"error": "zu viele Berichte"})
        try:
            report = validate(json.loads(self.rfile.read(length)))
        except (ValueError, UnicodeDecodeError) as exc:
            msg = str(exc) if isinstance(exc, Invalid) else "ungültiges JSON"
            return self._reply(HTTPStatus.BAD_REQUEST, {"error": msg})
        created, row_id = self.store.add(report)
        self._reply(HTTPStatus.CREATED if created else HTTPStatus.OK, {"id": row_id})


def make_server(bind: str, store: Store, limiter: RateLimiter, trust_proxy: bool) -> ThreadingHTTPServer:
    host, _, port = bind.rpartition(":")
    handler = type("BoundHandler", (Handler,), {
        "store": store, "limiter": limiter, "trust_proxy": trust_proxy,
    })
    server = ThreadingHTTPServer((host.strip("[]") or "127.0.0.1", int(port)), handler)
    server.daemon_threads = True
    return server


# ------------------------------------------------------------------- CLI
def _store_from_env(args) -> Store:
    return Store(args.db or os.environ.get("CRASH_DB", "reports.sqlite3"))


def cmd_serve(args) -> int:
    store = _store_from_env(args)
    limiter = RateLimiter(
        int(os.environ.get("CRASH_PER_IP_HOUR", 20)), int(os.environ.get("CRASH_PER_DAY", 2000))
    )
    bind = args.bind or os.environ.get("CRASH_BIND", "127.0.0.1:8765")
    server = make_server(bind, store, limiter, os.environ.get("CRASH_TRUST_PROXY") == "1")
    print(f"crash-collector hört auf {bind}", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_list(args) -> int:
    rows = _store_from_env(args).query(
        "SELECT id, received_at, app_version, kind, signature, os, python, pyside, qpa"
        " FROM reports ORDER BY id DESC LIMIT ?", (args.limit,)
    )
    for r in rows:
        print(f"{r['id']:>5}  {r['received_at']}  v{r['app_version']:<8} {r['kind']:<13}"
              f" {r['signature']}  {r['os'] or '-'} | py {r['python']} | PySide {r['pyside']} | {r['qpa']}")
    return 0


def cmd_groups(args) -> int:
    rows = _store_from_env(args).query(
        "SELECT signature, kind, COUNT(*) AS n, COUNT(DISTINCT install_id) AS users,"
        " MAX(received_at) AS last, MAX(id) AS example FROM reports"
        " GROUP BY signature ORDER BY n DESC"
    )
    print(f"{'Signatur':<13} {'Art':<13} {'Anzahl':>6} {'Install.':>8}  Zuletzt                    Beispiel")
    for r in rows:
        print(f"{r['signature']:<13} {r['kind']:<13} {r['n']:>6} {r['users']:>8}  {r['last']}  show {r['example']}")
    return 0


def cmd_show(args) -> int:
    store = _store_from_env(args)
    key = args.id
    rows = store.query(
        "SELECT payload FROM reports WHERE id = ? OR report_id = ?",
        (int(key) if key.isdigit() else -1, key),
    )
    if not rows:
        print("nicht gefunden", file=sys.stderr)
        return 1
    report = json.loads(rows[0]["payload"])
    stderr = report.pop("stderr_tail", "")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("\n--- stderr (Ende) ---\n" + stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    parser.add_argument("--db", help="Pfad der Datenbank (sonst CRASH_DB)")
    sub = parser.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("serve")
    p.add_argument("--bind")
    p.set_defaults(func=cmd_serve)
    p = sub.add_parser("list")
    p.add_argument("--limit", type=int, default=30)
    p.set_defaults(func=cmd_list)
    sub.add_parser("groups").set_defaults(func=cmd_groups)
    p = sub.add_parser("show")
    p.add_argument("id")
    p.set_defaults(func=cmd_show)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
