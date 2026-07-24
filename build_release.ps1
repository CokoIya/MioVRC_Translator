param(
    [string]$ReleaseSigningSeedFile = ""
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSCommandPath
if ($repoRoot) {
    Set-Location $repoRoot
}
. (Join-Path $repoRoot "tools\release\release_signing_helpers.ps1")

$releasePython = Join-Path $repoRoot ".venv-release311\Scripts\python.exe"
if (-not (Test-Path -LiteralPath $releasePython -PathType Leaf)) {
    throw "The dedicated Python 3.11 release environment is missing. Run .\rebuild_release_environment.ps1 first."
}
Write-Host "Using release Python: $releasePython"

$releaseSeed = $env:MIO_RELEASE_SIGNING_SEED
if (-not $releaseSeed) {
    $releaseSeed = $env:MIO_MANIFEST_SEED
}
$configuredSeedFile = $ReleaseSigningSeedFile
if (-not $configuredSeedFile) {
    $configuredSeedFile = $env:MIO_RELEASE_SIGNING_SEED_FILE
}
if ($releaseSeed -and $configuredSeedFile) {
    throw "Configure either an in-memory release signing seed or a seed file, not both."
}
if (-not $releaseSeed -and $configuredSeedFile) {
    $releaseSeed = Read-MioReleaseSigningSeedFile `
        -Path $configuredSeedFile `
        -RepositoryRoot $repoRoot
}
if (-not $releaseSeed) {
    throw "Release builds require MIO_RELEASE_SIGNING_SEED or MIO_RELEASE_SIGNING_SEED_FILE for Ed25519 installer and manifest signatures."
}
if ($releaseSeed -notmatch '^[0-9a-fA-F]{64}$') {
    throw "Release signing seed must contain exactly 64 hexadecimal characters."
}
# Keep the signing seed out of PyInstaller, Inno Setup, and their child
# processes. It is restored only for the final trusted manifest-signing tool.
Remove-Item Env:MIO_RELEASE_SIGNING_SEED -ErrorAction SilentlyContinue
Remove-Item Env:MIO_MANIFEST_SEED -ErrorAction SilentlyContinue
Remove-Item Env:MIO_RELEASE_SIGNING_SEED_FILE -ErrorAction SilentlyContinue
$configuredSeedFile = $null

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
    throw "Inno Setup 6.5.0 or newer was not found. Please install a compatible Inno Setup release."
}
$isccVersion = $null
$isccVersionText = [Diagnostics.FileVersionInfo]::GetVersionInfo($iscc).FileVersion
$isccVersionMatch = [regex]::Match([string]$isccVersionText, '\d+\.\d+\.\d+(?:\.\d+)?')
if ($isccVersionMatch.Success) {
    $fileVersion = [Version]$isccVersionMatch.Value
    if ($fileVersion -ge [Version]'6.0.0') {
        $isccVersion = $fileVersion
    }
}
if ($null -eq $isccVersion) {
    $isccDirectory = [IO.Path]::GetFullPath((Split-Path -Parent $iscc)).TrimEnd('\')
    $uninstallRoots = @(
        'HKCU:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\Microsoft\Windows\CurrentVersion\Uninstall\*',
        'HKLM:\Software\WOW6432Node\Microsoft\Windows\CurrentVersion\Uninstall\*'
    )
    foreach ($uninstallRoot in $uninstallRoots) {
        $entries = Get-ItemProperty $uninstallRoot -ErrorAction SilentlyContinue
        foreach ($entry in $entries) {
            if (-not $entry.InstallLocation -or -not $entry.DisplayVersion) {
                continue
            }
            try {
                $entryDirectory = [IO.Path]::GetFullPath(
                    [string]$entry.InstallLocation
                ).TrimEnd('\')
                $entryVersion = [Version]([string]$entry.DisplayVersion)
            } catch {
                continue
            }
            if (
                $entryDirectory.Equals(
                    $isccDirectory,
                    [StringComparison]::OrdinalIgnoreCase
                )
            ) {
                $isccVersion = $entryVersion
                break
            }
        }
        if ($null -ne $isccVersion) {
            break
        }
    }
}
if ($null -eq $isccVersion) {
    # Some current per-user Inno Setup installations stamp ISCC.exe and the
    # compiler binaries with 0.0.0.0 and omit an uninstall-registry entry.
    # Their sibling uninstaller retains the real product version. Accept that
    # fallback only when it is a regular file in the exact validated compiler
    # directory, so an unrelated executable cannot satisfy the release gate.
    $innoUninstaller = Join-Path $isccDirectory 'unins000.exe'
    if (Test-Path -LiteralPath $innoUninstaller -PathType Leaf) {
        $uninstallerItem = Get-Item -LiteralPath $innoUninstaller -Force
        $uninstallerDirectory = [IO.Path]::GetFullPath(
            (Split-Path -Parent $uninstallerItem.FullName)
        ).TrimEnd('\')
        if (
            $uninstallerDirectory.Equals(
                $isccDirectory,
                [StringComparison]::OrdinalIgnoreCase
            ) -and
            ($uninstallerItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -eq 0
        ) {
            $uninstallerVersionText = [Diagnostics.FileVersionInfo]::GetVersionInfo(
                $uninstallerItem.FullName
            ).ProductVersion
            $uninstallerVersionMatch = [regex]::Match(
                [string]$uninstallerVersionText,
                '\d+\.\d+\.\d+(?:\.\d+)?'
            )
            if ($uninstallerVersionMatch.Success) {
                $candidateVersion = [Version]$uninstallerVersionMatch.Value
                if ($candidateVersion -ge [Version]'6.0.0') {
                    $isccVersion = $candidateVersion
                }
            }
        }
    }
}
if ($null -eq $isccVersion) {
    throw "Unable to determine the Inno Setup compiler version: $iscc"
}
if ($isccVersion -lt [Version]'6.5.0') {
    throw "Inno Setup 6.5.0 or newer is required; found $isccVersion at $iscc"
}
Write-Host "Using Inno Setup compiler: $iscc ($isccVersion)"

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

$releaseMetadataTargets = @(
    (Join-Path $repoRoot "mio_update.json"),
    (Join-Path $repoRoot "docs\installer_manifest.json")
)
$releaseMetadataBackupRoot = Join-Path $repoRoot (
    ".release-metadata-backup-" + [Guid]::NewGuid().ToString("N")
)
New-Item -ItemType Directory -Path $releaseMetadataBackupRoot | Out-Null
foreach ($metadataTarget in $releaseMetadataTargets) {
    $metadataItem = Get-Item -LiteralPath $metadataTarget -Force
    if (
        $metadataItem.PSIsContainer -or
        ($metadataItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
    ) {
        throw "Release metadata backup source is unsafe: $metadataTarget"
    }
    Copy-Item `
        -LiteralPath $metadataTarget `
        -Destination (Join-Path $releaseMetadataBackupRoot $metadataItem.Name)
}
$releaseMetadataFinalized = $false
try {
    $env:MIO_RELEASE_SIGNING_SEED = $releaseSeed
    & $releasePython tools\update_release_manifests.py $installerPath
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    & $releasePython tools\release\generate_release_checksums.py `
        $installerPath `
        (Join-Path $repoRoot "mio_update.json") `
        (Join-Path $repoRoot "docs\installer_manifest.json") `
        (Join-Path $repoRoot "docs\release_signing_keys.json")
    if ($LASTEXITCODE -ne 0) {
        exit $LASTEXITCODE
    }
    $releaseMetadataFinalized = $true
} finally {
    Remove-Item Env:MIO_RELEASE_SIGNING_SEED -ErrorAction SilentlyContinue
    $releaseSeed = $null
    if (-not $releaseMetadataFinalized) {
        foreach ($metadataTarget in $releaseMetadataTargets) {
            $metadataName = Split-Path -Leaf $metadataTarget
            Copy-Item `
                -LiteralPath (Join-Path $releaseMetadataBackupRoot $metadataName) `
                -Destination $metadataTarget `
                -Force
        }
    }
    if (Test-Path -LiteralPath $releaseMetadataBackupRoot) {
        $backupItem = Get-Item -LiteralPath $releaseMetadataBackupRoot -Force
        $backupParent = [IO.Path]::GetFullPath((Split-Path -Parent $backupItem.FullName))
        if (
            $backupParent -ne [IO.Path]::GetFullPath($repoRoot) -or
            ($backupItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0
        ) {
            throw "Refusing unsafe release metadata backup cleanup: $releaseMetadataBackupRoot"
        }
        Remove-Item -LiteralPath $releaseMetadataBackupRoot -Recurse -Force
    }
}

Write-Host "Release build, Ed25519 signing, and detached checksum generation finished."
