"""Zustand einer laufenden Präsentation: aktuelle Folie, Schwarzbild, Timer.

Presenter- und Viewerfenster lesen nur aus diesem Objekt und hören auf seine
Signale – so bleiben beide Ansichten immer synchron.
"""

from __future__ import annotations

from PySide6.QtCore import QElapsedTimer, QObject, QTimer, Signal

from pdf_presenter.widgets.slide_view import BLANK_BLACK, BLANK_NONE, BLANK_WHITE


def format_duration(ms: int) -> str:
    seconds = max(0, ms) // 1000
    return f"{seconds // 3600:02d}:{seconds // 60 % 60:02d}:{seconds % 60:02d}"


class PresentationController(QObject):
    page_changed = Signal(int)
    blank_changed = Signal(str)
    timer_changed = Signal(int)          # verstrichene Millisekunden
    timer_running_changed = Signal(bool)

    TICK_MS = 250

    def __init__(self, page_count: int, start_page: int = 0, parent: QObject | None = None):
        super().__init__(parent)
        if page_count <= 0:
            raise ValueError("page_count muss > 0 sein")
        self._count = page_count
        self._page = min(max(start_page, 0), page_count - 1)
        self._blank = BLANK_NONE

        self._clock = QElapsedTimer()
        self._accumulated_ms = 0
        self._running = False
        self._ticker = QTimer(self)
        self._ticker.setInterval(self.TICK_MS)
        self._ticker.timeout.connect(lambda: self.timer_changed.emit(self.elapsed_ms()))

    # ---------------------------------------------------------- Navigation
    @property
    def page_count(self) -> int:
        return self._count

    @property
    def current_page(self) -> int:
        return self._page

    @property
    def next_page(self) -> int | None:
        return self._page + 1 if self._page + 1 < self._count else None

    def goto(self, page: int) -> None:
        # Schwarz/Weiß bleibt bewusst aktiv: So kann man im Presenter Folien
        # weiterschalten, während das Publikum nichts sieht. Aufheben nur per B/W.
        page = min(max(page, 0), self._count - 1)
        if page != self._page:
            self._page = page
            self.page_changed.emit(page)

    def next(self) -> None:
        self.goto(self._page + 1)

    def prev(self) -> None:
        self.goto(self._page - 1)

    def first(self) -> None:
        self.goto(0)

    def last(self) -> None:
        self.goto(self._count - 1)

    # ------------------------------------------------------- Schwarz/Weiß
    @property
    def blank(self) -> str:
        return self._blank

    def set_blank(self, mode: str) -> None:
        if mode != self._blank:
            self._blank = mode
            self.blank_changed.emit(mode)

    def toggle_black(self) -> None:
        self.set_blank(BLANK_NONE if self._blank == BLANK_BLACK else BLANK_BLACK)

    def toggle_white(self) -> None:
        self.set_blank(BLANK_NONE if self._blank == BLANK_WHITE else BLANK_WHITE)

    # ---------------------------------------------------------------- Timer
    @property
    def timer_running(self) -> bool:
        return self._running

    def elapsed_ms(self) -> int:
        running = self._clock.elapsed() if self._running else 0
        return self._accumulated_ms + running

    def start_timer(self) -> None:
        if self._running:
            return
        self._clock.start()
        self._running = True
        self._ticker.start()
        self.timer_running_changed.emit(True)
        self.timer_changed.emit(self.elapsed_ms())

    def pause_timer(self) -> None:
        if not self._running:
            return
        self._accumulated_ms += self._clock.elapsed()
        self._running = False
        self._ticker.stop()
        self.timer_running_changed.emit(False)
        self.timer_changed.emit(self.elapsed_ms())

    def toggle_timer(self) -> None:
        if self._running:
            self.pause_timer()
        else:
            self.start_timer()

    def reset_timer(self) -> None:
        """Setzt auf 00:00:00 zurück; ein laufender Timer läuft von vorn weiter."""
        self._accumulated_ms = 0
        if self._running:
            self._clock.restart()
        self.timer_changed.emit(0)
