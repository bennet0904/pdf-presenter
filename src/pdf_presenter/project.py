"""Projektmodell: eine PDF plus Sprechernotizen pro Folie.

Gespeichert wird als ``.pdfpres`` – ein ZIP-Archiv mit

* ``presentation.pdf`` – die unveränderte Original-PDF
* ``notes.json``       – ``{"version": 1, "title": "...", "notes": {"0": "..."}}``

Dieses Modul ist bewusst Qt-frei, damit es einfach testbar ist.
"""

from __future__ import annotations

import json
import os
import tempfile
import zipfile
from dataclasses import dataclass, field
from pathlib import Path

PROJECT_SUFFIX = ".pdfpres"
FORMAT_VERSION = 1
PDF_ENTRY = "presentation.pdf"
NOTES_ENTRY = "notes.json"


class ProjectError(Exception):
    """Datei konnte nicht geladen oder gespeichert werden."""


def _looks_like_pdf(data: bytes) -> bool:
    # Laut Spezifikation darf vor dem Header noch etwas Müll stehen.
    return b"%PDF-" in data[:1024]


@dataclass
class Project:
    pdf_bytes: bytes
    title: str
    notes: dict[int, str] = field(default_factory=dict)
    path: Path | None = None  # Pfad der .pdfpres-Datei, None = noch nie gespeichert
    dirty: bool = False

    # ------------------------------------------------------------------ laden
    @classmethod
    def open(cls, path: str | os.PathLike) -> "Project":
        """Öffnet eine ``.pdf`` (neues Projekt) oder eine ``.pdfpres``."""
        path = Path(path)
        if not path.is_file():
            raise ProjectError(f"Datei nicht gefunden: {path}")
        if zipfile.is_zipfile(path):
            return cls._load_project(path)
        return cls._from_pdf(path)

    @classmethod
    def _from_pdf(cls, path: Path) -> "Project":
        try:
            data = path.read_bytes()
        except OSError as exc:
            raise ProjectError(f"Datei kann nicht gelesen werden: {exc}") from exc
        if not _looks_like_pdf(data):
            raise ProjectError(f"{path.name} ist keine PDF-Datei.")
        return cls(pdf_bytes=data, title=path.stem)

    @classmethod
    def _load_project(cls, path: Path) -> "Project":
        try:
            with zipfile.ZipFile(path) as zf:
                names = set(zf.namelist())
                if PDF_ENTRY not in names:
                    raise ProjectError(f"{path.name} enthält keine Präsentation.")
                pdf_bytes = zf.read(PDF_ENTRY)
                meta = json.loads(zf.read(NOTES_ENTRY)) if NOTES_ENTRY in names else {}
        except (zipfile.BadZipFile, OSError, json.JSONDecodeError, UnicodeDecodeError) as exc:
            raise ProjectError(f"{path.name} ist beschädigt: {exc}") from exc

        if not _looks_like_pdf(pdf_bytes):
            raise ProjectError(f"{path.name} enthält keine gültige PDF.")
        if not isinstance(meta, dict):
            raise ProjectError(f"{path.name}: notes.json hat ein ungültiges Format.")
        version = meta.get("version", FORMAT_VERSION)
        if isinstance(version, int) and version > FORMAT_VERSION:
            raise ProjectError(
                f"{path.name} wurde mit einer neueren Version erstellt (Format {version})."
            )

        notes: dict[int, str] = {}
        raw_notes = meta.get("notes", {})
        if isinstance(raw_notes, dict):
            for key, text in raw_notes.items():
                try:
                    page = int(key)
                except (TypeError, ValueError):
                    continue
                if page >= 0 and isinstance(text, str) and text:
                    notes[page] = text

        title = meta.get("title")
        if not isinstance(title, str) or not title:
            title = path.stem
        return cls(pdf_bytes=pdf_bytes, title=title, notes=notes, path=path)

    # -------------------------------------------------------------- speichern
    def save(self, path: str | os.PathLike | None = None) -> Path:
        """Speichert atomar als ``.pdfpres``. Ohne ``path`` an den bisherigen Ort."""
        target = Path(path) if path is not None else self.path
        if target is None:
            raise ProjectError("Kein Speicherort angegeben.")
        if target.suffix.lower() != PROJECT_SUFFIX:
            target = target.with_name(target.name + PROJECT_SUFFIX)

        meta = {
            "version": FORMAT_VERSION,
            "title": self.title,
            "notes": {str(p): t for p, t in sorted(self.notes.items()) if t},
        }
        target.parent.mkdir(parents=True, exist_ok=True)
        fd, tmp_name = tempfile.mkstemp(prefix=".pdfpres-", dir=target.parent)
        try:
            with os.fdopen(fd, "wb") as fh, zipfile.ZipFile(fh, "w") as zf:
                # PDFs sind meist schon komprimiert -> STORED spart Zeit.
                zf.writestr(PDF_ENTRY, self.pdf_bytes, compress_type=zipfile.ZIP_STORED)
                zf.writestr(
                    NOTES_ENTRY,
                    json.dumps(meta, ensure_ascii=False, indent=2),
                    compress_type=zipfile.ZIP_DEFLATED,
                )
            os.replace(tmp_name, target)
        except OSError as exc:
            try:
                os.unlink(tmp_name)
            except OSError:
                pass
            raise ProjectError(f"Speichern fehlgeschlagen: {exc}") from exc

        self.path = target
        self.dirty = False
        return target

    # ---------------------------------------------------------------- notizen
    def note(self, page: int) -> str:
        return self.notes.get(page, "")

    def set_note(self, page: int, text: str) -> None:
        if self.notes.get(page, "") == text:
            return
        if text:
            self.notes[page] = text
        else:
            self.notes.pop(page, None)
        self.dirty = True

    @property
    def display_name(self) -> str:
        return self.path.name if self.path else self.title
