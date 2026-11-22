"""Viewer-Fenster für das Publikum (Beamer): nur die Folie, sonst nichts."""

from __future__ import annotations

from PySide6.QtCore import Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QMouseEvent, QWheelEvent
from PySide6.QtWidgets import QLayout, QVBoxLayout, QWidget

from pdf_presenter.controller import PresentationController
from pdf_presenter.document import SlideDocument
from pdf_presenter.widgets.slide_view import SlideView


class AudienceWindow(QWidget):
    closed = Signal()

    CURSOR_HIDE_MS = 2000

    def __init__(self, document: SlideDocument, controller: PresentationController):
        super().__init__(None, Qt.WindowType.Window)
        self.setWindowTitle("PDF Presenter – Publikum")
        self.setStyleSheet("background: black;")
        self._controller = controller
        self._wheel_accum = 0

        self.view = SlideView(self)
        self.view.set_document(document)
        self.view.set_page(controller.current_page)
        self.view.setMouseTracking(True)
        self.setMouseTracking(True)
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.addWidget(self.view)
        # Wie beim Presenter: Der Inhalt soll die Größe des Vollbildfensters
        # nie verändern (unter Wayland wäre das ein fataler Protokollfehler).
        layout.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)

        controller.page_changed.connect(self.view.set_page)
        controller.blank_changed.connect(self.view.set_blank)

        self._cursor_timer = QTimer(self)
        self._cursor_timer.setSingleShot(True)
        self._cursor_timer.setInterval(self.CURSOR_HIDE_MS)
        self._cursor_timer.timeout.connect(lambda: self.setCursor(Qt.CursorShape.BlankCursor))
        self._cursor_timer.start()

    def mouseMoveEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        self.unsetCursor()
        self._cursor_timer.start()
        super().mouseMoveEvent(event)

    def mousePressEvent(self, event: QMouseEvent) -> None:  # noqa: N802
        if event.button() == Qt.MouseButton.LeftButton:
            self._controller.next()
        elif event.button() == Qt.MouseButton.RightButton:
            self._controller.prev()

    def wheelEvent(self, event: QWheelEvent) -> None:  # noqa: N802
        # Aufsummieren, damit Touchpads nicht zehn Folien auf einmal springen.
        self._wheel_accum += event.angleDelta().y()
        while self._wheel_accum <= -120:
            self._wheel_accum += 120
            self._controller.next()
        while self._wheel_accum >= 120:
            self._wheel_accum -= 120
            self._controller.prev()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)
