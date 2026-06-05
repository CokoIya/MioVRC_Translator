$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSCommandPath
if ($repoRoot) {
    Set-Location $repoRoot
}

$releasePythonCandidates = @(
    (Join-Path $repoRoot ".venv-release311\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv311\Scripts\python.exe"),
    (Join-Path $repoRoot ".venv\Scripts\python.exe"),
    "python"
)
$releasePython = $releasePythonCandidates | Where-Object { $_ -eq "python" -or (Test-Path $_) } | Select-Object -First 1
Write-Host "Using release Python: $releasePython"

& $releasePython tools\check_release_environment.py
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$modelDir = "models\sensevoice-small"
if (-not ((Test-Path (Join-Path $modelDir "model.pt")) -and ((Test-Path (Join-Path $modelDir "configuration.json")) -or (Test-Path (Join-Path $modelDir "config.yaml"))))) {
    throw "Bundled SenseVoice model was not found at $modelDir. Run the release Python with download_models.py first."
}

$env:MIO_TRANSLATOR_BUNDLE_MODELS = "1"
& $releasePython -m PyInstaller --clean --noconfirm MioTranslator.spec

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

$signPfx = $env:MIO_TRANSLATOR_SIGN_PFX
$signPass = $env:MIO_TRANSLATOR_SIGN_PASS
if (-not $signPfx) {
    $signPfx = $env:MIO_VRC_SIGN_PFX
    $signPass = $env:MIO_VRC_SIGN_PASS
}

$isccArgs = @("/DBundleModels")
if ($signPfx -and $signPass) {
    if (-not (Test-Path $signPfx)) {
        throw "Code signing certificate not found: $signPfx"
    }
    $isccArgs += "/DSignPfx=$signPfx"
    $isccArgs += "/DSignPass=$signPass"
    Write-Host "Signing installer with configured certificate."
} else {
    Write-Warning "No Authenticode certificate configured. Windows will show an unknown publisher; in-app updates still require a signed manifest and SHA256 match."
}

& $iscc @isccArgs "MioTranslator-installer.iss"

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

Write-Host "Release-full installer finished. Upload it for first-time users."
