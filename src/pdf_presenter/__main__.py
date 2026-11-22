"""Startpunkt: ``python -m pdf_presenter [datei.pdf|datei.pdfpres]``."""

from __future__ import annotations

import sys

from PySide6.QtCore import QCoreApplication, QTimer
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
    marker = None
    if crashreport.is_child():
        crashreport.init_child(app)
    else:
        marker = crashreport.RunMarker()

    window = MainWindow()
    window.show()
    warning = unsafe_runtime_warning()
    if warning:
        print(warning, file=sys.stderr)
        QMessageBox.warning(window, APP_NAME, warning)
    files = [a for a in app.arguments()[1:] if not a.startswith("-")]
    if files:
        window.open_path(files[0])
    if marker is not None and marker.previous_crashes() and not crashreport.is_enabled():
        QTimer.singleShot(0, window.offer_crash_reports)
    exit_code = app.exec()
    if marker is not None:
        marker.release()
    return exit_code


if __name__ == "__main__":
    sys.exit(main())
