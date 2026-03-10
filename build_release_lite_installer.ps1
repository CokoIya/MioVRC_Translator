$ErrorActionPreference = "Stop"

$env:MIO_TRANSLATOR_BUNDLE_MODELS = "0"
python -m PyInstaller --noconfirm MioTranslator.spec

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

& $iscc "MioTranslator-installer.iss"

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Release-lite installer finished: dist\\MioTranslator-Setup-V1.2.0_beta3.2_Releases.exe"

