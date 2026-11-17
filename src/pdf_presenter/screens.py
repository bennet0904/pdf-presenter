"""Monitorwahl und Fensterplatzierung."""

from __future__ import annotations

from PySide6.QtGui import QGuiApplication, QScreen
from PySide6.QtWidgets import QWidget


def pick_screens(swap: bool = False) -> tuple[QScreen, QScreen | None]:
    """Liefert (Presenter-Monitor, Viewer-Monitor). Viewer ist ``None`` bei nur einem Monitor.

    Standard: Presenter auf dem Primärmonitor (Laptop), Viewer auf dem ersten weiteren.
    """
    primary = QGuiApplication.primaryScreen()
    others = [s for s in QGuiApplication.screens() if s is not primary]
    if not others:
        return primary, None
    presenter, audience = primary, others[0]
    if swap:
        presenter, audience = audience, presenter
    return presenter, audience


def _move_to(widget: QWidget, screen: QScreen) -> None:
    if widget.isFullScreen() or widget.isMaximized():
        widget.showNormal()
    widget.winId()  # erzwingt ein natives Fenster, damit windowHandle() existiert
    handle = widget.windowHandle()
    if handle is not None:
        handle.setScreen(screen)


def show_fullscreen_on(widget: QWidget, screen: QScreen) -> None:
    _move_to(widget, screen)
    widget.setGeometry(screen.geometry())
    widget.showFullScreen()


def show_maximized_on(widget: QWidget, screen: QScreen) -> None:
    _move_to(widget, screen)
    geo = screen.availableGeometry()
    widget.setGeometry(geo.adjusted(40, 40, -40, -40))
    widget.showMaximized()


def show_windowed_on(widget: QWidget, screen: QScreen, width: int, height: int) -> None:
    _move_to(widget, screen)
    geo = screen.availableGeometry()
    width, height = min(width, geo.width()), min(height, geo.height())
    widget.setGeometry(
        geo.x() + geo.width() - width - 30, geo.y() + 30, width, height
    )
    widget.showNormal()
