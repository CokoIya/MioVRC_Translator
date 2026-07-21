function Assert-MioRestrictedReleaseSecretAcl {
    param(
        [Parameter(Mandatory = $true)]
        [System.Security.AccessControl.FileSystemSecurity]$Acl,
        [Parameter(Mandatory = $true)]
        [string]$Label
    )

    if (-not $Acl.AreAccessRulesProtected) {
        throw "$Label must use protected, non-inherited access rules."
    }

    $allowedSids = @(
        [Security.Principal.WindowsIdentity]::GetCurrent().User.Value,
        'S-1-5-18',      # LocalSystem
        'S-1-5-32-544'   # Builtin Administrators
    )
    $sensitiveRights = (
        [Security.AccessControl.FileSystemRights]::FullControl -bor
        [Security.AccessControl.FileSystemRights]::Modify -bor
        [Security.AccessControl.FileSystemRights]::Read -bor
        [Security.AccessControl.FileSystemRights]::ReadAndExecute -bor
        [Security.AccessControl.FileSystemRights]::ReadData -bor
        [Security.AccessControl.FileSystemRights]::Write -bor
        [Security.AccessControl.FileSystemRights]::WriteData -bor
        [Security.AccessControl.FileSystemRights]::AppendData -bor
        [Security.AccessControl.FileSystemRights]::Delete -bor
        [Security.AccessControl.FileSystemRights]::DeleteSubdirectoriesAndFiles -bor
        [Security.AccessControl.FileSystemRights]::ChangePermissions -bor
        [Security.AccessControl.FileSystemRights]::TakeOwnership
    )
    $rules = $Acl.GetAccessRules(
        $true,
        $true,
        [Security.Principal.SecurityIdentifier]
    )
    foreach ($rule in $rules) {
        $grantsSensitiveAccess = (
            $rule.AccessControlType -eq [Security.AccessControl.AccessControlType]::Allow -and
            ($rule.FileSystemRights -band $sensitiveRights) -ne 0
        )
        if ($grantsSensitiveAccess -and $allowedSids -notcontains $rule.IdentityReference.Value) {
            throw "$Label grants access to an unauthorized principal."
        }
    }
}

function Read-MioReleaseSigningSeedFile {
    param(
        [Parameter(Mandatory = $true)]
        [string]$Path,
        [Parameter(Mandatory = $true)]
        [string]$RepositoryRoot
    )

    $rawPath = [string]$Path
    if ($rawPath -match '^(?:\\\\[?.]\\|//[?.]/)') {
        throw "Release signing seed file paths must not use device or extended namespaces."
    }

    $repoPath = [IO.Path]::GetFullPath($RepositoryRoot).TrimEnd(
        [IO.Path]::DirectorySeparatorChar,
        [IO.Path]::AltDirectorySeparatorChar
    )
    $seedPath = [IO.Path]::GetFullPath($rawPath)
    $repoPrefix = $repoPath + [IO.Path]::DirectorySeparatorChar
    if (
        $seedPath.Equals($repoPath, [StringComparison]::OrdinalIgnoreCase) -or
        $seedPath.StartsWith($repoPrefix, [StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "The release signing seed file must be stored outside the repository."
    }
    if (-not (Test-Path -LiteralPath $seedPath -PathType Leaf)) {
        throw "Release signing seed file not found: $seedPath"
    }

    $seedAncestor = Split-Path -Parent $seedPath
    while ($seedAncestor) {
        $seedAncestorItem = Get-Item -LiteralPath $seedAncestor -Force
        if (($seedAncestorItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release signing seed file parent directories must not contain reparse points."
        }
        $nextSeedAncestor = Split-Path -Parent $seedAncestor
        if (-not $nextSeedAncestor -or $nextSeedAncestor -eq $seedAncestor) {
            break
        }
        $seedAncestor = $nextSeedAncestor
    }

    $seedParentPath = Split-Path -Parent $seedPath
    Assert-MioRestrictedReleaseSecretAcl `
        -Acl (Get-Acl -LiteralPath $seedParentPath) `
        -Label "Release signing seed directory"

    $stream = New-Object IO.FileStream(
        $seedPath,
        [IO.FileMode]::Open,
        [IO.FileAccess]::Read,
        [IO.FileShare]::None,
        256,
        [IO.FileOptions]::SequentialScan
    )
    try {
        $seedItem = Get-Item -LiteralPath $seedPath -Force
        if (($seedItem.Attributes -band [IO.FileAttributes]::ReparsePoint) -ne 0) {
            throw "Release signing seed file must not be a link or reparse point."
        }
        if ($seedItem.Length -gt 256) {
            throw "Release signing seed file is unexpectedly large."
        }
        Assert-MioRestrictedReleaseSecretAcl `
            -Acl (Get-Acl -LiteralPath $seedPath) `
            -Label "Release signing seed file"

        $utf8 = New-Object Text.UTF8Encoding($false, $true)
        $reader = New-Object IO.StreamReader($stream, $utf8, $true, 256, $true)
        try {
            return $reader.ReadToEnd().Trim()
        } finally {
            $reader.Dispose()
        }
    } finally {
        $stream.Dispose()
    }
}
