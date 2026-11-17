"""PDF-Rendering über QtPdf mit kleinem LRU-Cache."""

from __future__ import annotations

from collections import OrderedDict

from PySide6.QtCore import QBuffer, QByteArray, QIODevice, QSize, QSizeF, Qt
from PySide6.QtGui import QImage
from PySide6.QtPdf import QPdfDocument

from pdf_presenter.project import ProjectError


class SlideDocument:
    """Hält ein geladenes ``QPdfDocument`` und rendert Seiten als ``QImage``."""

    CACHE_SIZE = 16

    def __init__(self, pdf_bytes: bytes):
        # Buffer und Daten müssen leben, solange das Dokument offen ist.
        self._data = QByteArray(pdf_bytes)
        self._buffer = QBuffer(self._data)
        self._buffer.open(QIODevice.OpenModeFlag.ReadOnly)
        self._doc = QPdfDocument()
        self._doc.load(self._buffer)
        if self._doc.status() != QPdfDocument.Status.Ready or self._doc.pageCount() <= 0:
            error = self._doc.error()
            if error == QPdfDocument.Error.IncorrectPassword:
                raise ProjectError("Passwortgeschützte PDFs werden nicht unterstützt.")
            raise ProjectError(f"PDF konnte nicht geladen werden ({error.name}).")
        self._cache: OrderedDict[tuple[int, int, int], QImage] = OrderedDict()

    @property
    def page_count(self) -> int:
        return self._doc.pageCount()

    def page_size(self, page: int) -> QSizeF:
        """Seitengröße in Punkten (1/72 Zoll)."""
        size = self._doc.pagePointSize(page)
        if size.isEmpty():
            return QSizeF(4, 3)
        return size

    def aspect_ratio(self, page: int = 0) -> float:
        size = self.page_size(page)
        return size.width() / size.height()

    def render(self, page: int, size: QSize, *, cache: bool = True) -> QImage:
        """Rendert ``page`` in genau ``size`` Pixeln."""
        if not 0 <= page < self.page_count or size.isEmpty():
            return QImage()
        key = (page, size.width(), size.height())
        if cache:
            hit = self._cache.get(key)
            if hit is not None:
                self._cache.move_to_end(key)
                return hit
        image = self._doc.render(page, size)
        if cache and not image.isNull():
            self._cache[key] = image
            while len(self._cache) > self.CACHE_SIZE:
                self._cache.popitem(last=False)
        return image

    def render_fit(self, page: int, box: QSize, *, cache: bool = True) -> QImage:
        """Rendert seitenverhältnistreu so groß wie möglich innerhalb von ``box``."""
        target = self.page_size(page).scaled(QSizeF(box), Qt.AspectRatioMode.KeepAspectRatio).toSize()
        return self.render(page, target, cache=cache)

    def close(self) -> None:
        self._cache.clear()
        self._doc.close()
        self._buffer.close()

