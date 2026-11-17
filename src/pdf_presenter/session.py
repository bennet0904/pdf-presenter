"""Eine laufende Präsentation: verbindet Controller, Presenter- und Viewerfenster.

Tastatureingaben werden über einen Event-Filter auf Anwendungsebene abgefangen,
damit sie in beiden Fenstern gleich funktionieren – auch wenn gerade das
Notizfeld den Fokus hat (dann nur Bild↑/↓, damit Presenter-Clicker gehen).
"""

from __future__ import annotations

from PySide6.QtCore import QEvent, QObject, QSettings, Qt, QTimer, Signal
from PySide6.QtGui import QGuiApplication, QKeyEvent, QScreen
from PySide6.QtWidgets import QApplication, QWidget

from pdf_presenter.controller import PresentationController
from pdf_presenter.document import SlideDocument
from pdf_presenter.project import Project
from pdf_presenter.screens import pick_screens, show_fullscreen_on, show_maximized_on, show_windowed_on
from pdf_presenter.windows.audience import AudienceWindow
from pdf_presenter.windows.presenter import PresenterWindow

Key = Qt.Key
NEXT_KEYS = {Key.Key_Right, Key.Key_Down, Key.Key_PageDown, Key.Key_Space, Key.Key_N}
PREV_KEYS = {Key.Key_Left, Key.Key_Up, Key.Key_PageUp, Key.Key_Backspace, Key.Key_P}
ENTER_KEYS = {Key.Key_Return, Key.Key_Enter}
DIGIT_KEYS = {getattr(Key, f"Key_{i}"): str(i) for i in range(10)}


class PresentationSession(QObject):
    finished = Signal(int)      # letzte angezeigte Folie
    save_requested = Signal()
    notes_edited = Signal()

    def __init__(
        self,
        project: Project,
        document: SlideDocument,
        start_page: int = 0,
        *,
        audience_only: bool = False,
        audience_screen: QScreen | None = None,
        parent: QObject | None = None,
    ):
        super().__init__(parent)
        self._settings = QSettings()
        self._audience_only = audience_only
        self._audience_screen = audience_screen
        self._ended = False
        self._jump = ""

        self.controller = PresentationController(document.page_count, start_page, self)
        self.audience = AudienceWindow(document, self.controller)
        self.audience.closed.connect(self.end)
        self.presenter: PresenterWindow | None = None
        if not audience_only:
            self.presenter = PresenterWindow(project, document, self.controller)
            self.presenter.closed.connect(self.end)
            self.presenter.end_requested.connect(self.end)
            self.presenter.swap_requested.connect(self.swap_screens)
            self.presenter.notes_edited.connect(self.notes_edited)

        # Nächste Folie vorrendern, sobald gewechselt wurde.
        self.controller.page_changed.connect(lambda _: QTimer.singleShot(0, self._prerender))

        app = QGuiApplication.instance()
        app.screenAdded.connect(self._on_screens_changed)
        app.screenRemoved.connect(self._on_screens_changed)
        QApplication.instance().installEventFilter(self)

    # -------------------------------------------------------------- Ablauf
    def start(self) -> None:
        self.place_windows()
        self.controller.start_timer()
        focus = self.presenter or self.audience
        focus.activateWindow()
        focus.raise_()
        focus.setFocus()
        QTimer.singleShot(0, self._prerender)

    def end(self) -> None:
        if self._ended:
            return
        self._ended = True
        QApplication.instance().removeEventFilter(self)
        app = QGuiApplication.instance()
        app.screenAdded.disconnect(self._on_screens_changed)
        app.screenRemoved.disconnect(self._on_screens_changed)
        self.controller.pause_timer()
        for window in (self.audience, self.presenter):
            if window is not None:
                window.blockSignals(True)
                window.close()
                window.deleteLater()
        self.finished.emit(self.controller.current_page)

    def _prerender(self) -> None:
        if self._ended:
            return
        nxt = self.controller.next_page
        self.audience.view.prerender(nxt)
        if self.presenter is not None:
            self.presenter.current_view.prerender(nxt)
            if nxt is not None and nxt + 1 < self.controller.page_count:
                self.presenter.next_view.prerender(nxt + 1)

    # ---------------------------------------------------------- Bildschirme
    def place_windows(self) -> None:
        if self._audience_only:
            screen = self._audience_screen or QGuiApplication.primaryScreen()
            show_fullscreen_on(self.audience, screen)
            return

        swap = self._settings.value("swap_screens", False, type=bool)
        presenter_screen, audience_screen = pick_screens(swap)
        assert self.presenter is not None
        if audience_screen is None:
            # Nur ein Monitor (z. B. Laptop ohne Beamer): Probemodus.
            show_maximized_on(self.presenter, presenter_screen)
            show_windowed_on(self.audience, presenter_screen, 960, 540)
            self.presenter.set_info("Nur ein Bildschirm erkannt – Publikumsansicht im Fenster")
        else:
            show_maximized_on(self.presenter, presenter_screen)
            show_fullscreen_on(self.audience, audience_screen)
            self.presenter.set_info("")
        self.presenter.activateWindow()

    def swap_screens(self) -> None:
        swap = not self._settings.value("swap_screens", False, type=bool)
        self._settings.setValue("swap_screens", swap)
        self.place_windows()

    def _on_screens_changed(self, _screen: QScreen) -> None:
        # Kurz warten, bis das Betriebssystem die neue Monitoranordnung gemeldet hat.
        QTimer.singleShot(500, lambda: None if self._ended else self.place_windows())

    # ----------------------------------------------------------- Tastatur
    def _owns(self, obj: QObject) -> bool:
        if not isinstance(obj, QWidget):
            return False
        window = obj.window()
        return window is self.audience or (self.presenter is not None and window is self.presenter)

    def eventFilter(self, obj: QObject, event: QEvent) -> bool:  # noqa: N802
        if event.type() != QEvent.Type.KeyPress or not self._owns(obj):
            return False
        return self._handle_key(obj, event)  # type: ignore[arg-type]

    def _set_jump(self, digits: str) -> None:
        self._jump = digits
        if self.presenter is not None:
            self.presenter.set_jump_buffer(digits)

    def _handle_key(self, obj: QObject, event: QKeyEvent) -> bool:
        key = event.key()
        mods = event.modifiers() & ~Qt.KeyboardModifier.KeypadModifier
        ctrl = self.controller
        presenter = self.presenter

        if mods == Qt.KeyboardModifier.ControlModifier:
            if key == Key.Key_S:
                self.save_requested.emit()
                return True
            if presenter is not None and key in (Key.Key_Plus, Key.Key_Equal):
                presenter.change_notes_font(+2)
                return True
            if presenter is not None and key == Key.Key_Minus:
                presenter.change_notes_font(-2)
                return True
            return False

        in_notes = presenter is not None and obj is presenter.notes
        if in_notes:
            # Im Notizfeld normal tippen – nur Clicker-Tasten navigieren.
            if key == Key.Key_PageDown:
                ctrl.next()
            elif key == Key.Key_PageUp:
                ctrl.prev()
            elif key == Key.Key_Escape:
                presenter.setFocus()
            else:
                return False
            return True

        if mods not in (Qt.KeyboardModifier.NoModifier, Qt.KeyboardModifier.ShiftModifier):
            return False

        if key in DIGIT_KEYS:
            self._set_jump((self._jump + DIGIT_KEYS[key])[-4:])
            return True
        if key in ENTER_KEYS and self._jump:
            ctrl.goto(int(self._jump) - 1)
            self._set_jump("")
            return True
        if key == Key.Key_Escape:
            if self._jump:
                self._set_jump("")
            else:
                self.end()
            return True
        self._set_jump("")

        if key in NEXT_KEYS or key in ENTER_KEYS:
            ctrl.next()
        elif key in PREV_KEYS:
            ctrl.prev()
        elif key == Key.Key_Home:
            ctrl.first()
        elif key == Key.Key_End:
            ctrl.last()
        elif key in (Key.Key_B, Key.Key_Period):
            ctrl.toggle_black()
        elif key in (Key.Key_W, Key.Key_Comma):
            ctrl.toggle_white()
        elif key == Key.Key_T:
            ctrl.toggle_timer()
        elif key == Key.Key_G and presenter is not None:
            presenter.show_overview()
        elif key == Key.Key_F5:
            pass  # manche Clicker senden F5 – während der Präsentation ignorieren
        else:
            return False
        return True
