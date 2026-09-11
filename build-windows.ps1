$ErrorActionPreference = "Stop"

Write-Host "Installing dependencies..."
python -m pip install --upgrade pip
python -m pip install -r requirements.txt

Write-Host "Building FileDrop.exe..."
python -m PyInstaller --noconfirm --clean FileDrop.spec

if (-not (Test-Path "dist\\FileDrop.exe")) {
    throw "Build finished without dist\\FileDrop.exe"
}

$size = (Get-Item "dist\\FileDrop.exe").Length
Write-Host ("Created dist\\FileDrop.exe ({0:N0} bytes)" -f $size)
