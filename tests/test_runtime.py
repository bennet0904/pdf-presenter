"""Schützt vor PySide-Versionen, die Referenzzähler von None/True/False beschädigen."""

import sys

import pytest
from PySide6.QtCore import QObject, Signal
from PySide6.QtWidgets import QLabel

from pdf_presenter import compat

CALLS = 2000

pytestmark = pytest.mark.skipif(
    sys.version_info >= (3, 12), reason="None/True/False sind ab Python 3.12 unsterblich"
)


class _Emitter(QObject):
    changed = Signal(bool)


def test_void_methods_keep_none_alive(qapp):
    label = QLabel()
    before = sys.getrefcount(None)
    for _ in range(CALLS):
        label.setText("x")
    # Kleine Schwankungen sind normal, ein Verlust pro Aufruf nicht.
    assert sys.getrefcount(None) > before - CALLS // 2


def test_signal_emit_keeps_bools_alive(qapp):
    emitter = _Emitter()
    emitter.changed.connect(lambda _v: None)
    before = sys.getrefcount(True), sys.getrefcount(False)
    for _ in range(CALLS):
        emitter.changed.emit(True)
    after = sys.getrefcount(True), sys.getrefcount(False)
    assert all(a > b - CALLS // 2 for a, b in zip(after, before))


def test_installed_combination_is_not_flagged():
    assert compat.unsafe_runtime_warning() is None


def test_broken_combination_is_flagged(monkeypatch):
    monkeypatch.setattr(compat.PySide6, "__version__", "6.12.0")
    assert "6.12.0" in compat.unsafe_runtime_warning()
