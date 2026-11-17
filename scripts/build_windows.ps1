# Baut dist\PDF-Presenter\PDF-Presenter.exe
$ErrorActionPreference = "Stop"
Set-Location (Join-Path $PSScriptRoot "..")
if (-not (Test-Path .venv)) { python -m venv .venv }
.\.venv\Scripts\python -m pip install -e ".[dev]"
.\.venv\Scripts\pyinstaller --noconfirm --clean --windowed --name PDF-Presenter `
    --paths src src\pdf_presenter\__main__.py
Write-Host "Fertig: dist\PDF-Presenter\PDF-Presenter.exe"
