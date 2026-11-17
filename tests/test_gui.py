"""Durchstich durch die GUI im Offscreen-Modus: öffnen, Notizen, präsentieren, speichern."""

import pytest
from PySide6.QtCore import QCoreApplication, QEvent, QSettings, Qt
from PySide6.QtGui import QKeyEvent
from PySide6.QtTest import QTest

from pdf_presenter.project import Project


@pytest.fixture
def window(qapp, tmp_path, monkeypatch):
    QSettings.setDefaultFormat(QSettings.Format.IniFormat)
    QSettings.setPath(QSettings.Format.IniFormat, QSettings.Scope.UserScope, str(tmp_path / "settings"))
    qapp.setOrganizationName("pdf-presenter-tests")
    from pdf_presenter.windows.main_window import MainWindow

    win = MainWindow()
    win.show()
    yield win
    if win.project is not None:
        win.project.dirty = False
    win.close()


def press(widget, key, mods=Qt.KeyboardModifier.NoModifier, text=""):
    QCoreApplication.sendEvent(widget, QKeyEvent(QEvent.Type.KeyPress, key, mods, text))


def test_full_workflow(window, sample_pdf, tmp_path):
    assert window.open_path(sample_pdf)
    assert window.document.page_count == 5

    # Notizen im Editor
    window.thumbs.select_page(1)
    window.notes.setPlainText("Notiz für Folie 2")
    assert window.project.dirty
    assert window.windowTitle().startswith("beispiel*")

    # Präsentation ab aktueller Folie
    window.start_presentation(from_current=True)
    session = window.session
    ctrl = session.controller
    presenter = session.presenter
    assert ctrl.current_page == 1 and ctrl.timer_running
    assert presenter.notes.toPlainText() == "Notiz für Folie 2"
    assert session.audience.view.page() == 1

    press(presenter, Qt.Key.Key_Right)
    assert ctrl.current_page == 2
    assert session.audience.view.page() == 2
    assert presenter.next_view.page() == 3

    press(session.audience, Qt.Key.Key_Left)
    assert ctrl.current_page == 1

    # Zahl + Enter springt
    press(presenter, Qt.Key.Key_5); press(presenter, Qt.Key.Key_Return)
    assert ctrl.current_page == 4 and presenter.next_view.page() is None

    # Schwarz bleibt beim Durchschalten, Publikum sieht weiter schwarz
    press(presenter, Qt.Key.Key_B)
    assert ctrl.blank == "black"
    press(presenter, Qt.Key.Key_Left); press(presenter, Qt.Key.Key_Left)
    press(presenter, Qt.Key.Key_3); press(presenter, Qt.Key.Key_Return)
    assert ctrl.blank == "black" and ctrl.current_page == 2
    assert presenter.current_view.page() == 2 and presenter.black_btn.isChecked()
    assert session.audience.view._blank == "black"
    press(presenter, Qt.Key.Key_B)
    assert ctrl.blank == "" and not presenter.black_btn.isChecked()
    press(presenter, Qt.Key.Key_End)
    assert ctrl.current_page == 4

    # Timer pausieren
    press(presenter, Qt.Key.Key_T)
    assert not ctrl.timer_running

    # Notiz im Presenter tippen; Pfeiltasten bleiben im Textfeld, Bild↓ navigiert
    presenter.notes.setFocus()
    QTest.keyClicks(presenter.notes, "Schluss")
    press(presenter.notes, Qt.Key.Key_Left)
    assert ctrl.current_page == 4
    assert window.project.note(4) == "Schluss"
    press(presenter.notes, Qt.Key.Key_PageUp)
    assert ctrl.current_page == 3

    # Esc beendet, Hauptfenster zeigt letzte Folie
    press(presenter, Qt.Key.Key_Escape)
    assert window.session is None
    assert window.isVisible()
    assert window.thumbs.selected_page() == 3

    # Speichern und neu laden
    target = tmp_path / "vortrag.pdfpres"
    assert window._save_to(target)
    assert not window.project.dirty
    reloaded = Project.open(target)
    assert reloaded.notes == {1: "Notiz für Folie 2", 4: "Schluss"}


def test_audience_only_mode(window, sample_pdf):
    window.open_path(sample_pdf)
    window.start_presentation(from_current=False, audience_only=True)
    session = window.session
    assert session.presenter is None
    assert session.audience.isFullScreen()
    press(session.audience, Qt.Key.Key_PageDown)
    assert session.controller.current_page == 1
    press(session.audience, Qt.Key.Key_Escape)
    assert window.session is None
