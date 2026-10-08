# Builds dist\JungleClearTracker.exe (single file, no console window).
#   powershell -ExecutionPolicy Bypass -File build.ps1
$ErrorActionPreference = "Stop"
Set-Location $PSScriptRoot

python -m pip install --upgrade -r requirements.txt pyinstaller
python -m PyInstaller --noconfirm --clean --onefile --windowed `
    --name JungleClearTracker `
    --add-data "jct\patch.json;jct" `
    --add-data "jct\assets;jct\assets" `
    --exclude-module scipy --exclude-module pandas `
    run.py

Write-Host "Built: $PSScriptRoot\dist\JungleClearTracker.exe"
