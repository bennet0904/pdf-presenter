import json
import zipfile

import pytest

from pdf_presenter.project import NOTES_ENTRY, PDF_ENTRY, Project, ProjectError

FAKE_PDF = b"%PDF-1.4\n% fake test pdf\n%%EOF\n"


@pytest.fixture
def pdf_file(tmp_path):
    path = tmp_path / "Vortrag.pdf"
    path.write_bytes(FAKE_PDF)
    return path


def test_open_pdf_creates_empty_project(pdf_file):
    project = Project.open(pdf_file)
    assert project.pdf_bytes == FAKE_PDF
    assert project.title == "Vortrag"
    assert project.notes == {}
    assert project.path is None
    assert not project.dirty


def test_roundtrip_keeps_pdf_and_notes(pdf_file, tmp_path):
    project = Project.open(pdf_file)
    project.set_note(0, "Begrüßung – Ümlaute ✓")
    project.set_note(3, "Zeile 1\nZeile 2")
    assert project.dirty

    saved = project.save(tmp_path / "out" / "Vortrag.pdfpres")
    assert not project.dirty
    assert project.path == saved

    loaded = Project.open(saved)
    assert loaded.pdf_bytes == FAKE_PDF
    assert loaded.notes == {0: "Begrüßung – Ümlaute ✓", 3: "Zeile 1\nZeile 2"}
    assert loaded.title == "Vortrag"
    assert loaded.path == saved


def test_save_appends_suffix(pdf_file, tmp_path):
    project = Project.open(pdf_file)
    saved = project.save(tmp_path / "talk")
    assert saved.name == "talk.pdfpres"
    assert saved.is_file()


def test_save_without_path_raises(pdf_file):
    with pytest.raises(ProjectError):
        Project.open(pdf_file).save()


def test_empty_note_removes_entry(pdf_file):
    project = Project.open(pdf_file)
    project.set_note(2, "x")
    project.set_note(2, "")
    assert project.notes == {}


def test_set_same_note_does_not_mark_dirty(pdf_file):
    project = Project.open(pdf_file)
    project.set_note(1, "")
    assert not project.dirty


def test_non_pdf_is_rejected(tmp_path):
    path = tmp_path / "x.pdf"
    path.write_text("hallo")
    with pytest.raises(ProjectError):
        Project.open(path)


def test_missing_file(tmp_path):
    with pytest.raises(ProjectError):
        Project.open(tmp_path / "gibtsnicht.pdf")


def test_zip_without_pdf_is_rejected(tmp_path):
    path = tmp_path / "kaputt.pdfpres"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(NOTES_ENTRY, "{}")
    with pytest.raises(ProjectError):
        Project.open(path)


def test_invalid_note_keys_are_ignored(tmp_path):
    path = tmp_path / "p.pdfpres"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(PDF_ENTRY, FAKE_PDF)
        zf.writestr(
            NOTES_ENTRY,
            json.dumps({"version": 1, "notes": {"1": "ok", "abc": "x", "-1": "y", "2": 5}}),
        )
    assert Project.open(path).notes == {1: "ok"}


def test_newer_format_is_rejected(tmp_path):
    path = tmp_path / "neu.pdfpres"
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(PDF_ENTRY, FAKE_PDF)
        zf.writestr(NOTES_ENTRY, json.dumps({"version": 99}))
    with pytest.raises(ProjectError):
        Project.open(path)
