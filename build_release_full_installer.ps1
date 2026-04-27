$ErrorActionPreference = "Stop"

$torchCuda = python -c "import torch; print(torch.version.cuda or '')"
if ($torchCuda) {
    throw "This Python environment uses CUDA PyTorch ($torchCuda). Install CPU-only torch before building the full installer."
}

$modelDir = "models\sensevoice-small"
if (-not ((Test-Path (Join-Path $modelDir "model.pt")) -and ((Test-Path (Join-Path $modelDir "configuration.json")) -or (Test-Path (Join-Path $modelDir "config.yaml"))))) {
    throw "Bundled SenseVoice model was not found at $modelDir. Run python download_models.py first."
}

$env:MIO_TRANSLATOR_BUNDLE_MODELS = "1"
python -m PyInstaller --clean --noconfirm MioTranslator.spec

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$compilerCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe"
)

$iscc = $compilerCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 was not found. Please install Inno Setup 6 or compile MioTranslator-installer.iss manually."
}

& $iscc /DBundleModels "MioTranslator-installer.iss"

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Release-full installer finished. Upload it for first-time users."
