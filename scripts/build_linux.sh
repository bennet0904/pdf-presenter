#!/usr/bin/env bash
# Baut dist/PDF-Presenter/PDF-Presenter (auf einem Linux-System ausführen)
set -euo pipefail
cd "$(dirname "$0")/.."
[ -d .venv ] || python3 -m venv .venv
.venv/bin/python -m pip install -e ".[dev]"
.venv/bin/pyinstaller --noconfirm --clean --windowed --name PDF-Presenter \
    --paths src src/pdf_presenter/__main__.py
echo "Fertig: dist/PDF-Presenter/PDF-Presenter"
