$ErrorActionPreference = "Stop"

$torchCuda = python -c "import torch; print(torch.version.cuda or '')"
if ($torchCuda) {
    throw "This Python environment uses CUDA PyTorch ($torchCuda). Install CPU-only torch before building the lite installer."
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

$compilerCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)

$iscc = $compilerCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 was not found. Please install Inno Setup 6 or compile MioTranslator-installer.iss manually."
}

& $iscc "MioTranslator-installer.iss"

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Release-lite installer finished. Upload it for existing users and in-app updates."

