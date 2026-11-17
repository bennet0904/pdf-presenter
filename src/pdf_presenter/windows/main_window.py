"""Hauptfenster: Präsentation öffnen, Notizen vorbereiten, speichern, Präsentation starten."""

from __future__ import annotations

from pathlib import Path

from PySide6.QtCore import QSettings, Qt
from PySide6.QtGui import QAction, QCloseEvent, QDragEnterEvent, QDropEvent, QKeySequence
from PySide6.QtWidgets import (
    QFileDialog,
    QLabel,
    QMainWindow,
    QMessageBox,
    QPlainTextEdit,
    QSplitter,
    QStackedWidget,
    QVBoxLayout,
    QWidget,
)

from pdf_presenter.document import SlideDocument
from pdf_presenter.project import PROJECT_SUFFIX, Project, ProjectError
from pdf_presenter.session import PresentationSession
from pdf_presenter.widgets.slide_view import SlideView
from pdf_presenter.widgets.thumbnails import ThumbnailList

APP_NAME = "PDF Presenter"
OPEN_FILTER = "Präsentationen (*.pdfpres *.pdf);;PDF Presenter-Projekt (*.pdfpres);;PDF (*.pdf);;Alle Dateien (*)"
SAVE_FILTER = "PDF Presenter-Projekt (*.pdfpres)"
MAX_RECENT = 8
SUPPORTED = {".pdf", PROJECT_SUFFIX}


class MainWindow(QMainWindow):
    def __init__(self) -> None:
        super().__init__()
        self.settings = QSettings()
        self.project: Project | None = None
        self.document: SlideDocument | None = None
        self.session: PresentationSession | None = None
        self._page = 0

        self.setAcceptDrops(True)
        self.resize(1280, 800)

        # --- Inhalt -------------------------------------------------------
        self.thumbs = ThumbnailList(thumb_width=170)
        self.thumbs.setMinimumWidth(210)
        self.thumbs.currentRowChanged.connect(self._on_row_changed)

        self.slide = SlideView(background="#e9e9ec")
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Sprechernotizen für diese Folie …")
        font = self.notes.font()
        font.setPointSize(12)
        self.notes.setFont(font)
        self.notes.textChanged.connect(self._on_notes_changed)

        notes_box = QWidget()
        notes_layout = QVBoxLayout(notes_box)
        notes_layout.setContentsMargins(0, 4, 0, 0)
        notes_layout.addWidget(QLabel("Notizen"))
        notes_layout.addWidget(self.notes)

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(self.slide)
        right.addWidget(notes_box)
        right.setStretchFactor(0, 3)
        right.setStretchFactor(1, 1)
        right.setSizes([560, 200])

        editor = QSplitter(Qt.Orientation.Horizontal)
        editor.addWidget(self.thumbs)
        editor.addWidget(right)
        editor.setStretchFactor(1, 1)
        editor.setSizes([230, 1050])

        empty = QLabel(
            "<h2>Keine Präsentation geöffnet</h2>"
            "<p>PDF oder .pdfpres-Datei über <b>Datei → Öffnen</b> (Strg+O) laden<br>"
            "oder einfach hierher ziehen.</p>"
        )
        empty.setAlignment(Qt.AlignmentFlag.AlignCenter)
        empty.setStyleSheet("color: #666;")

        self.stack = QStackedWidget()
        self.stack.addWidget(empty)
        self.stack.addWidget(editor)
        self.setCentralWidget(self.stack)

        self._build_actions()
        self._update_ui()

    # ----------------------------------------------------------- Aktionen
    def _action(self, text: str, slot, shortcut=None, tip: str = "") -> QAction:
        action = QAction(text, self)
        if shortcut is not None:
            action.setShortcut(QKeySequence(shortcut))
        if tip:
            action.setStatusTip(tip)
        action.triggered.connect(slot)
        return action

    def _build_actions(self) -> None:
        self.act_open = self._action("&Öffnen …", self.open_dialog, QKeySequence.StandardKey.Open)
        self.act_save = self._action("&Speichern", self.save, QKeySequence.StandardKey.Save)
        self.act_save_as = self._action("Speichern &unter …", self.save_as, "Ctrl+Shift+S")
        act_quit = self._action("&Beenden", self.close, QKeySequence.StandardKey.Quit)
        self.act_start = self._action(
            "Von &Anfang an", lambda: self.start_presentation(from_current=False), "F5",
            "Präsentation ab Folie 1 starten",
        )
        self.act_start_here = self._action(
            "Ab aktueller &Folie", lambda: self.start_presentation(from_current=True), "Shift+F5",
            "Präsentation ab der ausgewählten Folie starten",
        )
        self.act_audience_only = self._action(
            "Nur &Vollbild (ohne Presenter)",
            lambda: self.start_presentation(from_current=True, audience_only=True), "Ctrl+F5",
            "Folien im Vollbild auf diesem Bildschirm zeigen",
        )
        self.act_swap = QAction("Bildschirme &tauschen", self, checkable=True)
        self.act_swap.setStatusTip("Presenter auf dem zweiten, Publikum auf dem Hauptbildschirm")
        self.act_swap.setChecked(self.settings.value("swap_screens", False, type=bool))
        self.act_swap.toggled.connect(lambda on: self.settings.setValue("swap_screens", on))

        file_menu = self.menuBar().addMenu("&Datei")
        file_menu.addAction(self.act_open)
        self.recent_menu = file_menu.addMenu("&Zuletzt geöffnet")
        self.recent_menu.aboutToShow.connect(self._fill_recent_menu)
        file_menu.addSeparator()
        file_menu.addAction(self.act_save)
        file_menu.addAction(self.act_save_as)
        file_menu.addSeparator()
        file_menu.addAction(act_quit)

        show_menu = self.menuBar().addMenu("&Präsentation")
        show_menu.addAction(self.act_start)
        show_menu.addAction(self.act_start_here)
        show_menu.addAction(self.act_audience_only)
        show_menu.addSeparator()
        show_menu.addAction(self.act_swap)

        help_menu = self.menuBar().addMenu("&Hilfe")
        help_menu.addAction(self._action("&Tastenkürzel", self._show_shortcuts, "F1"))

        toolbar = self.addToolBar("Werkzeuge")
        toolbar.setMovable(False)
        toolbar.addAction(self.act_open)
        toolbar.addAction(self.act_save)
        toolbar.addSeparator()
        toolbar.addAction(self.act_start)
        toolbar.addAction(self.act_start_here)

    # ------------------------------------------------------------- Öffnen
    def open_dialog(self) -> None:
        start_dir = self.settings.value("last_dir", str(Path.home()))
        path, _ = QFileDialog.getOpenFileName(self, "Präsentation öffnen", start_dir, OPEN_FILTER)
        if path:
            self.open_path(path)

    def open_path(self, path: str | Path) -> bool:
        if not self._maybe_save():
            return False
        path = Path(path)
        try:
            project = Project.open(path)
            document = SlideDocument(project.pdf_bytes)
        except ProjectError as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            self._forget_recent(path)
            return False

        if self.document is not None:
            self.document.close()
        self.project, self.document = project, document
        self._page = 0
        self.settings.setValue("last_dir", str(path.parent))
        self._remember_recent(path)

        self.slide.set_document(document)
        self.thumbs.set_document(document)
        self.stack.setCurrentIndex(1)
        self.thumbs.select_page(0)
        self._show_page(0)
        self._update_ui()
        return True

    # ---------------------------------------------------------- Speichern
    def save(self) -> bool:
        if self.project is None:
            return False
        if self.project.path is None:
            return self.save_as()
        return self._save_to(self.project.path)

    def save_as(self) -> bool:
        if self.project is None:
            return False
        if self.project.path is not None:
            suggestion = str(self.project.path)
        else:
            suggestion = str(Path(self.settings.value("last_dir", str(Path.home()))) / (self.project.title + PROJECT_SUFFIX))
        path, _ = QFileDialog.getSaveFileName(self, "Präsentation speichern", suggestion, SAVE_FILTER)
        if not path:
            return False
        return self._save_to(Path(path))

    def _save_to(self, path: Path) -> bool:
        assert self.project is not None
        self._commit_notes()
        try:
            saved = self.project.save(path)
        except ProjectError as exc:
            QMessageBox.critical(self, APP_NAME, str(exc))
            return False
        self._remember_recent(saved)
        self.statusBar().showMessage(f"Gespeichert: {saved}", 4000)
        self._update_ui()
        return True

    def _maybe_save(self) -> bool:
        """Fragt bei ungespeicherten Änderungen nach. False = Abbrechen."""
        if self.project is None or not self.project.dirty:
            return True
        answer = QMessageBox.question(
            self, APP_NAME,
            f"„{self.project.display_name}“ hat ungespeicherte Notizen.\nVor dem Schließen speichern?",
            QMessageBox.StandardButton.Save | QMessageBox.StandardButton.Discard | QMessageBox.StandardButton.Cancel,
            QMessageBox.StandardButton.Save,
        )
        if answer == QMessageBox.StandardButton.Save:
            return self.save()
        return answer == QMessageBox.StandardButton.Discard

    # ------------------------------------------------------------ Notizen
    def _on_row_changed(self, row: int) -> None:
        if row >= 0 and self.project is not None:
            self._show_page(row)

    def _show_page(self, page: int) -> None:
        assert self.project is not None
        self._page = page
        self.slide.set_page(page)
        self.notes.blockSignals(True)
        self.notes.setPlainText(self.project.note(page))
        self.notes.blockSignals(False)

    def _on_notes_changed(self) -> None:
        self._commit_notes()
        self._update_ui()

    def _commit_notes(self) -> None:
        if self.project is not None:
            self.project.set_note(self._page, self.notes.toPlainText())

    # -------------------------------------------------------- Präsentation
    def start_presentation(self, from_current: bool, audience_only: bool = False) -> None:
        if self.project is None or self.document is None or self.session is not None:
            return
        self._commit_notes()
        start = self._page if from_current else 0
        screen = self.screen()
        self.session = PresentationSession(
            self.project, self.document, start,
            audience_only=audience_only, audience_screen=screen, parent=self,
        )
        self.session.finished.connect(self._on_presentation_finished)
        self.session.save_requested.connect(self.save)
        self.session.notes_edited.connect(self._update_ui)
        self.hide()
        self.session.start()

    def _on_presentation_finished(self, last_page: int) -> None:
        session, self.session = self.session, None
        if session is not None:
            session.deleteLater()
        self.act_swap.setChecked(self.settings.value("swap_screens", False, type=bool))
        self.show()
        self.activateWindow()
        self.thumbs.select_page(last_page)
        self._show_page(last_page)
        self._update_ui()

    # ------------------------------------------------------- Zuletzt offen
    def _recent(self) -> list[str]:
        value = self.settings.value("recent_files", [])
        if isinstance(value, str):
            value = [value] if value else []
        return [str(v) for v in value or []]

    def _remember_recent(self, path: Path) -> None:
        entry = str(Path(path).resolve())
        items = [p for p in self._recent() if p != entry]
        self.settings.setValue("recent_files", [entry, *items][:MAX_RECENT])

    def _forget_recent(self, path: Path) -> None:
        entry = str(Path(path).resolve())
        self.settings.setValue("recent_files", [p for p in self._recent() if p != entry])

    def _fill_recent_menu(self) -> None:
        self.recent_menu.clear()
        items = self._recent()
        if not items:
            self.recent_menu.addAction("(leer)").setEnabled(False)
            return
        for i, path in enumerate(items, 1):
            action = self.recent_menu.addAction(f"&{i}  {Path(path).name}")
            action.setToolTip(path)
            action.triggered.connect(lambda _=False, p=path: self.open_path(p))
        self.recent_menu.addSeparator()
        self.recent_menu.addAction("Liste leeren", lambda: self.settings.setValue("recent_files", []))

    # -------------------------------------------------------- Drag & Drop
    def _dropped_path(self, event) -> Path | None:
        if not event.mimeData().hasUrls():
            return None
        for url in event.mimeData().urls():
            if url.isLocalFile():
                path = Path(url.toLocalFile())
                if path.suffix.lower() in SUPPORTED:
                    return path
        return None

    def dragEnterEvent(self, event: QDragEnterEvent) -> None:  # noqa: N802
        if self._dropped_path(event) is not None:
            event.acceptProposedAction()

    def dropEvent(self, event: QDropEvent) -> None:  # noqa: N802
        path = self._dropped_path(event)
        if path is not None:
            event.acceptProposedAction()
            self.open_path(path)

    # ---------------------------------------------------------- Allgemein
    def _update_ui(self) -> None:
        has = self.project is not None
        for action in (self.act_save, self.act_save_as, self.act_start, self.act_start_here, self.act_audience_only):
            action.setEnabled(has)
        if has:
            star = "*" if self.project.dirty else ""
            self.setWindowTitle(f"{self.project.display_name}{star} – {APP_NAME}")
        else:
            self.setWindowTitle(APP_NAME)

    def _show_shortcuts(self) -> None:
        QMessageBox.information(
            self, "Tastenkürzel",
            "<table cellspacing=4>"
            "<tr><td><b>F5 / Umschalt+F5</b></td><td>Präsentation von vorn / ab aktueller Folie</td></tr>"
            "<tr><td><b>Strg+F5</b></td><td>Nur Vollbild ohne Presenter</td></tr>"
            "<tr><td colspan=2><br><b>Während der Präsentation</b></td></tr>"
            "<tr><td>→ ↓ Bild↓ Leertaste Enter</td><td>Nächste Folie</td></tr>"
            "<tr><td>← ↑ Bild↑ Rücktaste</td><td>Vorherige Folie</td></tr>"
            "<tr><td>Pos1 / Ende</td><td>Erste / letzte Folie</td></tr>"
            "<tr><td>Zahl + Enter</td><td>Zu Folie springen</td></tr>"
            "<tr><td>B oder .</td><td>Bildschirm schwarz an/aus</td></tr>"
            "<tr><td>W oder ,</td><td>Bildschirm weiß an/aus</td></tr>"
            "<tr><td></td><td><i>Bleibt beim Folienwechsel aktiv</i></td></tr>"
            "<tr><td>G</td><td>Folienübersicht</td></tr>"
            "<tr><td>T</td><td>Timer anhalten / fortsetzen</td></tr>"
            "<tr><td>Strg + / Strg −</td><td>Notizen größer / kleiner</td></tr>"
            "<tr><td>Strg+S</td><td>Speichern</td></tr>"
            "<tr><td>Esc</td><td>Präsentation beenden</td></tr>"
            "</table>",
        )

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        if self.session is not None:
            self.session.end()
        self._commit_notes()
        if self._maybe_save():
            event.accept()
        else:
            event.ignore()
