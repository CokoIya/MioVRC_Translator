$ErrorActionPreference = "Stop"

$env:MIO_TRANSLATOR_BUNDLE_MODELS = "0"
python -m PyInstaller --noconfirm MioTranslator.spec

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Release-lite build finished. Models are excluded from the bundle."
