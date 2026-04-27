$ErrorActionPreference = "Stop"

$torchCuda = python -c "import torch; print(torch.version.cuda or '')"
if ($torchCuda) {
    throw "This Python environment uses CUDA PyTorch ($torchCuda). Install CPU-only torch before building the lite release."
}

$env:MIO_TRANSLATOR_BUNDLE_MODELS = "0"
python -m PyInstaller --clean --noconfirm MioTranslator.spec

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$bundledModelsDir = "dist\MioTranslator\models"
if (Test-Path $bundledModelsDir) {
    Remove-Item -LiteralPath $bundledModelsDir -Recurse -Force
}

Write-Host "Release-lite build finished. Models are excluded from the bundle."

