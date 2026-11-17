"""Erzeugt eine Beispiel-PDF (16:9) zum Ausprobieren.

    python scripts/make_sample_pdf.py beispiel.pdf [Seitenzahl]
"""

from __future__ import annotations

import sys
from pathlib import Path

from PySide6.QtCore import QMarginsF, QRectF, Qt
from PySide6.QtGui import QColor, QFont, QGuiApplication, QPageLayout, QPageSize, QPainter, QPdfWriter

COLORS = ["#1f4e79", "#7a2e8c", "#2e7d32", "#b23c17", "#00695c", "#5d4037"]


def make_sample_pdf(path: str | Path, pages: int = 8) -> Path:
    path = Path(path)
    writer = QPdfWriter(str(path))
    writer.setResolution(96)
    size = QPageSize(QRectF(0, 0, 254, 142.875).size(), QPageSize.Unit.Millimeter, "16:9")
    writer.setPageLayout(QPageLayout(size, QPageLayout.Orientation.Portrait, QMarginsF(0, 0, 0, 0)))
    writer.setTitle("PDF Presenter – Beispiel")

    painter = QPainter(writer)
    try:
        for i in range(pages):
            if i:
                writer.newPage()
            rect = QRectF(painter.viewport())
            painter.fillRect(rect, QColor(COLORS[i % len(COLORS)]))
            painter.setPen(Qt.GlobalColor.white)
            painter.setFont(QFont("Arial", 54, QFont.Weight.Bold))
            painter.drawText(rect.adjusted(0, -60, 0, -60), Qt.AlignmentFlag.AlignCenter, f"Folie {i + 1}")
            painter.setFont(QFont("Arial", 20))
            painter.drawText(
                rect.adjusted(0, 80, 0, 80), Qt.AlignmentFlag.AlignCenter,
                f"Beispielpräsentation · {i + 1} von {pages}",
            )
    finally:
        painter.end()
    return path


if __name__ == "__main__":
    app = QGuiApplication.instance() or QGuiApplication(sys.argv[:1])
    out = sys.argv[1] if len(sys.argv) > 1 else "beispiel.pdf"
    count = int(sys.argv[2]) if len(sys.argv) > 2 else 8
    print(make_sample_pdf(out, count))
