param(
    # Numbers, then a label: "1.4.2_beta", "1.4.2_beta2", "1.5.0-rc1".
    [string]$BetaVersion = "1.4.2_beta"
)

# Internal test build for hand-picked players.
#
# What it does: the same freeze, self-tests and installer compile as
# build_release.ps1, stamped with a beta version.
# What it never does: sign manifests, touch mio_update.json or docs/, push
# anything, or delete other files in dist. The committed version surfaces
# (src/version.py, windows_version_info.txt, the .iss) are not edited; the
# beta version travels through environment variables and a stamp file that
# src/version.py reads back inside the frozen bundle.
#
# Output: dist\MioTranslator-Setup-v<BetaVersion>.exe and
#         dist\MioTranslator-v<BetaVersion>-SHA256SUMS.txt (plain, unsigned).

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSCommandPath
if ($repoRoot) {
    Set-Location $repoRoot
}
$repoRoot = [IO.Path]::GetFullPath((Get-Location).Path)

if ($BetaVersion -notmatch '^(\d+)\.(\d+)\.(\d+)(?:\.(\d+))?[-_.]?[A-Za-z]+\d*$') {
    throw "Beta version must be numbers followed by a label, like 1.4.2_beta: $BetaVersion"
}
$major = $Matches[1]
$minor = $Matches[2]
$patch = $Matches[3]
$build = if ($Matches[4]) { $Matches[4] } else { "0" }
$numericVersion = "$major.$minor.$patch"
$numericTuple = "$major, $minor, $patch, $build"
$displayVersion = "v$BetaVersion"

$releasePython = Join-Path $repoRoot ".venv-release311\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $releasePython -PathType Leaf)) {
    throw "The dedicated Python 3.11 release environment is missing. Run .\rebuild_release_environment.ps1 first."
}
Write-Host "Internal test build $displayVersion (resource version $numericVersion) with $releasePython"

# No signing of any kind here; keep the material away from child processes anyway.
foreach ($secretName in @(
    "MIO_RELEASE_SIGNING_SEED", "MIO_MANIFEST_SEED", "MIO_RELEASE_SIGNING_SEED_FILE",
    "MIO_TRANSLATOR_SIGN_PFX", "MIO_TRANSLATOR_SIGN_PASS", "MIO_VRC_SIGN_PFX", "MIO_VRC_SIGN_PASS"
)) {
    Remove-Item "Env:$secretName" -ErrorAction SilentlyContinue
}

function Invoke-WithTranslatorHome {
    param(
        [Parameter(Mandatory = $true)][string]$HomePath,
        [Parameter(Mandatory = $true)][scriptblock]$Body
    )
    $hadHome = Test-Path Env:MIO_TRANSLATOR_HOME
    $previousHome = $env:MIO_TRANSLATOR_HOME
    $hadNoRelaunch = Test-Path Env:MIO_TRANSLATOR_NO_VENV_RELAUNCH
    $previousNoRelaunch = $env:MIO_TRANSLATOR_NO_VENV_RELAUNCH
    try {
        $env:MIO_TRANSLATOR_HOME = $HomePath
        $env:MIO_TRANSLATOR_NO_VENV_RELAUNCH = "1"
        & $Body
    } finally {
        if ($hadHome) { $env:MIO_TRANSLATOR_HOME = $previousHome } else { Remove-Item Env:MIO_TRANSLATOR_HOME -ErrorAction SilentlyContinue }
        if ($hadNoRelaunch) { $env:MIO_TRANSLATOR_NO_VENV_RELAUNCH = $previousNoRelaunch } else { Remove-Item Env:MIO_TRANSLATOR_NO_VENV_RELAUNCH -ErrorAction SilentlyContinue }
        if (Test-Path -LiteralPath $HomePath) {
            Remove-Item -LiteralPath $HomePath -Recurse -Force
        }
    }
}

& $releasePython tools\ensure_silero_vad.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

& $releasePython tools\check_release_environment.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$sourceSelftestHome = Join-Path $repoRoot (".beta-source-selftest-" + [Guid]::NewGuid().ToString("N"))
Invoke-WithTranslatorHome -HomePath $sourceSelftestHome -Body {
    & $releasePython main.py --mio-selftest
    if ($LASTEXITCODE -ne 0) {
        throw "Source runtime self-test failed; refusing to package an incomplete ASR runtime."
    }
}

& $releasePython tools\ensure_pyopenjtalk_dict.py
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

# The beta's version, stamped for the bundle and the Windows resource.
$stampRoot = Join-Path $repoRoot ".beta-build"
if (Test-Path -LiteralPath $stampRoot) {
    Remove-Item -LiteralPath $stampRoot -Recurse -Force
}
New-Item -ItemType Directory -Path $stampRoot | Out-Null
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
$stampPath = Join-Path $stampRoot "mio_build_version.txt"
[IO.File]::WriteAllText($stampPath, $BetaVersion, $utf8NoBom)

$resourceSource = Join-Path $repoRoot "windows_version_info.txt"
$resource = [IO.File]::ReadAllText($resourceSource, $utf8NoBom)
$resource = [regex]::Replace($resource, 'filevers=\([^)]*\)', "filevers=($numericTuple)")
$resource = [regex]::Replace($resource, 'prodvers=\([^)]*\)', "prodvers=($numericTuple)")
$resource = [regex]::Replace(
    $resource,
    '(StringStruct\(u"(?:FileVersion|ProductVersion)",\s*u")v[^"]*(")',
    ('${1}' + $displayVersion + '${2}')
)
if ($resource -notmatch [regex]::Escape("filevers=($numericTuple)") -or $resource -notmatch [regex]::Escape("u`"$displayVersion`"")) {
    throw "Could not stamp $displayVersion into the Windows version resource."
}
$versionInfoPath = Join-Path $stampRoot "windows_version_info_beta.txt"
[IO.File]::WriteAllText($versionInfoPath, $resource, $utf8NoBom)

$env:MIO_BUILD_VERSION_STAMP = $stampPath
$env:MIO_BUILD_VERSION_INFO = $versionInfoPath
try {
    # --noconfirm replaces dist\MioTranslator only; the installers already in
    # dist are not touched.
    & $releasePython -m PyInstaller --clean --noconfirm MioTranslator.spec
    if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }
} finally {
    Remove-Item Env:MIO_BUILD_VERSION_STAMP -ErrorAction SilentlyContinue
    Remove-Item Env:MIO_BUILD_VERSION_INFO -ErrorAction SilentlyContinue
}

$frozenDir = Join-Path $repoRoot "dist\MioTranslator"
$frozenExe = Join-Path $frozenDir "MioTranslator.exe"
if (-not (Test-Path -LiteralPath $frozenExe -PathType Leaf)) {
    throw "Frozen application was not produced: $frozenExe"
}
$stampInBundle = Join-Path $frozenDir "_internal\mio_build_version.txt"
if (-not (Test-Path -LiteralPath $stampInBundle -PathType Leaf)) {
    throw "The version stamp did not reach the bundle: $stampInBundle"
}
$stamped = ([IO.File]::ReadAllText($stampInBundle, $utf8NoBom)).Trim()
if ($stamped -ne $BetaVersion) {
    throw "The bundle is stamped '$stamped', expected '$BetaVersion'."
}
$frozenVersion = [Diagnostics.FileVersionInfo]::GetVersionInfo($frozenExe).ProductVersion
Write-Host "Frozen executable reports product version: $frozenVersion"

$frozenSelftestRoot = Join-Path $repoRoot (".beta-frozen-selftest-" + [Guid]::NewGuid().ToString("N"))
New-Item -ItemType Directory -Path $frozenSelftestRoot | Out-Null
try {
    $frozenStdout = Join-Path $frozenSelftestRoot "stdout.txt"
    $frozenStderr = Join-Path $frozenSelftestRoot "stderr.txt"
    $hadHome = Test-Path Env:MIO_TRANSLATOR_HOME
    $previousHome = $env:MIO_TRANSLATOR_HOME
    try {
        $env:MIO_TRANSLATOR_HOME = Join-Path $frozenSelftestRoot "home"
        $frozenSelftest = Start-Process `
            -FilePath $frozenExe `
            -ArgumentList "--mio-selftest" `
            -RedirectStandardOutput $frozenStdout `
            -RedirectStandardError $frozenStderr `
            -WindowStyle Hidden `
            -Wait `
            -PassThru
        if ($frozenSelftest.ExitCode -ne 0) {
            $details = @(
                (Get-Content -LiteralPath $frozenStdout -Raw -ErrorAction SilentlyContinue),
                (Get-Content -LiteralPath $frozenStderr -Raw -ErrorAction SilentlyContinue)
            ) -join [Environment]::NewLine
            throw "Frozen runtime self-test failed with exit code $($frozenSelftest.ExitCode): $details"
        }
    } finally {
        if ($hadHome) { $env:MIO_TRANSLATOR_HOME = $previousHome } else { Remove-Item Env:MIO_TRANSLATOR_HOME -ErrorAction SilentlyContinue }
    }
} finally {
    if (Test-Path -LiteralPath $frozenSelftestRoot) {
        Remove-Item -LiteralPath $frozenSelftestRoot -Recurse -Force
    }
}

& $releasePython tools\verify_release_bundle.py
if ($LASTEXITCODE -ne 0) {
    throw "The frozen bundle failed payload verification."
}

$compilerCandidates = @(
    "${env:ProgramFiles(x86)}\Inno Setup 6\ISCC.exe",
    "$env:ProgramFiles\Inno Setup 6\ISCC.exe",
    "$env:LOCALAPPDATA\Programs\Inno Setup 6\ISCC.exe"
)
$iscc = $compilerCandidates | Where-Object { $_ -and (Test-Path $_) } | Select-Object -First 1
if (-not $iscc) {
    throw "Inno Setup 6 was not found."
}
Write-Host "Using Inno Setup compiler: $iscc"
& $iscc "/DAppVersion=$displayVersion" "/DAppNumericVersion=$numericVersion" "MioTranslator-installer.iss"
if ($LASTEXITCODE -ne 0) { exit $LASTEXITCODE }

$installerPath = Join-Path $repoRoot "dist\MioTranslator-Setup-$displayVersion.exe"
if (-not (Test-Path -LiteralPath $installerPath -PathType Leaf)) {
    throw "Compiled installer was not found: $installerPath"
}
$installerItem = Get-Item -LiteralPath $installerPath -Force
if ($installerItem.Length -le 0) {
    throw "Compiled installer is empty: $installerPath"
}

# A plain checksum for the testers; nothing here is signed.
$hash = (Get-FileHash -LiteralPath $installerPath -Algorithm SHA256).Hash.ToLowerInvariant()
$sumsPath = Join-Path $repoRoot "dist\MioTranslator-$displayVersion-SHA256SUMS.txt"
[IO.File]::WriteAllText($sumsPath, "$hash  $(Split-Path -Leaf $installerPath)`n", $utf8NoBom)

if (Test-Path -LiteralPath $stampRoot) {
    Remove-Item -LiteralPath $stampRoot -Recurse -Force
}

Write-Host ""
Write-Host "Internal test build finished:"
Write-Host "  $installerPath ($([math]::Round($installerItem.Length / 1MB)) MB)"
Write-Host "  $sumsPath"
Write-Host "  sha256 $hash"
Write-Host "Not signed, no update manifest, nothing published. Hand it to the testers directly."
