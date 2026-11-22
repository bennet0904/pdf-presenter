#!/usr/bin/env python3
"""Nimmt Absturzberichte von PDF Presenter entgegen, speichert sie und meldet sie per E-Mail.

Nur Standardbibliothek. Läuft als Docker-Container hinter einem
Reverse-Proxy, der TLS übernimmt. Siehe server/README.md.

    crash_collector.py serve                  Server starten
    crash_collector.py list [--limit N]       letzte Berichte
    crash_collector.py groups                 Berichte nach Ursache gruppiert
    crash_collector.py show <id|report_id>    einen Bericht vollständig anzeigen
    crash_collector.py testmail               Testmail (neuester Bericht oder Beispiel)

Konfiguration über Umgebungsvariablen:

    CRASH_DB               Pfad der Datenbank       (Standard: ./reports.sqlite3)
    CRASH_BIND             Adresse:Port             (Standard: 127.0.0.1:8765)
    CRASH_TRUSTED_PROXIES  Netze, deren X-Forwarded-For für das Rate-Limit
                           geglaubt wird, z. B. "127.0.0.1,172.16.0.0/12"
    CRASH_PER_IP_HOUR      Berichte pro IP und Stunde   (Standard: 20)
    CRASH_PER_DAY          Berichte insgesamt pro Tag   (Standard: 2000)

E-Mail (aktiv, sobald CRASH_MAIL_TO und CRASH_SMTP_HOST gesetzt sind):

    CRASH_MAIL_TO             Empfänger, mehrere mit Komma getrennt
    CRASH_MAIL_FROM           Absender                (Standard: CRASH_SMTP_USER)
    CRASH_SMTP_HOST           SMTP-Server
    CRASH_SMTP_PORT           Port                    (Standard: 587, bei ssl 465)
    CRASH_SMTP_SECURITY       starttls | ssl | none   (Standard: starttls)
    CRASH_SMTP_USER           Benutzername (leer = ohne Anmeldung)
    CRASH_SMTP_PASSWORD_FILE  Datei mit dem Passwort (Docker-Secret), alternativ
    CRASH_SMTP_PASSWORD       das Passwort direkt
    CRASH_MAIL_REPEAT_HOURS   Neue Ursachen kommen sofort, bekannte höchstens
                              alle N Stunden (Standard: 24, 0 = jeder Bericht)
"""

from __future__ import annotations

import argparse
import hashlib
import html
import ipaddress
import json
import os
import queue
import re
import smtplib
import sqlite3
import ssl
import sys
import threading
import time
import uuid
from collections import deque
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from email.message import EmailMessage
from http import HTTPStatus
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

API_PATH = "/api/v1/reports"
MAX_BODY = 256 * 1024
SCHEMA = 1
APP_NAME = "PDF Presenter"

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
CREATE TABLE IF NOT EXISTS mails (
    signature    TEXT PRIMARY KEY,
    last_sent_at TEXT NOT NULL,
    last_id      INTEGER NOT NULL
);
"""


def _utcnow() -> datetime:
    return datetime.now(timezone.utc)


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


def _number(value) -> bool:
    return isinstance(value, (int, float)) and not isinstance(value, bool)


def _env(value) -> dict:
    """Umgebung: flaches Objekt aus Texten/Zahlen, plus Liste ``screens``."""
    if not isinstance(value, dict) or len(value) > 40:
        raise Invalid("environment: Objekt mit höchstens 40 Feldern erwartet")
    clean: dict = {}
    for key, item in value.items():
        if not isinstance(key, str) or len(key) > 40:
            raise Invalid("environment: ungültiger Schlüssel")
        if key == "screens":
            if not isinstance(item, list) or len(item) > 16 or not all(isinstance(s, dict) for s in item):
                raise Invalid("environment.screens: Liste von Objekten erwartet")
            clean[key] = [
                {k: v for k, v in s.items() if k in ("w", "h", "dpr", "hz") and _number(v)} for s in item
            ]
        elif item is None or isinstance(item, bool) or _number(item):
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
    if not _number(uptime) or uptime < 0:
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
        self._db.row_factory = sqlite3.Row
        self._db.execute("PRAGMA journal_mode=WAL")
        self._db.executescript(SCHEMA_SQL)

    def add(self, report: dict) -> tuple[bool, int, str]:
        """Speichert einen Bericht. Liefert (neu?, Datenbank-ID, Signatur)."""
        env = report["environment"]
        sig = signature(report)
        with self._lock, self._db:
            row = self._db.execute(
                "SELECT id FROM reports WHERE report_id = ?", (report["report_id"],)
            ).fetchone()
            if row:
                return False, row["id"], sig
            cur = self._db.execute(
                "INSERT INTO reports (received_at, report_id, install_id, app_version, kind,"
                " signature, os, python, pyside, qpa, payload)"
                " VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)",
                (
                    _utcnow().isoformat(timespec="seconds"),
                    report["report_id"], report["install_id"], report["app_version"],
                    report["kind"], sig,
                    " ".join(str(env.get(k, "")) for k in ("os", "distro")).strip() or None,
                    env.get("python"), env.get("pyside"), env.get("qpa"),
                    json.dumps(report, ensure_ascii=False),
                ),
            )
            return True, cur.lastrowid, sig

    def query(self, sql: str, args: tuple = ()) -> list[sqlite3.Row]:
        with self._lock:
            return self._db.execute(sql, args).fetchall()

    def stats(self, sig: str) -> dict:
        """Zahlen zu einer Absturzursache, für die E-Mail."""
        row = self.query(
            "SELECT COUNT(*) AS n, COUNT(DISTINCT install_id) AS installs,"
            " MIN(received_at) AS first, GROUP_CONCAT(DISTINCT app_version) AS versions"
            " FROM reports WHERE signature = ?", (sig,)
        )[0]
        mail = self.query("SELECT last_sent_at, last_id FROM mails WHERE signature = ?", (sig,))
        since = None
        if mail:
            since = self.query(
                "SELECT COUNT(*) AS n FROM reports WHERE signature = ? AND id > ?",
                (sig, mail[0]["last_id"]),
            )[0]["n"]
        return {
            "count": row["n"], "installs": row["installs"], "first": row["first"],
            "versions": row["versions"] or "",
            "last_mail": mail[0]["last_sent_at"] if mail else None,
            "since_last_mail": since,
        }

    def mark_mailed(self, sig: str, report_id: int) -> None:
        with self._lock, self._db:
            self._db.execute(
                "INSERT INTO mails (signature, last_sent_at, last_id) VALUES (?, ?, ?)"
                " ON CONFLICT(signature) DO UPDATE SET"
                " last_sent_at = excluded.last_sent_at, last_id = excluded.last_id",
                (sig, _utcnow().isoformat(timespec="seconds"), report_id),
            )


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


def parse_networks(spec: str) -> list:
    return [ipaddress.ip_network(p.strip(), strict=False) for p in spec.split(",") if p.strip()]


# ------------------------------------------------------------------ E-Mail
@dataclass
class MailConfig:
    to: list[str]
    sender: str
    host: str
    port: int
    security: str  # starttls | ssl | none
    user: str
    password: str
    repeat_hours: float

    @classmethod
    def from_env(cls, env=os.environ) -> MailConfig | None:
        to = [a.strip() for a in env.get("CRASH_MAIL_TO", "").split(",") if a.strip()]
        host = env.get("CRASH_SMTP_HOST", "").strip()
        if not to or not host:
            return None
        security = env.get("CRASH_SMTP_SECURITY", "starttls").strip().lower()
        if security not in ("starttls", "ssl", "none"):
            raise SystemExit(f"CRASH_SMTP_SECURITY: starttls, ssl oder none erwartet, nicht {security!r}")
        password = env.get("CRASH_SMTP_PASSWORD", "")
        if env.get("CRASH_SMTP_PASSWORD_FILE"):
            with open(env["CRASH_SMTP_PASSWORD_FILE"], encoding="utf-8") as fh:
                password = fh.read().strip()
        user = env.get("CRASH_SMTP_USER", "").strip()
        return cls(
            to=to,
            sender=env.get("CRASH_MAIL_FROM", "").strip() or user,
            host=host,
            port=int(env.get("CRASH_SMTP_PORT") or (465 if security == "ssl" else 587)),
            security=security,
            user=user,
            password=password,
            repeat_hours=float(env.get("CRASH_MAIL_REPEAT_HOURS", 24)),
        )


def _py_below_312(env: dict) -> bool:
    try:
        return tuple(int(x) for x in str(env.get("python", "")).split(".")[:2]) < (3, 12)
    except ValueError:
        return False


# Bekannte Ursachen, die in der Mail als Hinweis erscheinen: (Prüfung, Text)
KNOWN_CAUSES = [
    (
        lambda r, e: r["kind"] == "python_fatal" and e.get("pyside") == "6.12.0" and _py_below_312(e),
        "Bekannt: PySide6 6.12.0 beschädigt unter Python < 3.12 die Referenzzähler von "
        "None/True/False. Abhilfe ist PySide6 < 6.12 oder Python 3.12+. Betroffen sind "
        "Installationen, die vor dieser Korrektur eingerichtet wurden.",
    ),
    (
        lambda r, e: r["kind"] == "wayland",
        "Wayland-Protokollfehler: Der Compositor hat die Verbindung getrennt. Häufige Ursache "
        "sind Fenstergrößen, die nicht zum maximierten oder Vollbild-Zustand passen.",
    ),
    (
        lambda r, e: r["kind"] == "killed",
        "Der Prozess wurde mit SIGKILL beendet. Unter Linux ist das meist der OOM-Killer "
        "oder systemd-oomd (Arbeitsspeicher voll).",
    ),
]


def _fmt_duration(seconds: float) -> str:
    seconds = int(seconds)
    if seconds < 60:
        return f"{seconds} s"
    if seconds < 3600:
        return f"{seconds // 60} min {seconds % 60} s"
    return f"{seconds // 3600} h {seconds // 60 % 60} min"


def headline(report: dict) -> str:
    text = report["stderr_tail"]
    fatal = _FATAL_RE.search(text)
    if fatal:
        return text[fatal.start():].splitlines()[0][:140]
    if report["kind"] == "wayland":
        return "Wayland-Verbindung abgebrochen"
    if report.get("signal"):
        return f"Beendet durch {report['signal']}"
    return f"Unerwartet beendet (Exit-Code {report['exit_code']})"


def _safe(check, report, env) -> bool:
    try:
        return bool(check(report, env))
    except Exception:  # noqa: BLE001 - ein kaputter Hinweis darf die Mail nicht verhindern
        return False


def build_mail(report: dict, row_id: int, sig: str, stats: dict, cfg: MailConfig) -> EmailMessage:
    """Baut eine lesbare Mail (Text und HTML) zu einem Bericht."""
    env = report["environment"]
    new = stats["count"] <= 1
    title = headline(report)
    if new:
        subject = f"[{APP_NAME}] Neuer Absturz: {title}"
    else:
        subject = f"[{APP_NAME}] Absturz erneut ({stats['count']}×): {title}"

    screens = ", ".join(
        f"{s.get('w')}×{s.get('h')} @{s.get('dpr')}x, {s.get('hz')} Hz" for s in env.get("screens", [])
    ) or "unbekannt"
    graphics = " / ".join(str(x) for x in (env.get("qpa"), env.get("session_type"), env.get("desktop")) if x)
    system = " ".join(str(x) for x in (env.get("distro") or env.get("os"), env.get("os_release")) if x)
    rows = [
        ("Programmversion", report["app_version"] + (" (gebündelt)" if env.get("frozen") else "")),
        ("Art", report["kind"]),
        ("Exit-Code / Signal", f"{report['exit_code']} / {report.get('signal') or '–'}"),
        ("Laufzeit bis Absturz", _fmt_duration(report["uptime_s"])),
        ("Während Präsentation", {True: "ja", False: "nein"}.get(env.get("presenting"), "unbekannt")),
        ("Python", env.get("python", "?")),
        ("PySide6 / Qt", f"{env.get('pyside', '?')} / {env.get('qt', '?')}"),
        ("System", system or "?"),
        ("Grafik", graphics or "?"),
        ("Bildschirme", screens),
        ("Absturzzeit (UTC)", report["crashed_at"]),
    ]
    history = [
        ("Signatur", sig),
        ("Berichte gesamt", str(stats["count"])),
        ("Betroffene Installationen", str(stats["installs"])),
        ("Erstmals gesehen", stats["first"] or "–"),
        ("Versionen", stats["versions"]),
    ]
    if stats["since_last_mail"] is not None:
        history.append(("Seit der letzten Mail", f"{stats['since_last_mail']} weitere (letzte Mail {stats['last_mail']})"))
    hints = [text for check, text in KNOWN_CAUSES if _safe(check, report, env)]
    log_lines = report["stderr_tail"].rstrip().splitlines()[-80:]
    show_cmd = f"docker compose exec crash-collector crash_collector.py show {row_id}"

    # Text-Fassung
    width = max(len(k) for k, _ in rows + history)
    text = [title, "=" * min(len(title), 72), ""]
    text += [f"Hinweis: {h}" for h in hints] + ([""] if hints else [])
    text += [f"{k:<{width}}  {v}" for k, v in rows] + [""]
    text += [f"{k:<{width}}  {v}" for k, v in history] + [""]
    text += ["Fehlerausgabe (Ende):", "-" * 40, *log_lines, "-" * 40, "", f"Vollständig: {show_cmd}"]

    # HTML-Fassung
    def table(items) -> str:
        return "".join(
            "<tr><th align=left style='width:210px;padding:2px 14px 2px 0;color:#555;font-weight:normal'>"
            f"{html.escape(k)}</th><td style='padding:2px 0'>{html.escape(str(v))}</td></tr>"
            for k, v in items
        )

    hint_html = "".join(
        "<p style='background:#fff4d6;border-left:4px solid #f0b429;padding:8px 12px'>"
        f"{html.escape(h)}</p>" for h in hints
    )
    if new:
        badge = "<span style='background:#c62828;color:#fff;padding:2px 8px;border-radius:4px'>NEU</span>"
    else:
        badge = (f"<span style='background:#555;color:#fff;padding:2px 8px;border-radius:4px'>"
                 f"{stats['count']}×</span>")
    html_body = f"""<!doctype html><html><body style="font-family:sans-serif;font-size:14px;color:#222">
<h2 style="margin-bottom:4px">{badge} {html.escape(title)}</h2>
{hint_html}
<h3>Absturz</h3><table>{table(rows)}</table>
<h3>Verlauf dieser Ursache</h3><table>{table(history)}</table>
<h3>Fehlerausgabe (letzte {len(log_lines)} Zeilen)</h3>
<pre style="background:#f4f4f6;padding:10px;font-size:12px;white-space:pre-wrap">{html.escape(chr(10).join(log_lines))}</pre>
<p style="color:#777">Vollständiger Bericht auf dem Server: <code>{html.escape(show_cmd)}</code></p>
</body></html>"""

    msg = EmailMessage()
    msg["Subject"] = subject
    msg["From"] = cfg.sender
    msg["To"] = ", ".join(cfg.to)
    msg.set_content("\n".join(text))
    msg.add_alternative(html_body, subtype="html")
    return msg


def send_mail(cfg: MailConfig, msg: EmailMessage) -> None:
    context = ssl.create_default_context()
    if cfg.security == "ssl":
        smtp = smtplib.SMTP_SSL(cfg.host, cfg.port, timeout=30, context=context)
    else:
        smtp = smtplib.SMTP(cfg.host, cfg.port, timeout=30)
    with smtp:
        if cfg.security == "starttls":
            smtp.starttls(context=context)
        if cfg.user:
            smtp.login(cfg.user, cfg.password)
        smtp.send_message(msg)


class Notifier:
    """Verschickt Mails in einem eigenen Thread, damit Clients nie warten müssen."""

    def __init__(self, store: Store, cfg: MailConfig, sender=send_mail):
        self._store = store
        self._cfg = cfg
        self._send = sender
        self._queue: queue.Queue = queue.Queue(maxsize=500)
        threading.Thread(target=self._run, daemon=True, name="mailer").start()

    def report_added(self, report: dict, row_id: int, sig: str) -> None:
        try:
            self._queue.put_nowait((report, row_id, sig))
        except queue.Full:
            print("Mail-Warteschlange voll, Bericht wird nicht gemailt", file=sys.stderr)

    def due(self, sig: str) -> bool:
        """Neue Ursachen sofort, bekannte höchstens alle ``repeat_hours`` Stunden."""
        if self._cfg.repeat_hours <= 0:
            return True
        rows = self._store.query("SELECT last_sent_at FROM mails WHERE signature = ?", (sig,))
        if not rows:
            return True
        last = datetime.fromisoformat(rows[0]["last_sent_at"])
        return _utcnow() - last >= timedelta(hours=self._cfg.repeat_hours)

    def process(self, report: dict, row_id: int, sig: str) -> bool:
        if not self.due(sig):
            return False
        msg = build_mail(report, row_id, sig, self._store.stats(sig), self._cfg)
        try:
            self._send(self._cfg, msg)
        except (OSError, smtplib.SMTPException) as exc:
            print(f"Mail fehlgeschlagen: {exc}", file=sys.stderr)
            return False
        self._store.mark_mailed(sig, row_id)
        return True

    def wait_idle(self) -> None:
        self._queue.join()

    def _run(self) -> None:
        while True:
            item = self._queue.get()
            try:
                self.process(*item)
            except Exception as exc:  # noqa: BLE001 - der Mail-Thread darf nie sterben
                print(f"Fehler beim Mailen: {exc!r}", file=sys.stderr)
            finally:
                self._queue.task_done()


# ------------------------------------------------------------------ HTTP
class Handler(BaseHTTPRequestHandler):
    server_version = "crash-collector"
    sys_version = ""
    timeout = 15  # langsame Clients nicht ewig festhalten

    store: Store
    limiter: RateLimiter
    trusted: list
    notifier: Notifier | None

    def log_message(self, fmt: str, *args) -> None:  # keine IP-Adressen ins Log
        status = args[1] if len(args) > 1 else ""
        sys.stderr.write(f"{self.command} {self.path} {status}\n")

    def _reply(self, status: HTTPStatus, body: dict) -> None:
        data = json.dumps(body).encode()
        self.send_response(status)
        self.send_header("Content-Type", "application/json")
        self.send_header("Content-Length", str(len(data)))
        self.end_headers()
        self.wfile.write(data)

    def _client_ip(self) -> str:
        ip = self.client_address[0]
        forwarded = self.headers.get("X-Forwarded-For", "")
        if forwarded and self.trusted:
            try:
                peer = ipaddress.ip_address(ip)
            except ValueError:
                return ip
            if any(peer in net for net in self.trusted):
                # Den letzten Eintrag setzt der eigene Proxy, frühere kann der Client fälschen.
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
        created, row_id, sig = self.store.add(report)
        if created and self.notifier is not None:
            self.notifier.report_added(report, row_id, sig)
        self._reply(HTTPStatus.CREATED if created else HTTPStatus.OK, {"id": row_id})


def make_server(
    bind: str,
    store: Store,
    limiter: RateLimiter,
    trusted_proxies: str = "",
    notifier: Notifier | None = None,
) -> ThreadingHTTPServer:
    host, _, port = bind.rpartition(":")
    handler = type("BoundHandler", (Handler,), {
        "store": store, "limiter": limiter, "notifier": notifier,
        "trusted": parse_networks(trusted_proxies),
    })
    server = ThreadingHTTPServer((host.strip("[]") or "127.0.0.1", int(port)), handler)
    server.daemon_threads = True
    return server


# ------------------------------------------------------------------- CLI
def _store(args) -> Store:
    return Store(args.db or os.environ.get("CRASH_DB", "reports.sqlite3"))


def cmd_serve(args) -> int:
    store = _store(args)
    limiter = RateLimiter(
        int(os.environ.get("CRASH_PER_IP_HOUR", 20)), int(os.environ.get("CRASH_PER_DAY", 2000))
    )
    cfg = MailConfig.from_env()
    notifier = Notifier(store, cfg) if cfg else None
    bind = args.bind or os.environ.get("CRASH_BIND", "127.0.0.1:8765")
    server = make_server(bind, store, limiter, os.environ.get("CRASH_TRUSTED_PROXIES", ""), notifier)
    mail_info = f"Mails über {cfg.host}" if cfg else "ohne Mail"
    print(f"crash-collector hört auf {bind} ({mail_info})", flush=True)
    try:
        server.serve_forever()
    except KeyboardInterrupt:
        pass
    return 0


def cmd_list(args) -> int:
    rows = _store(args).query(
        "SELECT id, received_at, app_version, kind, signature, os, python, pyside, qpa"
        " FROM reports ORDER BY id DESC LIMIT ?", (args.limit,)
    )
    for r in rows:
        print(f"{r['id']:>5}  {r['received_at']}  v{r['app_version']:<8} {r['kind']:<13}"
              f" {r['signature']}  {r['os'] or '-'} | py {r['python']} | PySide {r['pyside']} | {r['qpa']}")
    return 0


def cmd_groups(args) -> int:
    rows = _store(args).query(
        "SELECT signature, kind, COUNT(*) AS n, COUNT(DISTINCT install_id) AS users,"
        " MAX(received_at) AS last, MAX(id) AS example FROM reports"
        " GROUP BY signature ORDER BY n DESC"
    )
    print(f"{'Signatur':<13} {'Art':<13} {'Anzahl':>6} {'Install.':>8}  Zuletzt                    Beispiel")
    for r in rows:
        print(f"{r['signature']:<13} {r['kind']:<13} {r['n']:>6} {r['users']:>8}  {r['last']}  show {r['example']}")
    return 0


def _load(store: Store, key: str) -> tuple[int, dict] | None:
    rows = store.query(
        "SELECT id, payload FROM reports WHERE id = ? OR report_id = ?",
        (int(key) if key.isdigit() else -1, key),
    )
    return (rows[0]["id"], json.loads(rows[0]["payload"])) if rows else None


def cmd_show(args) -> int:
    found = _load(_store(args), args.id)
    if not found:
        print("nicht gefunden", file=sys.stderr)
        return 1
    report = dict(found[1])
    stderr = report.pop("stderr_tail", "")
    print(json.dumps(report, indent=2, ensure_ascii=False))
    print("\n--- stderr (Ende) ---\n" + stderr)
    return 0


SAMPLE_REPORT = {
    "schema": 1, "report_id": "00000000-0000-4000-8000-000000000000",
    "install_id": "00000000-0000-4000-8000-000000000001", "app_version": "0.1.0",
    "kind": "python_fatal", "exit_code": -6, "signal": "SIGABRT",
    "started_at": "2026-01-01T10:00:00+00:00", "crashed_at": "2026-01-01T10:12:00+00:00",
    "uptime_s": 720.0,
    "environment": {"python": "3.11.9", "pyside": "6.12.0", "qt": "6.12.0", "os": "Linux",
                    "distro": "Ubuntu 22.04.5 LTS", "qpa": "wayland", "session_type": "wayland",
                    "desktop": "ubuntu:GNOME", "presenting": True,
                    "screens": [{"w": 1920, "h": 1080, "dpr": 1.25, "hz": 60}]},
    "stderr_tail": "Fatal Python error: none_dealloc: deallocating None: bug likely caused by a "
                   "refcount error in a C extension\n\nCurrent thread (most recent call first):\n"
                   '  File "pdf_presenter/widgets/slide_view.py", line 99 in paintEvent\n',
}


def cmd_testmail(args) -> int:
    cfg = MailConfig.from_env()
    if cfg is None:
        print("Mail ist nicht eingerichtet: CRASH_MAIL_TO und CRASH_SMTP_HOST setzen.", file=sys.stderr)
        return 1
    store = _store(args)
    latest = store.query("SELECT id FROM reports ORDER BY id DESC LIMIT 1")
    if latest:
        row_id, report = _load(store, str(latest[0]["id"]))
        sig = signature(report)
        stats = store.stats(sig)
    else:
        row_id, report, sig = 0, SAMPLE_REPORT, signature(SAMPLE_REPORT)
        stats = {"count": 1, "installs": 1, "first": SAMPLE_REPORT["crashed_at"], "versions": "0.1.0",
                 "last_mail": None, "since_last_mail": None}
    msg = build_mail(report, row_id, sig, stats, cfg)
    msg.replace_header("Subject", "[Test] " + msg["Subject"])
    send_mail(cfg, msg)
    print(f"Testmail an {', '.join(cfg.to)} gesendet.")
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
    sub.add_parser("testmail").set_defaults(func=cmd_testmail)
    args = parser.parse_args(argv)
    return args.func(args)


if __name__ == "__main__":
    sys.exit(main())
