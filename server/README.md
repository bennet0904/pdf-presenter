# Absturzberichte: Server

`crash_collector.py` nimmt die Berichte von PDF Presenter an
(`POST https://<domain>/api/v1/reports`) und speichert sie in einer
SQLite-Datenbank. Es braucht nur Python 3.10 oder neuer, keine weiteren Pakete.
TLS übernimmt ein Reverse-Proxy (Caddy oder nginx).

## Einrichten auf dem VPS

1. **DNS:** A-Record für `<domain>` auf die IPv4 des VPS, bei IPv6 zusätzlich
   einen AAAA-Record.
2. **Programm kopieren:**
   ```bash
   sudo mkdir -p /opt/crash-collector
   sudo cp crash_collector.py /opt/crash-collector/
   ```
3. **Dienst einrichten:**
   ```bash
   sudo cp deploy/crash-collector.service /etc/systemd/system/
   sudo systemctl daemon-reload
   sudo systemctl enable --now crash-collector
   systemctl status crash-collector
   ```
4. **Reverse-Proxy:** Den Block aus `deploy/Caddyfile` in `/etc/caddy/Caddyfile`
   übernehmen und `sudo systemctl reload caddy`. Mit nginx stattdessen
   `deploy/nginx.conf` verwenden.
5. **Prüfen:** `curl https://<domain>/healthz` sollte `{"ok": true}` liefern.

## Berichte ansehen

Die Datenbank gehört dem Dienstbenutzer, deshalb mit `sudo`:

```bash
DB=/var/lib/crash-collector/reports.sqlite3
sudo python3 /opt/crash-collector/crash_collector.py --db $DB groups     # nach Ursache gruppiert
sudo python3 /opt/crash-collector/crash_collector.py --db $DB list       # die neuesten
sudo python3 /opt/crash-collector/crash_collector.py --db $DB show 42    # ein Bericht komplett
```

`groups` fasst gleiche Abstürze über eine Signatur zusammen (Art des Absturzes,
erste Zeile eines `Fatal Python error`, oberste Funktionen im Traceback) und
zeigt, wie viele Installationen betroffen sind.

## Schutz und Datenschutz

- Höchstens 256 KB pro Bericht, Felder und Längen werden geprüft.
- Rate-Limit: 20 Berichte pro IP und Stunde, 2000 pro Tag insgesamt
  (`CRASH_PER_IP_HOUR`, `CRASH_PER_DAY`).
- IP-Adressen werden weder gespeichert noch geloggt. Sie liegen nur für das
  Rate-Limit kurz im Arbeitsspeicher.
- Die App entfernt vor dem Senden Home-Verzeichnis, Benutzernamen und Namen
  von PDF-Dateien aus dem Bericht.
- Nach dem Abschalten der Funktion in der App wird nichts mehr gesendet.

## Format eines Berichts (Schema 1)

```json
{
  "schema": 1,
  "report_id": "UUID, pro Bericht",
  "install_id": "UUID, zufällig pro Installation",
  "app_version": "0.1.0",
  "kind": "python_fatal | wayland | signal | killed | exit",
  "exit_code": -6,
  "signal": "SIGABRT",
  "started_at": "2026-11-22T10:00:00+00:00",
  "crashed_at": "2026-11-22T10:12:31+00:00",
  "uptime_s": 751.2,
  "environment": {"python": "3.11.9", "pyside": "6.12.0", "qt": "6.12.0", "os": "Linux",
                  "distro": "Ubuntu 22.04.5 LTS", "qpa": "wayland", "desktop": "ubuntu:GNOME",
                  "screens": [{"w": 1920, "h": 1080, "dpr": 1.25, "hz": 60}], "presenting": true},
  "stderr_tail": "Fatal Python error: none_dealloc: ..."
}
```
