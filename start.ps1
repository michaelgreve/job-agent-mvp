$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot
if (-not (Test-Path .venv)) { throw "Kør .\setup.ps1 først" }
& .\.venv\Scripts\Activate.ps1
Start-Process "http://127.0.0.1:8765"
python run.py
