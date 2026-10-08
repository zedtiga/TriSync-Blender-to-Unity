[CmdletBinding()]
param(
    [string]$OutputDirectory,
    [string]$SourceCommit = 'HEAD'
)

$ErrorActionPreference = 'Stop'

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

function Get-RelativePath {
    param(
        [string]$Root,
        [string]$Path
    )

    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    $prefix = $rootFull + [System.IO.Path]::DirectorySeparatorChar
    if (!$pathFull.StartsWith($prefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Path '$Path' is not under '$Root'."
    }
    return $pathFull.Substring($prefix.Length)
}

function Write-Utf8WithoutBom {
    param(
        [string]$Path,
        [string]$Content
    )

    [System.IO.File]::WriteAllText(
        $Path,
        $Content,
        [System.Text.UTF8Encoding]::new($false)
    )
}

function New-DeterministicZip {
    param(
        [string]$SourceRoot,
        [string]$DestinationPath
    )

    Add-Type -AssemblyName System.IO.Compression
    Add-Type -AssemblyName System.IO.Compression.FileSystem

    if (Test-Path -LiteralPath $DestinationPath) {
        Remove-Item -LiteralPath $DestinationPath -Force
    }
    $destinationParent = Split-Path -Parent $DestinationPath
    if (!(Test-Path -LiteralPath $destinationParent -PathType Container)) {
        New-Item -ItemType Directory -Path $destinationParent -Force | Out-Null
    }

    $entries = @(Get-ChildItem -LiteralPath $SourceRoot -Recurse -File -Force | ForEach-Object {
        [pscustomobject]@{
            File = $_
            Relative = (Get-RelativePath -Root $SourceRoot -Path $_.FullName).Replace('\', '/')
        }
    } | Sort-Object Relative)
    if ($entries.Count -eq 0) {
        throw "Refusing to create an empty archive from '$SourceRoot'."
    }

    $fileStream = [System.IO.File]::Open(
        $DestinationPath,
        [System.IO.FileMode]::CreateNew,
        [System.IO.FileAccess]::ReadWrite,
        [System.IO.FileShare]::None
    )
    $archive = $null
    try {
        $archive = New-Object System.IO.Compression.ZipArchive(
            $fileStream,
            [System.IO.Compression.ZipArchiveMode]::Create,
            $false
        )
        $fixedTimestamp = [System.DateTimeOffset]::new(2000, 1, 1, 0, 0, 0, [System.TimeSpan]::Zero)
        foreach ($item in $entries) {
            $entry = $archive.CreateEntry(
                $item.Relative,
                [System.IO.Compression.CompressionLevel]::Optimal
            )
            $entry.LastWriteTime = $fixedTimestamp
            $input = [System.IO.File]::OpenRead($item.File.FullName)
            $output = $entry.Open()
            try {
                $input.CopyTo($output)
            }
            finally {
                $output.Dispose()
                $input.Dispose()
            }
        }
    }
    finally {
        if ($null -ne $archive) {
            $archive.Dispose()
        }
        $fileStream.Dispose()
    }
}

function Get-FileManifest {
    param([string]$Root)

    return @(Get-ChildItem -LiteralPath $Root -Recurse -File -Force | ForEach-Object {
        $relative = (Get-RelativePath -Root $Root -Path $_.FullName).Replace('\', '/')
        [ordered]@{
            path = $relative
            bytes = [long]$_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    } | Sort-Object { $_.path })
}

function Get-ForbiddenCompiledFiles {
    param([string]$Root)

    $extensions = @(
        '.pyd', '.so', '.dylib', '.dll', '.exe', '.pyc',
        '.bin', '.obj', '.lib', '.a', '.o'
    )
    return @(Get-ChildItem -LiteralPath $Root -Recurse -File -Force | Where-Object {
        $extensions -contains $_.Extension.ToLowerInvariant()
    })
}

$repoRoot = Get-RepoRoot
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $repoRoot 'tmp\superhive'
}
elseif (![System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $repoRoot $OutputDirectory
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
if (!(Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

$resolvedCommit = ([string](& git -C $repoRoot rev-parse --verify "$SourceCommit`^{commit}")).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($resolvedCommit)) {
    throw "Could not resolve source commit '$SourceCommit'."
}

$workRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('bssuper-' + [System.Guid]::NewGuid().ToString('N').Substring(0, 12))
$archiveRoot = Join-Path $workRoot 'archive'
$archivePath = Join-Path $workRoot 'source.tar'
$packageRoot = Join-Path $workRoot 'package'
$addonRoot = Join-Path $packageRoot 'blendersync_vnext'

try {
    New-Item -ItemType Directory -Path $archiveRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $packageRoot -Force | Out-Null

    & git -C $repoRoot archive --format=tar --output=$archivePath $resolvedCommit -- VERSION blender_addon/blendersync_vnext
    if ($LASTEXITCODE -ne 0) {
        throw "Could not archive source commit '$resolvedCommit'."
    }
    & tar -xf $archivePath -C $archiveRoot
    if ($LASTEXITCODE -ne 0) {
        throw 'Could not extract the archived source tree.'
    }

    $versionPath = Join-Path $archiveRoot 'VERSION'
    $archivedAddonRoot = Join-Path $archiveRoot 'blender_addon\blendersync_vnext'
    if (!(Test-Path -LiteralPath $versionPath -PathType Leaf)) {
        throw 'Archived VERSION file is missing.'
    }
    if (!(Test-Path -LiteralPath $archivedAddonRoot -PathType Container)) {
        throw 'Archived Blender add-on source is missing.'
    }

    $productVersion = (Get-Content -LiteralPath $versionPath -Raw).Trim()
    if ([string]::IsNullOrWhiteSpace($productVersion)) {
        throw 'Archived product version is empty.'
    }

    Copy-Item -LiteralPath $archivedAddonRoot -Destination $packageRoot -Recurse
    $legacyArtifactRoot = Join-Path $addonRoot 'native\artifacts'
    if (Test-Path -LiteralPath $legacyArtifactRoot) {
        Remove-Item -LiteralPath $legacyArtifactRoot -Recurse -Force
    }
    $nativeSourceRoot = Join-Path $addonRoot 'native\blendersync_native'
    if (Test-Path -LiteralPath $nativeSourceRoot) {
        Remove-Item -LiteralPath $nativeSourceRoot -Recurse -Force
    }

    $forbidden = @(Get-ForbiddenCompiledFiles -Root $packageRoot)
    if ($forbidden.Count -gt 0) {
        throw "Source-only staging contains compiled or generated files: $($forbidden.FullName -join ', ')"
    }
    $excludedDirectories = @(Get-ChildItem -LiteralPath $packageRoot -Recurse -Directory -Force | Where-Object {
        $_.Name -in @('target', '__pycache__') -or
        $_.FullName -like '*\native\artifacts*' -or
        $_.FullName -like '*\native\blendersync_native*'
    })
    if ($excludedDirectories.Count -gt 0) {
        throw "Source-only staging contains excluded directories: $($excludedDirectories.FullName -join ', ')"
    }

    $manifestFiles = @(Get-FileManifest -Root $packageRoot)
    $bundleName = "TriSync-Blender-$productVersion-source-only"
    $zipPath = Join-Path $OutputDirectory "$bundleName.zip"
    $manifestPath = Join-Path $OutputDirectory "$bundleName-manifest.json"
    $checksumsPath = Join-Path $OutputDirectory "$bundleName-SHA256SUMS.txt"

    New-DeterministicZip -SourceRoot $packageRoot -DestinationPath $zipPath

    $manifest = [ordered]@{
        schemaVersion = 1
        distributionVariant = 'superhive-source-only'
        productVersion = $productVersion
        sourceCommit = $resolvedCommit
        nativeArtifactsIncluded = $false
        nativeSourceIncluded = $false
        nativeFallback = 'python'
        forbiddenCompiledFileCount = 0
        files = $manifestFiles
    }
    $manifestJson = $manifest | ConvertTo-Json -Depth 8
    Write-Utf8WithoutBom -Path $manifestPath -Content ($manifestJson + "`n")

    $checksumTargets = @($zipPath, $manifestPath) | Sort-Object { Split-Path -Leaf $_ }
    $checksumLines = @($checksumTargets | ForEach-Object {
        $hash = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $(Split-Path -Leaf $_)"
    })
    Write-Utf8WithoutBom -Path $checksumsPath -Content (($checksumLines -join "`n") + "`n")

    Write-Host "superhive_package_ok version=$productVersion sourceCommit=$resolvedCommit output=$OutputDirectory files=$($manifestFiles.Count) compiledFiles=0"
    Write-Host "superhive_artifact path=$zipPath"
    Write-Host "superhive_manifest path=$manifestPath"
    Write-Host "superhive_checksums path=$checksumsPath"
}
finally {
    $tempPrefix = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $workFull = [System.IO.Path]::GetFullPath($workRoot)
    if (!$workFull.StartsWith($tempPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean staging path outside the system temp directory: $workFull"
    }
    if (Test-Path -LiteralPath $workFull) {
        Remove-Item -LiteralPath $workFull -Recurse -Force
    }
}
