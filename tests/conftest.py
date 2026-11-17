import os
import sys
from pathlib import Path

import pytest

os.environ.setdefault("QT_QPA_PLATFORM", "offscreen")
sys.path.insert(0, str(Path(__file__).resolve().parents[1] / "scripts"))


@pytest.fixture(scope="session")
def qapp():
    from PySide6.QtWidgets import QApplication

    return QApplication.instance() or QApplication([])


@pytest.fixture
def sample_pdf(qapp, tmp_path):
    from make_sample_pdf import make_sample_pdf

    return make_sample_pdf(tmp_path / "beispiel.pdf", pages=5)
