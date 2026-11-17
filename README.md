# PDF Presenter

PDF-Präsentationen wie in PowerPoint halten: Auf dem eigenen Bildschirm die
**Presenteransicht** (aktuelle + nächste Folie, Notizen, Laufzeit-Timer, Uhr),
auf dem Beamer die Folien im **Vollbild**. Läuft unter Windows und Linux.

## Funktionen

- PDF öffnen (Menü, Drag & Drop, Kommandozeile) – Projekte als `.pdfpres` speichern
  und wieder öffnen (PDF + Sprechernotizen in **einer** Datei)
- Sprechernotizen pro Folie – im Editor und während der Präsentation bearbeitbar
- Presenteransicht mit aktueller/nächster Folie, Notizen (Schriftgröße anpassbar),
  Folienzähler, Uhrzeit und **Timer, wie lange die Präsentation schon läuft**
  (startet automatisch, Pause/Reset)
- Automatische Monitorerkennung, Bildschirme tauschen, Reaktion auf An-/Abstecken
- Nur ein Monitor? Publikumsansicht im Fenster (zum Proben) oder „Nur Vollbild“
- Schwarz-/Weißbild, Folienübersicht, Sprung per Foliennummer, Presenter-Clicker
- Zuletzt geöffnete Dateien, Warnung bei ungespeicherten Änderungen

## Starten

```bash
python -m venv .venv
.venv/Scripts/pip install -e ".[dev]"     # Linux: .venv/bin/pip
.venv/Scripts/pdf-presenter vortrag.pdf    # Linux: .venv/bin/pdf-presenter
```

Eine Beispiel-PDF zum Ausprobieren: `python scripts/make_sample_pdf.py beispiel.pdf`

Unter Linux braucht Qt ggf. Systempakete (z. B. `libxcb-cursor0` unter Debian/Ubuntu).

## Tastenkürzel

| Taste | Aktion |
|---|---|
| F5 / Umschalt+F5 | Präsentation von vorn / ab aktueller Folie |
| Strg+F5 | Nur Vollbild ohne Presenter |
| → ↓ Bild↓ Leertaste Enter, Linksklick, Mausrad | Nächste Folie |
| ← ↑ Bild↑ Rücktaste, Rechtsklick | Vorherige Folie |
| Pos1 / Ende | Erste / letzte Folie |
| Zahl + Enter | Zu Folie springen |
| B oder `.` / W oder `,` | Bildschirm schwarz / weiß an/aus – bleibt beim Folienwechsel aktiv, sodass man unbemerkt weiterschalten kann |
| G | Folienübersicht |
| T | Timer anhalten / fortsetzen |
| Strg + / Strg − | Notizen größer / kleiner |
| Strg+S | Speichern |
| Esc | Präsentation beenden (im Notizfeld: Fokus verlassen) |

Im Notizfeld tippt man ganz normal; nur Bild↑/Bild↓ (Clicker) schalten dort weiter.

## Dateiformat `.pdfpres`

ZIP-Archiv mit `presentation.pdf` (Original, unverändert) und `notes.json`:

```json
{"version": 1, "title": "Vortrag", "notes": {"0": "Notiz zu Folie 1"}}
```

## Tests

```bash
.venv/Scripts/python -m pytest
```

## Als Programm bauen

- Windows: `scripts\build_windows.ps1` → `dist\PDF-Presenter\PDF-Presenter.exe`
- Linux: `scripts/build_linux.sh` → `dist/PDF-Presenter/PDF-Presenter`

PyInstaller kann nicht cross-kompilieren – die Linux-Version muss auf Linux gebaut werden.

## CI/CD mit GitHub Actions

- **CI** (`.github/workflows/ci.yml`): Bei jedem Push auf `main`/`master` und bei Pull Requests
  laufen die Tests unter Windows und Linux.
- **Release** (`.github/workflows/release.yml`): Testet, baut mit PyInstaller für Windows
  (`.zip`) und Linux (`.tar.gz`) und erstellt ein GitHub-Release mit beiden Downloads.

Release auslösen – entweder per Tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

oder auf GitHub unter **Actions → Release → Run workflow** die Version (z. B. `v0.1.0`)
eintragen. Der Tag wird dann automatisch angelegt.

## Hinweis Wayland

Unter Wayland dürfen Programme Fenster nicht selbst auf einen bestimmten Monitor
setzen. Falls der Viewer auf dem falschen Bildschirm landet: „Tauschen“ drücken
oder die App mit `QT_QPA_PLATFORM=xcb` (XWayland) starten.
