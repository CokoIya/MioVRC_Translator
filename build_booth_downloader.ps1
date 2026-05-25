$ErrorActionPreference = "Stop"

function Find-SignTool {
    $command = Get-Command signtool.exe -ErrorAction SilentlyContinue
    if ($command -and $command.Source) {
        return $command.Source
    }

    $roots = @(
        "$env:ProgramFiles(x86)\Windows Kits\10\bin",
        "$env:ProgramFiles\Windows Kits\10\bin"
    )

    foreach ($root in $roots) {
        if (-not (Test-Path $root)) {
            continue
        }
        $candidate = Get-ChildItem $root -Directory |
            Sort-Object Name -Descending |
            ForEach-Object { Join-Path $_.FullName "x64\signtool.exe" } |
            Where-Object { Test-Path $_ } |
            Select-Object -First 1
        if ($candidate) {
            return $candidate
        }
    }

    return $null
}

function Sign-Binary {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$SignTool,
        [Parameter(Mandatory = $true)]
        [string]$SignPfx,
        [Parameter(Mandatory = $true)]
        [string]$SignPass
    )

    if (-not (Test-Path $Path)) {
        throw "Binary not found for signing: $Path"
    }

    & $SignTool sign /fd sha256 /tr http://timestamp.digicert.com /td sha256 /f $SignPfx /p $SignPass $Path
    if ($LASTEXITCODE -ne 0) {
        throw "Code signing failed for: $Path"
    }
}

function Compress-WithRetry {
    param(
        [Parameter(Mandatory = $true)]
        [string]$SourcePath,
        [Parameter(Mandatory = $true)]
        [string]$DestinationPath
    )

    $maxAttempts = 8
    for ($attempt = 1; $attempt -le $maxAttempts; $attempt++) {
        try {
            Compress-Archive -Path $SourcePath -DestinationPath $DestinationPath -Force -ErrorAction Stop
            return
        } catch {
            if ($attempt -ge $maxAttempts) {
                throw
            }
            Start-Sleep -Milliseconds 1500
        }
    }
}

python -m PyInstaller --clean --noconfirm Mio_vrc_download.spec
python -m PyInstaller --clean --noconfirm Mio_vrc_download_bundle.spec

$output = Join-Path $PSScriptRoot "dist\Mio_vrc_download.exe"
$bundleDir = Join-Path $PSScriptRoot "dist\Mio_vrc_download_bundle"
$bundleExe = Join-Path $bundleDir "Mio_vrc_download.exe"
$bundleZip = Join-Path $PSScriptRoot "dist\Mio_vrc_download_bundle.zip"
if (-not (Test-Path $output)) {
    throw "Downloader output not found: $output"
}
if (-not (Test-Path $bundleExe)) {
    throw "Bundled downloader output not found: $bundleExe"
}

$signPfx = $env:MIO_VRC_SIGN_PFX
$signPass = $env:MIO_VRC_SIGN_PASS
if ($signPfx -and $signPass) {
    $signTool = Find-SignTool
    if (-not $signTool) {
        throw "signtool.exe was not found. Install Windows SDK or add signtool.exe to PATH."
    }
    if (-not (Test-Path $signPfx)) {
        throw "Code signing certificate not found: $signPfx"
    }

    Sign-Binary -Path $output -SignTool $signTool -SignPfx $signPfx -SignPass $signPass
    Sign-Binary -Path $bundleExe -SignTool $signTool -SignPfx $signPfx -SignPass $signPass
    Write-Host "Signed downloader binaries."
} else {
    Write-Host "Skipping code signing. Set MIO_VRC_SIGN_PFX and MIO_VRC_SIGN_PASS to sign the downloader."
}

if (Test-Path $bundleZip) {
    Remove-Item -LiteralPath $bundleZip -Force
}
Compress-WithRetry -SourcePath $bundleDir -DestinationPath $bundleZip

Write-Host "BOOTH downloader finished: dist\\Mio_vrc_download.exe"
Write-Host "Safer downloader bundle finished: dist\\Mio_vrc_download_bundle.zip"
