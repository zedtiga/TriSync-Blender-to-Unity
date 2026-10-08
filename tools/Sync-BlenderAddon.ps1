param(
    [string]$SourceRoot,
    [string]$RuntimeRoot,
    [switch]$SkipSync,
    [switch]$CleanPythonCache,
    [switch]$RemoveStaleFiles,
    [switch]$VerboseFiles
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    throw 'Pass -RuntimeRoot with the path to your Blender scripts\addons\blendersync_vnext directory.'
}

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

function Convert-ToRelativePath {
    param(
        [string]$Root,
        [string]$Path
    )

    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    if (!$pathFull.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path '$Path' is not under root '$Root'."
    }

    return $pathFull.Substring($rootFull.Length + 1)
}

function Test-ExcludedRelativePath {
    param([string]$RelativePath)

    return (
        $RelativePath -like '__pycache__\*' -or
        $RelativePath -like '*\__pycache__\*' -or
        $RelativePath -like '*.pyc' -or
        $RelativePath -like 'native\blendersync_native\target\*'
    )
}

function Get-SyncSourceFiles {
    param([string]$Root)

    Get-ChildItem -LiteralPath $Root -Recurse -File | Where-Object {
        $rel = Convert-ToRelativePath -Root $Root -Path $_.FullName
        -not (Test-ExcludedRelativePath -RelativePath $rel)
    }
}

function Sync-Files {
    param(
        [string]$Source,
        [string]$Destination,
        [object[]]$Files
    )

    $copied = 0
    foreach ($file in $Files) {
        $rel = Convert-ToRelativePath -Root $Source -Path $file.FullName
        $target = Join-Path $Destination $rel
        $targetDir = Split-Path -Parent $target
        if (!(Test-Path -LiteralPath $targetDir)) {
            New-Item -ItemType Directory -Path $targetDir | Out-Null
        }

        $shouldCopy = $true
        if (Test-Path -LiteralPath $target) {
            $srcHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash
            $dstHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash
            $shouldCopy = $srcHash -ne $dstHash
        }

        if ($shouldCopy) {
            Copy-Item -LiteralPath $file.FullName -Destination $target -Force
            $copied += 1
            if ($VerboseFiles) {
                Write-Host "copied $rel"
            }
        }
    }

    return $copied
}

function Compare-BlenderAddonMirror {
    param(
        [string]$Source,
        [string]$Destination,
        [object[]]$Files
    )

    $diff = New-Object System.Collections.Generic.List[string]
    foreach ($file in $Files) {
        $rel = Convert-ToRelativePath -Root $Source -Path $file.FullName
        $target = Join-Path $Destination $rel
        if (!(Test-Path -LiteralPath $target)) {
            $diff.Add("missing_runtime $rel")
            continue
        }

        $srcHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash
        $dstHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash
        if ($srcHash -ne $dstHash) {
            $diff.Add("different $rel")
        }
    }

    return $diff.ToArray()
}

function Get-StaleRuntimeFiles {
    param(
        [string]$Source,
        [string]$Destination,
        [object[]]$Files
    )

    $sourceRelativePaths = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($file in $Files) {
        $rel = Convert-ToRelativePath -Root $Source -Path $file.FullName
        [void]$sourceRelativePaths.Add($rel)
    }

    $stale = New-Object System.Collections.Generic.List[object]
    Get-ChildItem -LiteralPath $Destination -Recurse -File -Force | ForEach-Object {
        $rel = Convert-ToRelativePath -Root $Destination -Path $_.FullName
        if (Test-ExcludedRelativePath -RelativePath $rel) {
            return
        }
        if (!$sourceRelativePaths.Contains($rel)) {
            $stale.Add([pscustomobject]@{
                RelativePath = $rel
                FullName = $_.FullName
            })
        }
    }

    return $stale.ToArray()
}

function Remove-StaleRuntimeFiles {
    param(
        [string]$Root,
        [object[]]$Files
    )

    $removed = 0
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    foreach ($file in $Files) {
        $filePath = [System.IO.Path]::GetFullPath($file.FullName)
        if (!$filePath.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove stale file outside runtime root: $filePath"
        }
        Remove-Item -LiteralPath $filePath -Force
        $removed += 1
        if ($VerboseFiles) {
            Write-Host "removed_stale $($file.RelativePath)"
        }
    }
    return $removed
}

function Remove-PythonCaches {
    param([string]$Root)

    $removed = 0
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    Get-ChildItem -LiteralPath $Root -Recurse -Directory -Force -Filter '__pycache__' | ForEach-Object {
        $cachePath = [System.IO.Path]::GetFullPath($_.FullName)
        if (!$cachePath.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove cache outside runtime root: $cachePath"
        }
        Remove-Item -LiteralPath $cachePath -Recurse -Force
        $removed += 1
        if ($VerboseFiles) {
            Write-Host "removed_cache $(Convert-ToRelativePath -Root $Root -Path $cachePath)"
        }
    }
    return $removed
}

$repoRoot = Get-RepoRoot
if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $SourceRoot = Join-Path $repoRoot 'blender_addon\blendersync_vnext'
}

$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
if (!(Test-Path -LiteralPath $RuntimeRoot)) {
    New-Item -ItemType Directory -Path $RuntimeRoot | Out-Null
}
$RuntimeRoot = (Resolve-Path -LiteralPath $RuntimeRoot).Path

Write-Host "source=$SourceRoot"
Write-Host "runtime=$RuntimeRoot"

$sourceFiles = @(Get-SyncSourceFiles -Root $SourceRoot)
Write-Host "source_files=$($sourceFiles.Count)"

if (!$SkipSync) {
    $copied = Sync-Files -Source $SourceRoot -Destination $RuntimeRoot -Files $sourceFiles
    Write-Host "copied=$copied"
}

if ($CleanPythonCache) {
    $removedCaches = Remove-PythonCaches -Root $RuntimeRoot
    Write-Host "removed_python_cache_dirs=$removedCaches"
}

$staleFiles = @(Get-StaleRuntimeFiles -Source $SourceRoot -Destination $RuntimeRoot -Files $sourceFiles)
Write-Host "stale_files=$($staleFiles.Count)"
if ($staleFiles.Count -gt 0) {
    if ($RemoveStaleFiles) {
        $removedStale = Remove-StaleRuntimeFiles -Root $RuntimeRoot -Files $staleFiles
        Write-Host "removed_stale_files=$removedStale"
        $staleFiles = @(Get-StaleRuntimeFiles -Source $SourceRoot -Destination $RuntimeRoot -Files $sourceFiles)
        Write-Host "stale_files_after_remove=$($staleFiles.Count)"
    }
    if ($staleFiles.Count -gt 0) {
        $staleFiles | Select-Object -First 50 | ForEach-Object { Write-Host "stale_runtime $($_.RelativePath)" }
        throw "Blender addon runtime has stale files. Re-run with -RemoveStaleFiles to delete them."
    }
}

$diff = @(Compare-BlenderAddonMirror -Source $SourceRoot -Destination $RuntimeRoot -Files $sourceFiles)
Write-Host "mirror_diffs=$($diff.Count)"
if ($diff.Count -gt 0) {
    $diff | Select-Object -First 50 | ForEach-Object { Write-Host $_ }
    throw "Blender addon runtime mirror differs from source."
}

Write-Host "blender_addon_sync_ok"
