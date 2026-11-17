"""Thumbnail-Liste, die Vorschaubilder schrittweise im Hintergrund der Eventloop rendert."""

from __future__ import annotations

from PySide6.QtCore import QSize, Qt, QTimer
from PySide6.QtGui import QColor, QIcon, QPainter, QPixmap
from PySide6.QtWidgets import QListView, QListWidget, QListWidgetItem, QWidget

from pdf_presenter.document import SlideDocument


class ThumbnailList(QListWidget):
    BATCH = 4

    def __init__(self, parent: QWidget | None = None, *, thumb_width: int = 160, grid: bool = False):
        super().__init__(parent)
        self._document: SlideDocument | None = None
        self._thumb_width = thumb_width
        self._pending: list[int] = []
        self._loader = QTimer(self)
        self._loader.setInterval(0)
        self._loader.timeout.connect(self._render_batch)

        self.setViewMode(QListView.ViewMode.IconMode)
        self.setMovement(QListView.Movement.Static)
        self.setResizeMode(QListView.ResizeMode.Adjust)
        self.setUniformItemSizes(True)
        self.setSpacing(6)
        if grid:
            self.setFlow(QListView.Flow.LeftToRight)
            self.setWrapping(True)
        else:
            self.setFlow(QListView.Flow.TopToBottom)
            self.setWrapping(False)
            self.setHorizontalScrollBarPolicy(Qt.ScrollBarPolicy.ScrollBarAlwaysOff)

    def set_document(self, document: SlideDocument | None) -> None:
        self._loader.stop()
        self.clear()
        self._document = document
        if document is None:
            return
        width = self._thumb_width
        height = max(1, round(width / document.aspect_ratio(0)))
        self.setIconSize(QSize(width, height))
        placeholder = QPixmap(width, height)
        placeholder.fill(QColor("#d0d0d0"))
        for page in range(document.page_count):
            item = QListWidgetItem(QIcon(placeholder), str(page + 1))
            item.setData(Qt.ItemDataRole.UserRole, page)
            item.setTextAlignment(Qt.AlignmentFlag.AlignHCenter)
            self.addItem(item)
        self._pending = list(range(document.page_count))
        self._loader.start()

    def _render_batch(self) -> None:
        if self._document is None or not self._pending:
            self._loader.stop()
            return
        dpr = self.devicePixelRatioF()
        box = self.iconSize() * dpr
        for _ in range(min(self.BATCH, len(self._pending))):
            page = self._pending.pop(0)
            image = self._document.render_fit(page, box, cache=False)
            if image.isNull():
                continue
            pixmap = QPixmap(image.size())
            pixmap.fill(Qt.GlobalColor.white)
            painter = QPainter(pixmap)
            painter.drawImage(0, 0, image)
            painter.end()
            pixmap.setDevicePixelRatio(dpr)
            self.item(page).setIcon(QIcon(pixmap))

    def select_page(self, page: int) -> None:
        if 0 <= page < self.count():
            self.setCurrentRow(page)
            self.scrollToItem(self.item(page))

    def selected_page(self) -> int | None:
        item = self.currentItem()
        return None if item is None else int(item.data(Qt.ItemDataRole.UserRole))
