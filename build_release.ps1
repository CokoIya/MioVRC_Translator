$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSCommandPath
if ($repoRoot) {
    Set-Location $repoRoot
}

$releasePython = Join-Path $repoRoot ".venv-release311\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $releasePython -PathType Leaf)) {
    throw "The dedicated Python 3.11 release environment is missing. Run .\rebuild_release_environment.ps1 first."
}
Write-Host "Using release Python: $releasePython"

$releaseSeed = $env:MIO_RELEASE_SIGNING_SEED
if (-not $releaseSeed) {
    $releaseSeed = $env:MIO_MANIFEST_SEED
}
if (-not $releaseSeed) {
    throw "Release builds require MIO_RELEASE_SIGNING_SEED for Ed25519 installer and manifest signatures."
}
# Keep the signing seed out of PyInstaller, Inno Setup, and their child
# processes. It is restored only for the final trusted manifest-signing tool.
Remove-Item Env:MIO_RELEASE_SIGNING_SEED -ErrorAction SilentlyContinue
Remove-Item Env:MIO_MANIFEST_SEED -ErrorAction SilentlyContinue

$signPfx = $env:MIO_TRANSLATOR_SIGN_PFX
$signPass = $env:MIO_TRANSLATOR_SIGN_PASS
if (-not $signPfx -and -not $signPass) {
    $signPfx = $env:MIO_VRC_SIGN_PFX
    $signPass = $env:MIO_VRC_SIGN_PASS
}
$useAuthenticode = [bool]$signPfx -or [bool]$signPass
if ($useAuthenticode -and (-not $signPfx -or -not $signPass)) {
    throw "Optional Authenticode signing requires both MIO_TRANSLATOR_SIGN_PFX and MIO_TRANSLATOR_SIGN_PASS."
}
if ($useAuthenticode -and -not (Test-Path -LiteralPath $signPfx -PathType Leaf)) {
    throw "Code signing certificate not found: $signPfx"
}
Remove-Item Env:MIO_TRANSLATOR_SIGN_PFX -ErrorAction SilentlyContinue
Remove-Item Env:MIO_TRANSLATOR_SIGN_PASS -ErrorAction SilentlyContinue
Remove-Item Env:MIO_VRC_SIGN_PFX -ErrorAction SilentlyContinue
Remove-Item Env:MIO_VRC_SIGN_PASS -ErrorAction SilentlyContinue

& $releasePython tools\ensure_silero_vad.py
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $releasePython tools\check_release_environment.py
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$sourceSelftestHome = Join-Path $repoRoot (".release-source-selftest-" + [Guid]::NewGuid().ToString("N"))
$hadSourceSelftestHome = Test-Path Env:MIO_TRANSLATOR_HOME
$previousSourceSelftestHome = $env:MIO_TRANSLATOR_HOME
$hadSourceNoVenvRelaunch = Test-Path Env:MIO_TRANSLATOR_NO_VENV_RELAUNCH
$previousSourceNoVenvRelaunch = $env:MIO_TRANSLATOR_NO_VENV_RELAUNCH
try {
    $env:MIO_TRANSLATOR_HOME = $sourceSelftestHome
    $env:MIO_TRANSLATOR_NO_VENV_RELAUNCH = "1"
    & $releasePython main.py --mio-selftest
    if ($LASTEXITCODE -ne 0) {
        throw "Source runtime self-test failed; refusing to package an incomplete XTTS/ASR runtime."
    }
} finally {
    if ($hadSourceSelftestHome) {
        $env:MIO_TRANSLATOR_HOME = $previousSourceSelftestHome
    } else {
        Remove-Item Env:MIO_TRANSLATOR_HOME -ErrorAction SilentlyContinue
    }
    if ($hadSourceNoVenvRelaunch) {
        $env:MIO_TRANSLATOR_NO_VENV_RELAUNCH = $previousSourceNoVenvRelaunch
    } else {
        Remove-Item Env:MIO_TRANSLATOR_NO_VENV_RELAUNCH -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $sourceSelftestHome) {
        Remove-Item -LiteralPath $sourceSelftestHome -Recurse -Force
    }
}

& $releasePython tools\ensure_pyopenjtalk_dict.py
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

& $releasePython -m PyInstaller --clean --noconfirm MioTranslator.spec
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}

$frozenExe = Join-Path $repoRoot "dist\MioTranslator\MioTranslator.exe"
if (-not (Test-Path -LiteralPath $frozenExe -PathType Leaf)) {
    throw "Frozen application was not produced: $frozenExe"
}
$frozenSelftestRoot = Join-Path $repoRoot (".release-frozen-selftest-" + [Guid]::NewGuid().ToString("N"))
$frozenSelftestStdout = Join-Path $frozenSelftestRoot "stdout.txt"
$frozenSelftestStderr = Join-Path $frozenSelftestRoot "stderr.txt"
$hadFrozenSelftestHome = Test-Path Env:MIO_TRANSLATOR_HOME
$previousFrozenSelftestHome = $env:MIO_TRANSLATOR_HOME
New-Item -ItemType Directory -Path $frozenSelftestRoot | Out-Null
try {
    $env:MIO_TRANSLATOR_HOME = Join-Path $frozenSelftestRoot "home"
    $frozenSelftest = Start-Process `
        -FilePath $frozenExe `
        -ArgumentList "--mio-selftest" `
        -RedirectStandardOutput $frozenSelftestStdout `
        -RedirectStandardError $frozenSelftestStderr `
        -WindowStyle Hidden `
        -Wait `
        -PassThru
    if ($frozenSelftest.ExitCode -ne 0) {
        $selftestDetails = @(
            (Get-Content -LiteralPath $frozenSelftestStdout -Raw -ErrorAction SilentlyContinue),
            (Get-Content -LiteralPath $frozenSelftestStderr -Raw -ErrorAction SilentlyContinue)
        ) -join [Environment]::NewLine
        throw "Frozen runtime self-test failed with exit code $($frozenSelftest.ExitCode): $selftestDetails"
    }
} finally {
    if ($hadFrozenSelftestHome) {
        $env:MIO_TRANSLATOR_HOME = $previousFrozenSelftestHome
    } else {
        Remove-Item Env:MIO_TRANSLATOR_HOME -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $frozenSelftestRoot) {
        Remove-Item -LiteralPath $frozenSelftestRoot -Recurse -Force
    }
}

$compilerCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $compilerCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 was not found. Please install Inno Setup 6."
}

$isccArgs = @()
if ($useAuthenticode) {
    $isccArgs += "/DSignPfx=$signPfx"
    $isccArgs += "/DSignPass=$signPass"
    Write-Host "Building with optional Authenticode signing."
} else {
    Write-Host "Building without Authenticode; Ed25519 release signatures remain mandatory."
}
try {
    & $iscc @isccArgs "MioTranslator-installer.iss"
    $isccExitCode = $LASTEXITCODE
} finally {
    $signPass = $null
}
if ($isccExitCode -ne 0) {
    exit $isccExitCode
}

$installerPath = & $releasePython -c "from pathlib import Path; from src.version import APP_VERSION; print(Path('dist') / f'MioTranslator-Setup-v{APP_VERSION}.exe')"
if ($LASTEXITCODE -ne 0) {
    exit $LASTEXITCODE
}
$installerPath = [string]$installerPath
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Compiled installer was not found: $installerPath"
}
$installerItem = Get-Item -LiteralPath $installerPath -Force
if (($installerItem.Attributes -band [System.IO.FileAttributes]::ReparsePoint) -ne 0) {
    throw "Compiled installer must not be a reparse point: $installerPath"
}
if ($installerItem.Length -le 0) {
    throw "Compiled installer is empty: $installerPath"
}

if ($useAuthenticode) {
    $signature = Get-AuthenticodeSignature -LiteralPath $installerPath
    if ($signature.Status -ne 'Valid' -or $null -eq $signature.SignerCertificate) {
        throw "Compiled installer Authenticode validation failed: $($signature.Status) $($signature.StatusMessage)"
    }
}

try {
    $env:MIO_RELEASE_SIGNING_SEED = $releaseSeed
    & $releasePython tools\update_release_manifests.py $installerPath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
} finally {
    Remove-Item Env:MIO_RELEASE_SIGNING_SEED -ErrorAction SilentlyContinue
    $releaseSeed = $null
}

Write-Host "Release build and Ed25519 installer/manifest signing finished."
