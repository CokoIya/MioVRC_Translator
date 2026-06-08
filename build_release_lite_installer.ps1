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

& $releasePython tools\ensure_pyopenjtalk_dict.py
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$env:MIO_TRANSLATOR_BUNDLE_MODELS = "0"
& $releasePython -m PyInstaller --clean --noconfirm MioTranslator.spec

if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$bundledModelsDir = "dist\MioTranslator\models"
if (Test-Path $bundledModelsDir) {
    Remove-Item -LiteralPath $bundledModelsDir -Recurse -Force
}

$internalDir = "dist\MioTranslator\_internal"
$forbiddenRuntimeDirs = @()
foreach ($name in $forbiddenRuntimeDirs) {
    $candidate = Join-Path $internalDir $name
    if (Test-Path $candidate) {
        throw "Forbidden runtime dependency found in release bundle: $candidate"
    }
}

$compilerCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
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

$isccArgs = @()
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

Write-Host "Release-lite installer finished. Upload it for existing users and in-app updates."
