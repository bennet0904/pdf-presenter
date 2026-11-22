"""Startpunkt: ``python -m pdf_presenter [datei.pdf|datei.pdfpres]``."""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication
from PySide6.QtWidgets import QApplication, QMessageBox

from pdf_presenter import crashreport, supervisor
from pdf_presenter.compat import unsafe_runtime_warning
from pdf_presenter.windows.main_window import APP_NAME, MainWindow

ORG_NAME = "pdf-presenter"


def main(argv: list[str] | None = None) -> int:
    argv = list(sys.argv if argv is None else argv)
    # Vor der QApplication setzen, damit der Supervisor die Einstellungen lesen kann.
    QCoreApplication.setOrganizationName(ORG_NAME)
    QCoreApplication.setApplicationName(APP_NAME)
    if supervisor.should_supervise(argv):
        return supervisor.run(argv)

    app = QApplication(argv)
    app.setApplicationDisplayName(APP_NAME)
    if crashreport.is_child():
        crashreport.init_child(app)

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
