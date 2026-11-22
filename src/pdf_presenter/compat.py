"""Erkennt Kombinationen aus Python und PySide6, die bekanntermaßen abstürzen."""

from __future__ import annotations

import sys

import PySide6

# PySide6-Versionen, die unter Python < 3.12 die Referenzzähler von None,
# True und False beschädigen. Die App stürzt dann nach einiger Zeit ab.
BROKEN_BELOW_PY312 = ("6.12.0",)


def unsafe_runtime_warning() -> str | None:
    """Warntext, falls diese Laufzeitumgebung bekannt instabil ist, sonst ``None``."""
    if sys.version_info >= (3, 12):
        return None
    version = PySide6.__version__
    if version not in BROKEN_BELOW_PY312:
        return None
    py = ".".join(map(str, sys.version_info[:3]))
    return (
        f"PySide6 {version} ist mit Python {py} nicht stabil und führt nach "
        "kurzer Zeit zum Absturz des Programms.\n\n"
        "Abhilfe: Abhängigkeiten neu installieren (pip install -e .), dann wird "
        "eine passende PySide6-Version gewählt, oder Python 3.12 bzw. neuer verwenden."
    )
