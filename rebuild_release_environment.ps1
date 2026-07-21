param(
    [string]$BasePython = "C:\Program Files\Python311\python.exe"
)

$ErrorActionPreference = "Stop"

$repoRoot = Split-Path -Parent $PSCommandPath
if (-not $repoRoot) {
    throw "Unable to resolve the repository root."
}
$repoRoot = [IO.Path]::GetFullPath($repoRoot)
Set-Location $repoRoot

function Assert-SafeReleaseEnvironmentPath {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$AllowedNamePattern
    )

    $fullPath = [IO.Path]::GetFullPath($Path)
    $parentPath = [IO.Path]::GetFullPath((Split-Path -Parent $fullPath))
    $leafName = Split-Path -Leaf $fullPath
    if ($parentPath -ne $repoRoot -or $leafName -notmatch $AllowedNamePattern) {
        throw "Refusing unsafe release-environment path: $fullPath"
    }
    if (Test-Path -LiteralPath $fullPath) {
        $item = Get-Item -LiteralPath $fullPath -Force
        if (($item.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release-environment path must not be a reparse point: $fullPath"
        }
        if (-not $item.PSIsContainer) {
            throw "Release-environment path is not a directory: $fullPath"
        }
    }
    return $fullPath
}

function Remove-SafeReleaseEnvironmentDirectory {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$AllowedNamePattern
    )

    $fullPath = Assert-SafeReleaseEnvironmentPath `
        -Path $Path `
        -AllowedNamePattern $AllowedNamePattern
    if (-not (Test-Path -LiteralPath $fullPath)) {
        return
    }
    try {
        Remove-Item -LiteralPath $fullPath -Recurse -Force -ErrorAction Stop
    } catch {
        # Windows PowerShell 5 can fail partway through deeply nested package
        # trees even when long paths are enabled. Retry the verified directory
        # through the Win32 extended-length namespace.
        $extendedPath = if ($fullPath.StartsWith('\\')) {
            '\\?\UNC\' + $fullPath.Substring(2)
        } else {
            '\\?\' + $fullPath
        }
        [IO.Directory]::Delete($extendedPath, $true)
    }
    if (Test-Path -LiteralPath $fullPath) {
        throw "Failed to remove release-environment directory: $fullPath"
    }
}

if (-not (Test-Path -LiteralPath $BasePython -PathType Leaf)) {
    throw "Python 3.11 was not found at: $BasePython"
}
$pythonVersion = & $BasePython -c "import sys; print('.'.join(map(str, sys.version_info[:2])))"
if ($LASTEXITCODE -ne 0 -or $pythonVersion.Trim() -ne "3.11") {
    throw "Release builds require CPython 3.11; $BasePython reported $pythonVersion."
}

$releaseEnv = Assert-SafeReleaseEnvironmentPath `
    -Path (Join-Path $repoRoot ".venv-release311") `
    -AllowedNamePattern '^\.venv-release311$'
$backupEnv = Assert-SafeReleaseEnvironmentPath `
    -Path (Join-Path $repoRoot (".venv-release311.backup-" + [Guid]::NewGuid().ToString("N"))) `
    -AllowedNamePattern '^\.venv-release311\.backup-[0-9a-f]{32}$'
$selftestHome = Join-Path $repoRoot (".release-env-selftest-" + [Guid]::NewGuid().ToString("N"))
$movedExisting = $false
$rebuildSucceeded = $false
$hadTranslatorHome = Test-Path Env:MIO_TRANSLATOR_HOME
$previousTranslatorHome = $env:MIO_TRANSLATOR_HOME
$hadNoVenvRelaunch = Test-Path Env:MIO_TRANSLATOR_NO_VENV_RELAUNCH
$previousNoVenvRelaunch = $env:MIO_TRANSLATOR_NO_VENV_RELAUNCH

try {
    if (Test-Path -LiteralPath $releaseEnv) {
        Move-Item -LiteralPath $releaseEnv -Destination $backupEnv
        $movedExisting = $true
    }

    & $BasePython -m venv $releaseEnv
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to create the Python 3.11 release environment."
    }

    $releasePython = Join-Path $releaseEnv "Scripts\python.exe"
    & $releasePython -m pip --isolated install `
        --disable-pip-version-check `
        --no-cache-dir `
        --index-url https://pypi.org/simple `
        --upgrade `
        "pip==26.1.2" `
        "setuptools==81.0.0" `
        "wheel==0.47.0"
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install the pinned packaging toolchain."
    }

    & $releasePython -m pip --isolated install `
        --disable-pip-version-check `
        --no-cache-dir `
        --index-url https://pypi.org/simple `
        --requirement (Join-Path $repoRoot "requirements.lock.txt")
    if ($LASTEXITCODE -ne 0) {
        throw "Failed to install the locked release dependencies."
    }

    & $releasePython -m pip check
    if ($LASTEXITCODE -ne 0) {
        throw "The rebuilt release environment has broken dependencies."
    }

    & $releasePython tools\check_release_environment.py
    if ($LASTEXITCODE -ne 0) {
        throw "The rebuilt release environment failed release validation."
    }

    $env:MIO_TRANSLATOR_HOME = $selftestHome
    $env:MIO_TRANSLATOR_NO_VENV_RELAUNCH = "1"
    & $releasePython main.py --mio-selftest
    if ($LASTEXITCODE -ne 0) {
        throw "The rebuilt release environment failed the ASR/XTTS runtime self-test."
    }

    $rebuildSucceeded = $true
} finally {
    if ($hadTranslatorHome) {
        $env:MIO_TRANSLATOR_HOME = $previousTranslatorHome
    } else {
        Remove-Item Env:MIO_TRANSLATOR_HOME -ErrorAction SilentlyContinue
    }
    if ($hadNoVenvRelaunch) {
        $env:MIO_TRANSLATOR_NO_VENV_RELAUNCH = $previousNoVenvRelaunch
    } else {
        Remove-Item Env:MIO_TRANSLATOR_NO_VENV_RELAUNCH -ErrorAction SilentlyContinue
    }
    if (Test-Path -LiteralPath $selftestHome) {
        Remove-Item -LiteralPath $selftestHome -Recurse -Force
    }

    if ($rebuildSucceeded) {
        if ($movedExisting -and (Test-Path -LiteralPath $backupEnv)) {
            Remove-SafeReleaseEnvironmentDirectory `
                -Path $backupEnv `
                -AllowedNamePattern '^\.venv-release311\.backup-[0-9a-f]{32}$'
        }
    } else {
        if (Test-Path -LiteralPath $releaseEnv) {
            Remove-SafeReleaseEnvironmentDirectory `
                -Path $releaseEnv `
                -AllowedNamePattern '^\.venv-release311$'
        }
        if ($movedExisting -and (Test-Path -LiteralPath $backupEnv)) {
            Move-Item -LiteralPath $backupEnv -Destination $releaseEnv
        }
    }
}

Write-Host "Release environment rebuilt and validated: $releaseEnv"
