$ErrorActionPreference = "Stop"

$projectRoot = Split-Path -Parent $MyInvocation.MyCommand.Path
Set-Location $projectRoot

$python = Join-Path $projectRoot ".venv\Scripts\python.exe"
$pyinstaller = Join-Path $projectRoot ".venv\Scripts\pyinstaller.exe"

if (-not (Test-Path $python)) {
    throw "No existe .venv. Crea el entorno con: python -m venv .venv"
}

if (-not (Test-Path $pyinstaller)) {
    & $python -m pip install --upgrade pip
    & $python -m pip install -r requirements.txt pyinstaller
}

$env:PLAYWRIGHT_BROWSERS_PATH = "0"
& $python -m playwright install chromium

Remove-Item -Recurse -Force -ErrorAction SilentlyContinue build, dist
Remove-Item -Force -ErrorAction SilentlyContinue QA-Test-Recorder.spec

& $pyinstaller `
    --noconfirm `
    --clean `
    --windowed `
    --onedir `
    --name QA-Test-Recorder `
    --collect-all playwright `
    --collect-all docx `
    --collect-all axe_playwright_python `
    app\main.py

$distribution = Join-Path $projectRoot "dist\QA-Test-Recorder"
if (-not (Test-Path (Join-Path $distribution "_internal\python313.dll"))) {
    throw "La distribución no contiene python313.dll."
}

Write-Host "Compilación terminada: $distribution" -ForegroundColor Green
Write-Host "Ejecutable: $distribution\QA-Test-Recorder.exe" -ForegroundColor Green
Write-Host "En otra PC debes copiar toda la carpeta QA-Test-Recorder." -ForegroundColor Yellow
