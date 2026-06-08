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
$forbiddenRuntimeDirs = @("scipy", "scipy.libs", "numba", "llvmlite")
foreach ($name in $forbiddenRuntimeDirs) {
    $candidate = Join-Path $internalDir $name
    if (Test-Path $candidate) {
        throw "Forbidden runtime dependency found in release bundle: $candidate"
    }
}

Write-Host "Release-lite build finished. Models are excluded from the bundle."
