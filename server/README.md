# Absturzberichte: Server

`crash_collector.py` nimmt die Berichte von PDF Presenter an
(`POST https://<domain>/api/v1/reports`), speichert sie in SQLite und
schickt auf Wunsch eine aufbereitete E-Mail. Es läuft als Docker-Container und
braucht nur die Python-Standardbibliothek. TLS übernimmt ein Reverse-Proxy.

`<domain>` steht in dieser Anleitung für die Subdomain, unter der der Server
erreichbar ist. Sie muss mit `DEFAULT_ENDPOINT` in
`src/pdf_presenter/crashreport.py` und den Dateien in `deploy/` übereinstimmen.

## Einrichten auf dem VPS

1. **DNS:** A-Record für `<domain>` auf die IPv4 des VPS, bei IPv6 zusätzlich
   einen AAAA-Record.
2. **Dateien kopieren:** den Ordner `server/` auf den VPS, z. B. nach
   `/opt/crash-collector`.
3. **Mail einrichten** (optional, ohne Mail werden Berichte nur gespeichert):
   ```bash
   cd /opt/crash-collector
   cp .env.example .env            # Empfänger, SMTP-Server, Benutzer eintragen
   printf '%s' 'SMTP-PASSWORT' > smtp_password.txt
   chmod 600 smtp_password.txt
   ```
   Ohne Mail trotzdem eine leere `smtp_password.txt` anlegen (`touch smtp_password.txt`),
   Docker Compose verlangt die Datei.
4. **Starten:**
   - Läuft auf dem VPS schon ein Reverse-Proxy: `docker compose up -d --build`
     und den Block aus `deploy/Caddyfile` bzw. `deploy/nginx.conf` übernehmen.
     Der Container ist nur unter `127.0.0.1:8765` erreichbar.
   - Gibt es noch keinen: `docker compose --profile caddy up -d --build`. Dann
     startet zusätzlich ein Caddy, belegt die Ports 80 und 443 und holt das
     TLS-Zertifikat selbst.
5. **Prüfen:**
   ```bash
   curl https://<domain>/healthz                             # {"ok": true}
   docker compose exec crash-collector crash_collector.py testmail
   ```
   `testmail` schickt eine Beispielmail (oder den neuesten Bericht) an die
   eingetragenen Empfänger.

## E-Mails

- Eine **neue Absturzursache** wird sofort gemeldet (Betreff „Neuer Absturz: …“).
- Eine **bekannte Ursache** höchstens alle `CRASH_MAIL_REPEAT_HOURS` Stunden
  (Standard 24), mit der Zahl der Berichte seit der letzten Mail. Mit `0` kommt
  zu jedem Bericht eine Mail.
- Inhalt: Programmversion, Art des Absturzes, Laufzeit, ob gerade präsentiert
  wurde, Python/PySide/Qt, System, Grafiksystem, Bildschirme, Verlauf der Ursache
  (Anzahl, betroffene Installationen, erstmals gesehen) und die letzten 80 Zeilen
  der Fehlerausgabe. Bei bekannten Ursachen steht oben ein Hinweis.
- Scheitert der Versand, steht das im Log (`docker compose logs crash-collector`).
  Der nächste Bericht dieser Ursache löst dann erneut eine Mail aus.

Typische Einstellungen in `.env`:

| Anbieter | `CRASH_SMTP_HOST` | Port | `CRASH_SMTP_SECURITY` |
|---|---|---|---|
| Eigener Mailserver / die meisten Hoster | smtp.… | 587 | starttls |
| Hoster mit SSL ab Verbindungsbeginn | smtp.… | 465 | ssl |

## Berichte ansehen

```bash
docker compose exec crash-collector crash_collector.py groups     # nach Ursache gruppiert
docker compose exec crash-collector crash_collector.py list       # die neuesten
docker compose exec crash-collector crash_collector.py show 42    # ein Bericht komplett
```

Die Datenbank liegt im Docker-Volume `data` (`/data/reports.sqlite3` im Container).
Sicherung zum Beispiel mit
`docker compose cp crash-collector:/data/reports.sqlite3 ./backup.sqlite3`.

## Aktualisieren

```bash
docker compose up -d --build
```

## Schutz und Datenschutz

- Höchstens 256 KB pro Bericht, Felder und Längen werden geprüft.
- Rate-Limit: 20 Berichte pro IP und Stunde, 2000 pro Tag insgesamt
  (`CRASH_PER_IP_HOUR`, `CRASH_PER_DAY`).
- IP-Adressen werden weder gespeichert noch geloggt. Sie liegen nur für das
  Rate-Limit kurz im Arbeitsspeicher.
- Der Container läuft als unprivilegierter Benutzer, mit schreibgeschütztem
  Dateisystem (außer dem Daten-Volume), ohne Linux-Capabilities und mit
  Speichergrenze. Das SMTP-Passwort kommt als Docker-Secret, nicht als
  Umgebungsvariable.
- Die App entfernt vor dem Senden Home-Verzeichnis, Benutzernamen, Pfade und
  Namen von PDF-Dateien.

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
