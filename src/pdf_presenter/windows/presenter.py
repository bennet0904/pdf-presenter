"""Presenteransicht: aktuelle + nächste Folie, Notizen, Timer, Uhr und Steuerung."""

from __future__ import annotations

from PySide6.QtCore import QDateTime, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QCloseEvent, QFont, QResizeEvent, QShowEvent
from PySide6.QtWidgets import (
    QDialog,
    QHBoxLayout,
    QLabel,
    QLayout,
    QPlainTextEdit,
    QPushButton,
    QSizePolicy,
    QSplitter,
    QVBoxLayout,
    QWidget,
)

from pdf_presenter.controller import PresentationController, format_duration
from pdf_presenter.document import SlideDocument
from pdf_presenter.project import Project
from pdf_presenter.widgets.slide_view import BLANK_BLACK, BLANK_NONE, SlideView
from pdf_presenter.widgets.thumbnails import ThumbnailList

STYLE = """
QWidget#presenter { background: #1b1b1f; color: #e8e8ea; }
QLabel { color: #e8e8ea; }
QLabel.caption { color: #9a9aa3; font-size: 11pt; }
QLabel#timer { font-size: 30pt; font-weight: 600; font-family: "Consolas", "DejaVu Sans Mono", monospace; }
QLabel#timer[paused="true"] { color: #f0b429; }
QLabel#clock { font-size: 16pt; color: #b8b8c0; }
QLabel#counter { font-size: 16pt; font-weight: 600; }
QLabel#status { color: #f0b429; font-size: 12pt; }
QPlainTextEdit { background: #26262c; color: #f2f2f4; border: 1px solid #3a3a42; border-radius: 6px; padding: 6px; }
QPushButton { background: #2d2d35; color: #e8e8ea; border: 1px solid #44444e; border-radius: 6px; padding: 8px 14px; font-size: 11pt; }
QPushButton:hover { background: #3a3a44; }
QPushButton:checked { background: #5a4a10; border-color: #f0b429; }
QSplitter::handle { background: #1b1b1f; }
QWidget#bar { background: #232328; border-top: 1px solid #33333b; }
"""

DEFAULT_NOTES_PT = 16

# Untergrenze für die Fenstergröße. Bewusst klein: Die Presenteransicht darf
# nie mehr Platz verlangen, als der Bildschirm hat (siehe PresenterWindow).
MIN_WINDOW_SIZE = (640, 360)

# Beschriftungen der unteren Leiste: (normal, kompakt für schmale Bildschirme)
LABELS = {
    "prev": ("◀  Zurück", "◀"),
    "next": ("Weiter  ▶", "▶"),
    "pause": ("⏸  Pause", "⏸"),
    "start": ("▶  Start", "▶"),
    "overview": ("▦ Übersicht", "▦"),
    "black": ("■ Schwarz", "■"),
    "swap": ("⇄ Tauschen", "⇄"),
    "end": ("✕ Beenden", "✕"),
}


def _caption(text: str) -> QLabel:
    label = QLabel(text)
    label.setProperty("class", "caption")
    return label


def _button(text: str, tooltip: str) -> QPushButton:
    button = QPushButton(text)
    button.setToolTip(tooltip)
    # Buttons dürfen nie den Fokus stehlen, sonst löst die Leertaste sie aus.
    button.setFocusPolicy(Qt.FocusPolicy.NoFocus)
    return button


class PresenterWindow(QWidget):
    closed = Signal()
    end_requested = Signal()
    swap_requested = Signal()
    notes_edited = Signal()

    def __init__(self, project: Project, document: SlideDocument, controller: PresentationController):
        super().__init__(None, Qt.WindowType.Window)
        self.setObjectName("presenter")
        self.setWindowTitle(f"Presenter – {project.display_name}")
        self.setStyleSheet(STYLE)
        self.setFocusPolicy(Qt.FocusPolicy.StrongFocus)
        self._project = project
        self._document = document
        self._ctrl = controller
        self._settings = QSettings()
        self._compact = False

        # --- Folien -----------------------------------------------------
        self.current_view = SlideView(background="#111114")
        self.current_view.set_document(document)
        self.next_view = SlideView(background="#111114")
        self.next_view.set_document(document)
        self.next_view.set_placeholder("Ende der Präsentation")

        left = QWidget()
        left_layout = QVBoxLayout(left)
        left_layout.setContentsMargins(12, 12, 6, 6)
        self.status = QLabel()
        self.status.setObjectName("status")
        self.status.setAlignment(Qt.AlignmentFlag.AlignRight | Qt.AlignmentFlag.AlignVCenter)
        # Lange Hinweise dürfen das Fenster nicht verbreitern.
        self.status.setSizePolicy(QSizePolicy.Policy.Ignored, QSizePolicy.Policy.Preferred)
        left_head = QHBoxLayout()
        left_head.addWidget(_caption("Aktuelle Folie"))
        left_head.addWidget(self.status, 1)
        left_layout.addLayout(left_head)
        left_layout.addWidget(self.current_view, 1)

        # --- rechte Spalte: nächste Folie + Notizen ---------------------
        self.notes = QPlainTextEdit()
        self.notes.setPlaceholderText("Keine Notizen für diese Folie – hier tippen, um welche hinzuzufügen.")
        size = int(self._settings.value("notes_font_pt", DEFAULT_NOTES_PT))
        self._set_notes_font(size)
        self.notes.textChanged.connect(self._on_notes_changed)

        next_box = QWidget()
        next_layout = QVBoxLayout(next_box)
        next_layout.setContentsMargins(6, 12, 12, 6)
        next_layout.addWidget(_caption("Nächste Folie"))
        next_layout.addWidget(self.next_view, 1)

        notes_box = QWidget()
        notes_layout = QVBoxLayout(notes_box)
        notes_layout.setContentsMargins(6, 6, 12, 6)
        notes_head = QHBoxLayout()
        notes_head.addWidget(_caption("Notizen"))
        notes_head.addStretch(1)
        smaller = _button("A−", "Notizen kleiner (Strg + −)")
        bigger = _button("A+", "Notizen größer (Strg + +)")
        smaller.clicked.connect(lambda: self.change_notes_font(-2))
        bigger.clicked.connect(lambda: self.change_notes_font(+2))
        notes_head.addWidget(smaller)
        notes_head.addWidget(bigger)
        notes_layout.addLayout(notes_head)
        notes_layout.addWidget(self.notes, 1)

        right = QSplitter(Qt.Orientation.Vertical)
        right.addWidget(next_box)
        right.addWidget(notes_box)
        right.setSizes([350, 500])

        main_split = QSplitter(Qt.Orientation.Horizontal)
        main_split.addWidget(left)
        main_split.addWidget(right)
        main_split.setStretchFactor(0, 3)
        main_split.setStretchFactor(1, 2)
        main_split.setSizes([1100, 650])

        # --- untere Leiste ----------------------------------------------
        bar = QWidget()
        bar.setObjectName("bar")
        bar_layout = QHBoxLayout(bar)
        bar_layout.setContentsMargins(12, 8, 12, 8)
        bar_layout.setSpacing(10)

        self.prev_btn = _button(LABELS["prev"][0], "Vorherige Folie (←, Bild↑)")
        self.next_btn = _button(LABELS["next"][0], "Nächste Folie (→, Bild↓, Leertaste)")
        self.prev_btn.clicked.connect(controller.prev)
        self.next_btn.clicked.connect(controller.next)
        self.counter = QLabel()
        self.counter.setObjectName("counter")

        self.timer_label = QLabel("00:00:00")
        self.timer_label.setObjectName("timer")
        self.timer_label.setToolTip("Laufzeit der Präsentation")
        self.pause_btn = _button(LABELS["pause"][0], "Timer anhalten/fortsetzen (T)")
        self.pause_btn.clicked.connect(controller.toggle_timer)
        reset_btn = _button("↺", "Timer zurücksetzen")
        reset_btn.clicked.connect(controller.reset_timer)

        self.clock = QLabel()
        self.clock.setObjectName("clock")
        self.clock.setToolTip("Aktuelle Uhrzeit")
        overview_btn = _button(LABELS["overview"][0], "Alle Folien anzeigen (G)")
        overview_btn.clicked.connect(self.show_overview)
        self.black_btn = _button(LABELS["black"][0], "Bildschirm schwarz an/aus (B) – bleibt beim Folienwechsel aktiv")
        self.black_btn.setCheckable(True)
        self.black_btn.clicked.connect(lambda: controller.toggle_black())
        swap_btn = _button(LABELS["swap"][0], "Bildschirme tauschen")
        swap_btn.clicked.connect(self.swap_requested)
        end_btn = _button(LABELS["end"][0], "Präsentation beenden (Esc)")
        end_btn.clicked.connect(self.end_requested)

        bar_layout.addWidget(self.prev_btn)
        bar_layout.addWidget(self.next_btn)
        bar_layout.addSpacing(8)
        bar_layout.addWidget(self.counter)
        bar_layout.addStretch(1)
        bar_layout.addWidget(self.timer_label)
        bar_layout.addWidget(self.pause_btn)
        bar_layout.addWidget(reset_btn)
        bar_layout.addSpacing(12)
        bar_layout.addWidget(self.clock)
        bar_layout.addSpacing(12)
        bar_layout.addWidget(overview_btn)
        bar_layout.addWidget(self.black_btn)
        bar_layout.addWidget(swap_btn)
        bar_layout.addWidget(end_btn)

        root = QVBoxLayout(self)
        root.setContentsMargins(0, 0, 0, 0)
        root.setSpacing(0)
        root.addWidget(main_split, 1)
        root.addWidget(bar)
        # Das Layout darf die Fenstergröße nicht bestimmen. Sonst vergrößert Qt
        # das maximierte Fenster, sobald die Leiste breiter wird als der
        # Bildschirm (z. B. bei „Folie 10 / 12“). Unter Wayland ist das ein
        # Protokollfehler, und der Compositor beendet daraufhin das Programm.
        root.setSizeConstraint(QLayout.SizeConstraint.SetNoConstraint)
        self.setMinimumSize(*MIN_WINDOW_SIZE)

        # Buttons, deren Beschriftung im Kompaktmodus gekürzt wird
        self._labelled = {
            "prev": self.prev_btn, "next": self.next_btn, "overview": overview_btn,
            "black": self.black_btn, "swap": swap_btn, "end": end_btn,
        }

        # --- Signale ------------------------------------------------------
        controller.page_changed.connect(self._on_page_changed)
        controller.blank_changed.connect(self._on_blank_changed)
        controller.timer_changed.connect(lambda ms: self.timer_label.setText(format_duration(ms)))
        controller.timer_running_changed.connect(self._on_timer_running)

        self._clock_timer = QTimer(self)
        self._clock_timer.timeout.connect(self._update_clock)
        self._clock_timer.start(1000)
        self._update_clock()

        self._info = ""
        self._jump = ""
        self._on_page_changed(controller.current_page)
        self._on_timer_running(controller.timer_running)
        self._screen_hooked = False
        self._measure_full_width()

    # ------------------------------------------------------------ Anzeige
    def _on_page_changed(self, page: int) -> None:
        self.current_view.set_page(page)
        self.next_view.set_page(self._ctrl.next_page)
        self.counter.setText(f"Folie {page + 1} / {self._ctrl.page_count}")
        self.prev_btn.setEnabled(page > 0)
        self.next_btn.setEnabled(self._ctrl.next_page is not None)
        self.notes.blockSignals(True)
        self.notes.setPlainText(self._project.note(page))
        self.notes.blockSignals(False)

    def _on_notes_changed(self) -> None:
        self._project.set_note(self._ctrl.current_page, self.notes.toPlainText())
        self.notes_edited.emit()

    def _on_blank_changed(self, mode: str) -> None:
        self.black_btn.setChecked(mode == BLANK_BLACK)
        self._refresh_status()

    def _on_timer_running(self, running: bool) -> None:
        self.pause_btn.setText(LABELS["pause" if running else "start"][self._compact])
        self.timer_label.setProperty("paused", "false" if running else "true")
        self.timer_label.style().unpolish(self.timer_label)
        self.timer_label.style().polish(self.timer_label)

    # ------------------------------------------------------ Platzbedarf
    def _fix_label_widths(self) -> None:
        """Feste Breiten für Texte, die sich während des Vortrags ändern.

        So springt die Leiste nicht hin und her, wenn aus „Folie 9“ „Folie 10“
        wird oder der Timer zwischen Pause und Start wechselt.
        """
        self.ensurePolished()
        count = self._ctrl.page_count
        widest = f"Folie {count} / {count}"
        self.counter.setMinimumWidth(self.counter.fontMetrics().horizontalAdvance(widest) + 4)
        self.timer_label.setMinimumWidth(
            self.timer_label.fontMetrics().horizontalAdvance("00:00:00") + 4
        )
        self._update_pause_width()

    def _update_pause_width(self) -> None:
        current = self.pause_btn.text()
        width = 0
        for key in ("pause", "start"):
            self.pause_btn.setText(LABELS[key][self._compact])
            width = max(width, self.pause_btn.sizeHint().width())
        self.pause_btn.setText(current)
        self.pause_btn.setMinimumWidth(width)

    def set_compact(self, compact: bool) -> None:
        """Kurze Button-Beschriftungen (nur Symbole) für schmale Bildschirme."""
        if compact == self._compact:
            return
        self._compact = compact
        for key, button in self._labelled.items():
            button.setText(LABELS[key][compact])
        self._on_timer_running(self._ctrl.timer_running)
        self._update_pause_width()

    def is_compact(self) -> bool:
        return self._compact

    def full_width(self) -> int:
        """Breite, ab der die Leiste mit vollen Beschriftungen Platz hat."""
        return self._full_width

    def _measure_full_width(self) -> None:
        """Misst den Platzbedarf der Leiste mit vollen Beschriftungen.

        Schriftgröße in Pixeln hängt von der DPI des Bildschirms ab. Deshalb wird
        beim Anzeigen und bei jedem Bildschirmwechsel neu gemessen, nicht nur
        einmal beim Erzeugen des Fensters.
        """
        compact = self._compact
        self.set_compact(False)
        self._fix_label_widths()
        self.layout().invalidate()
        self._full_width = self.layout().minimumSize().width()
        self.set_compact(compact)
        self._fix_label_widths()
        self.set_compact(self.width() < self._full_width)

    def showEvent(self, event: QShowEvent) -> None:  # noqa: N802
        super().showEvent(event)
        handle = self.windowHandle()
        if handle is not None and not self._screen_hooked:
            handle.screenChanged.connect(lambda _s: self._measure_full_width())
            self._screen_hooked = True
        self._measure_full_width()

    def resizeEvent(self, event: QResizeEvent) -> None:  # noqa: N802
        self.set_compact(event.size().width() < self._full_width)
        super().resizeEvent(event)

    def _update_clock(self) -> None:
        self.clock.setText(QDateTime.currentDateTime().toString("HH:mm"))

    def set_info(self, text: str) -> None:
        self._info = text
        self._refresh_status()

    def set_jump_buffer(self, digits: str) -> None:
        self._jump = digits
        self._refresh_status()

    def _refresh_status(self) -> None:
        parts = []
        if self._jump:
            parts.append(f"Gehe zu Folie: {self._jump} ⏎")
        blank = self._ctrl.blank
        if blank != BLANK_NONE:
            key = "B" if blank == BLANK_BLACK else "W"
            color = "schwarz" if blank == BLANK_BLACK else "weiß"
            parts.append(f"● Publikum sieht {color} – {key} blendet wieder ein")
        if self._info:
            parts.append(self._info)
        self.status.setText("   ·   ".join(parts))

    # ------------------------------------------------------------ Notizen
    def _set_notes_font(self, pt: int) -> None:
        font = QFont(self.notes.font())
        font.setPointSize(pt)
        self.notes.setFont(font)

    def change_notes_font(self, delta: int) -> None:
        pt = min(48, max(8, self.notes.font().pointSize() + delta))
        self._set_notes_font(pt)
        self._settings.setValue("notes_font_pt", pt)

    # ---------------------------------------------------------- Übersicht
    def show_overview(self) -> None:
        dialog = QDialog(self)
        dialog.setWindowTitle("Folienübersicht")
        dialog.setStyleSheet("QDialog { background: #1b1b1f; } QListWidget { background: #1b1b1f; color: #e8e8ea; border: none; }")
        thumbs = ThumbnailList(dialog, thumb_width=220, grid=True)
        thumbs.set_document(self._document)
        thumbs.select_page(self._ctrl.current_page)

        def choose(item) -> None:
            self._ctrl.goto(int(item.data(Qt.ItemDataRole.UserRole)))
            dialog.accept()

        thumbs.itemActivated.connect(choose)
        thumbs.itemClicked.connect(choose)
        layout = QVBoxLayout(dialog)
        layout.addWidget(thumbs)
        dialog.resize(self.width() * 4 // 5, self.height() * 4 // 5)
        dialog.exec()
        self.setFocus()

    def closeEvent(self, event: QCloseEvent) -> None:  # noqa: N802
        self.closed.emit()
        super().closeEvent(event)
