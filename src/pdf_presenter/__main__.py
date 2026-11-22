"""Startpunkt: ``python -m pdf_presenter [datei.pdf|datei.pdfpres]``."""

from __future__ import annotations

import sys

from PySide6.QtWidgets import QApplication, QMessageBox

from pdf_presenter.compat import unsafe_runtime_warning
from pdf_presenter.windows.main_window import APP_NAME, MainWindow


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    app = QApplication(argv)
    app.setApplicationName(APP_NAME)
    app.setOrganizationName("pdf-presenter")
    app.setApplicationDisplayName(APP_NAME)

    window = MainWindow()
    window.show()
    warning = unsafe_runtime_warning()
    if warning:
        print(warning, file=sys.stderr)
        QMessageBox.warning(window, APP_NAME, warning)
    files = [a for a in app.arguments()[1:] if not a.startswith("-")]
    if files:
        window.open_path(files[0])
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
