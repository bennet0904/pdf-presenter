import pytest
from PySide6.QtCore import QSize

from pdf_presenter.document import SlideDocument
from pdf_presenter.project import Project, ProjectError


def test_renders_real_pdf(sample_pdf):
    doc = SlideDocument(Project.open(sample_pdf).pdf_bytes)
    assert doc.page_count == 5
    assert doc.aspect_ratio() == pytest.approx(16 / 9, rel=0.01)

    image = doc.render(0, QSize(320, 180))
    assert image.size() == QSize(320, 180)
    # Hintergrund der ersten Beispielfolie ist dunkelblau, nicht leer.
    assert image.pixelColor(5, 5).blue() > 100

    assert doc.render(0, QSize(320, 180)) is image  # aus dem Cache
    assert doc.render(99, QSize(10, 10)).isNull()

    fitted = doc.render_fit(1, QSize(400, 400))
    assert fitted.width() == 400 and fitted.height() < 400


def test_garbage_pdf_raises(qapp):
    with pytest.raises(ProjectError):
        SlideDocument(b"%PDF-1.4 kaputt")
