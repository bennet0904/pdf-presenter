"""Widget, das eine PDF-Seite seitenverhältnistreu und scharf (HiDPI) anzeigt."""

from __future__ import annotations

from PySide6.QtCore import QRectF, QSize, QSizeF, Qt
from PySide6.QtGui import QColor, QPainter, QPaintEvent
from PySide6.QtWidgets import QSizePolicy, QWidget

from pdf_presenter.document import SlideDocument

BLANK_NONE = ""
BLANK_BLACK = "black"
BLANK_WHITE = "white"


class SlideView(QWidget):
    def __init__(self, parent: QWidget | None = None, *, background: str = "#000000"):
        super().__init__(parent)
        self._document: SlideDocument | None = None
        self._page: int | None = None
        self._blank = BLANK_NONE
        self._background = QColor(background)
        self._placeholder = ""
        self._placeholder_color = QColor("#888888")
        self.setSizePolicy(QSizePolicy.Policy.Expanding, QSizePolicy.Policy.Expanding)
        self.setMinimumSize(80, 60)
        self.setAttribute(Qt.WidgetAttribute.WA_OpaquePaintEvent)

    # ------------------------------------------------------------- Zustand
    def set_document(self, document: SlideDocument | None) -> None:
        self._document = document
        self.update()

    def set_page(self, page: int | None) -> None:
        if page != self._page:
            self._page = page
            self.update()

    def page(self) -> int | None:
        return self._page

    def set_blank(self, mode: str) -> None:
        if mode != self._blank:
            self._blank = mode
            self.update()

    def set_placeholder(self, text: str) -> None:
        """Text, der angezeigt wird, wenn keine Seite gesetzt ist."""
        self._placeholder = text
        self.update()

    # ----------------------------------------------------------- Rendering
    def _slide_rect(self, page: int) -> QRectF:
        assert self._document is not None
        size = self._document.page_size(page).scaled(
            QSizeF(self.size()), Qt.AspectRatioMode.KeepAspectRatio
        )
        x = (self.width() - size.width()) / 2
        y = (self.height() - size.height()) / 2
        return QRectF(x, y, size.width(), size.height())

    def _pixel_size(self, rect: QRectF) -> QSize:
        dpr = self.devicePixelRatioF()
        return QSize(max(1, round(rect.width() * dpr)), max(1, round(rect.height() * dpr)))

    def prerender(self, page: int | None) -> None:
        """Rendert eine Seite in den Cache, damit der nächste Wechsel sofort geht."""
        if self._document is None or page is None or not self.isVisible():
            return
        if 0 <= page < self._document.page_count:
            self._document.render(page, self._pixel_size(self._slide_rect(page)))

    def paintEvent(self, event: QPaintEvent) -> None:  # noqa: N802 (Qt-API)
        painter = QPainter(self)
        if self._blank == BLANK_BLACK:
            painter.fillRect(self.rect(), Qt.GlobalColor.black)
            return
        if self._blank == BLANK_WHITE:
            painter.fillRect(self.rect(), Qt.GlobalColor.white)
            return

        painter.fillRect(self.rect(), self._background)
        doc = self._document
        if doc is None or self._page is None or not 0 <= self._page < doc.page_count:
            if self._placeholder:
                painter.setPen(self._placeholder_color)
                painter.drawText(
                    self.rect(), Qt.AlignmentFlag.AlignCenter | Qt.TextFlag.TextWordWrap,
                    self._placeholder,
                )
            return

        rect = self._slide_rect(self._page)
        image = doc.render(self._page, self._pixel_size(rect))
        # Transparente PDF-Hintergründe sollen weiß wirken wie im PDF-Viewer.
        painter.fillRect(rect, Qt.GlobalColor.white)
        if not image.isNull():
            image.setDevicePixelRatio(self.devicePixelRatioF())
            painter.drawImage(rect, image)
