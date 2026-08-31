$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

$version = python -c "import sys; print(f'{sys.version_info.major}.{sys.version_info.minor}')"
$parts = $version.Split('.')
if ([int]$parts[0] -ne 3 -or [int]$parts[1] -lt 11 -or [int]$parts[1] -gt 14) {
    throw "Jobagenten kræver Python 3.11–3.14. Fundet: $version"
}

if (Test-Path .venv) {
    Write-Host "Fjerner tidligere virtuelt Python-miljø…"
    Remove-Item -Recurse -Force .venv
}

python -m venv .venv
& .\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip setuptools wheel
python -m pip install --prefer-binary -r requirements.txt
python -m playwright install chromium
python -m pytest -q
New-Item -ItemType File -Force .setup-complete | Out-Null
Write-Host "Setup færdig og selvtest bestået. Start med .\start.ps1"
