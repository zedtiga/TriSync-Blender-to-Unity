[CmdletBinding()]
param(
    [string]$OutputDirectory,
    [switch]$AllowDirty,
    [switch]$RequireReleaseReady
)

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'NativeArtifactTargets.ps1')

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

function Copy-TrackedTree {
    param(
        [string]$RepoRoot,
        [string]$SourcePrefix,
        [string]$DestinationRoot,
        [switch]$IncludeUntracked
    )

    $normalizedPrefix = $SourcePrefix.Replace('\', '/').TrimEnd('/')
    $gitArgs = @('-C', $RepoRoot, 'ls-files', '--cached')
    if ($IncludeUntracked) {
        $gitArgs += @('--others', '--exclude-standard')
    }
    $gitArgs += @('--', "$normalizedPrefix/")
    $tracked = @(& git @gitArgs)
    if ($LASTEXITCODE -ne 0) {
        throw "Could not enumerate tracked files under '$normalizedPrefix'."
    }
    if ($tracked.Count -eq 0) {
        throw "No tracked files found under '$normalizedPrefix'."
    }

    foreach ($trackedPath in $tracked) {
        $normalizedPath = ([string]$trackedPath).Replace('\', '/')
        $expectedPrefix = $normalizedPrefix + '/'
        if (!$normalizedPath.StartsWith($expectedPrefix, [System.StringComparison]::Ordinal)) {
            throw "Tracked path '$normalizedPath' escaped '$normalizedPrefix'."
        }
        $relative = $normalizedPath.Substring($expectedPrefix.Length).Replace('/', '\')
        $source = Join-Path $RepoRoot $normalizedPath.Replace('/', '\')
        if (!(Test-Path -LiteralPath $source -PathType Leaf)) {
            if ($IncludeUntracked) {
                continue
            }
            throw "Tracked release source is missing: $source"
        }
        $destination = Join-Path $DestinationRoot $relative
        $parent = Split-Path -Parent $destination
        if (!(Test-Path -LiteralPath $parent -PathType Container)) {
            New-Item -ItemType Directory -Path $parent -Force | Out-Null
        }
        Copy-Item -LiteralPath $source -Destination $destination -Force
    }
}

function Copy-ReleaseFile {
    param(
        [string]$RepoRoot,
        [string]$RelativePath,
        [string]$DestinationRoot
    )

    $source = Join-Path $RepoRoot $RelativePath
    if (!(Test-Path -LiteralPath $source -PathType Leaf)) {
        throw "Release source is missing: $source"
    }
    $destination = Join-Path $DestinationRoot $RelativePath
    $parent = Split-Path -Parent $destination
    if (!(Test-Path -LiteralPath $parent -PathType Container)) {
        New-Item -ItemType Directory -Path $parent -Force | Out-Null
    }
    Copy-Item -LiteralPath $source -Destination $destination -Force
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

    $entries = @(Get-ChildItem -LiteralPath $SourceRoot -Recurse -File | ForEach-Object {
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

function Get-ReleaseFileManifest {
    param([string]$Root)

    return @(Get-ChildItem -LiteralPath $Root -Recurse -File | ForEach-Object {
        $relative = (Get-RelativePath -Root $Root -Path $_.FullName).Replace('\', '/')
        [ordered]@{
            path = $relative
            bytes = [long]$_.Length
            sha256 = (Get-FileHash -LiteralPath $_.FullName -Algorithm SHA256).Hash.ToLowerInvariant()
        }
    } | Sort-Object { $_.path })
}

function Get-NativeArtifactManifest {
    param([string]$AddonRoot)

    return @(Get-BlenderSyncNativeArtifactTargets | ForEach-Object {
        $relativePath = "native/artifacts/$($_.Platform)/$($_.PythonAbi)/$($_.ModuleFile)"
        $archivePath = "blendersync_vnext/$relativePath"
        $sourcePath = Join-Path $AddonRoot $relativePath.Replace('/', '\')
        $available = Test-Path -LiteralPath $sourcePath -PathType Leaf
        if (!$available) {
            Write-Warning "native_artifact_missing platform=$($_.Platform) expected=$archivePath"
        }

        [pscustomobject][ordered]@{
            platform = [string]$_.Platform
            pythonAbi = [string]$_.PythonAbi
            archivePath = $archivePath
            available = [bool]$available
            bytes = if ($available) { [long](Get-Item -LiteralPath $sourcePath).Length } else { [long]0 }
            sha256 = if ($available) { (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant() } else { '' }
        }
    })
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

$repoRoot = Get-RepoRoot
if ([string]::IsNullOrWhiteSpace($OutputDirectory)) {
    $OutputDirectory = Join-Path $repoRoot 'tmp\release'
}
elseif (![System.IO.Path]::IsPathRooted($OutputDirectory)) {
    $OutputDirectory = Join-Path $repoRoot $OutputDirectory
}
$OutputDirectory = [System.IO.Path]::GetFullPath($OutputDirectory)
if (!(Test-Path -LiteralPath $OutputDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $OutputDirectory -Force | Out-Null
}

$metadataRaw = & uv run python (Join-Path $repoRoot 'tools\release_metadata.py') --repo-root $repoRoot
if ($LASTEXITCODE -ne 0) {
    throw "Release metadata validation failed with exit code $LASTEXITCODE."
}
$metadata = (($metadataRaw -join "`n") | ConvertFrom-Json)
$productVersion = [string]$metadata.productVersion

$dirtyLines = @(& git -C $repoRoot status --porcelain --untracked-files=all)
if ($LASTEXITCODE -ne 0) {
    throw 'Could not inspect the Git worktree.'
}
$sourceDirty = $dirtyLines.Count -gt 0
if ($sourceDirty -and !$AllowDirty) {
    throw 'Release packaging requires a clean Git worktree. Use -AllowDirty only for local smoke packages.'
}
$sourceCommit = ([string](& git -C $repoRoot rev-parse HEAD)).Trim()
if ($LASTEXITCODE -ne 0 -or [string]::IsNullOrWhiteSpace($sourceCommit)) {
    throw 'Could not resolve the source commit.'
}

$licenseArgs = @(
    '-NoProfile',
    '-ExecutionPolicy', 'Bypass',
    '-File', (Join-Path $repoRoot 'tools\Test-LicenseBundle.ps1')
)
$stableVersion = !$productVersion.Contains('-')
if ($stableVersion -or $RequireReleaseReady) {
    $licenseArgs += '-RequireReleaseReady'
}
& powershell @licenseArgs
if ($LASTEXITCODE -ne 0) {
    throw "License validation failed with exit code $LASTEXITCODE."
}
$licenseGate = if ($stableVersion -or $RequireReleaseReady) { 'release-ready' } else { 'preview-structural' }

$workRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('bsrel-' + [System.Guid]::NewGuid().ToString('N').Substring(0, 12))
$bundleParent = Join-Path $workRoot 'bundle'
$bundleName = "TriSync-$productVersion"
$bundleRoot = Join-Path $bundleParent $bundleName
$addonParent = Join-Path $workRoot 'addon'
$addonRoot = Join-Path $addonParent 'blendersync_vnext'
$blenderDirectory = Join-Path $bundleRoot 'blender'
$unityDirectory = Join-Path $bundleRoot 'unity\TriSync'

$blenderArchiveName = "TriSync-Blender-$productVersion.zip"
$unityArchiveName = "TriSync-Unity-$productVersion.zip"
$bundleArchiveName = "$bundleName.zip"
$manifestName = "$bundleName-manifest.json"
$checksumName = "$bundleName-SHA256SUMS.txt"

try {
    New-Item -ItemType Directory -Path $addonRoot -Force | Out-Null
    New-Item -ItemType Directory -Path $blenderDirectory -Force | Out-Null
    New-Item -ItemType Directory -Path $unityDirectory -Force | Out-Null

    Copy-TrackedTree -RepoRoot $repoRoot -SourcePrefix 'blender_addon/blendersync_vnext' -DestinationRoot $addonRoot -IncludeUntracked:$AllowDirty
    Copy-TrackedTree -RepoRoot $repoRoot -SourcePrefix 'unity/TriSync' -DestinationRoot $unityDirectory -IncludeUntracked:$AllowDirty
    Copy-TrackedTree -RepoRoot $repoRoot -SourcePrefix 'docs' -DestinationRoot (Join-Path $bundleRoot 'docs') -IncludeUntracked:$AllowDirty
    foreach ($packageRoot in @($bundleRoot, $addonRoot, $unityDirectory)) {
        Copy-TrackedTree -RepoRoot $repoRoot -SourcePrefix 'LICENSES' -DestinationRoot (Join-Path $packageRoot 'LICENSES') -IncludeUntracked:$AllowDirty
        foreach ($relativePath in @('LICENSE.md', 'THIRD_PARTY_NOTICES.md')) {
            Copy-ReleaseFile -RepoRoot $repoRoot -RelativePath $relativePath -DestinationRoot $packageRoot
        }
    }
    foreach ($relativePath in @('VERSION', 'README.md', 'CONTRIBUTING.md')) {
        Copy-ReleaseFile -RepoRoot $repoRoot -RelativePath $relativePath -DestinationRoot $bundleRoot
    }
    $nativeArtifacts = @(Get-NativeArtifactManifest -AddonRoot $addonRoot)

    $innerBlenderArchive = Join-Path $blenderDirectory $blenderArchiveName
    New-DeterministicZip -SourceRoot $addonParent -DestinationPath $innerBlenderArchive

    $externalBlenderArchive = Join-Path $OutputDirectory $blenderArchiveName
    Copy-Item -LiteralPath $innerBlenderArchive -Destination $externalBlenderArchive -Force

    $externalUnityArchive = Join-Path $OutputDirectory $unityArchiveName
    New-DeterministicZip -SourceRoot (Join-Path $bundleRoot 'unity') -DestinationPath $externalUnityArchive

    $manifest = [ordered]@{
        schemaVersion = 1
        productVersion = $productVersion
        versions = [ordered]@{
            blenderAddon = [string]$metadata.blenderAddonVersion
            native = [string]$metadata.nativeVersion
            nativeCapabilities = @($metadata.nativeCapabilities)
            sessionProtocol = [int]$metadata.sessionProtocolVersion
            assetBridgeContract = [string]$metadata.assetBridgeContractVersion
        }
        source = [ordered]@{
            commit = $sourceCommit
            dirty = $sourceDirty
        }
        licenseGate = $licenseGate
        nativeArtifacts = @($nativeArtifacts)
        files = @(Get-ReleaseFileManifest -Root $bundleRoot)
    }
    $manifestJson = $manifest | ConvertTo-Json -Depth 8
    $innerManifestPath = Join-Path $bundleRoot 'manifest.json'
    Write-Utf8WithoutBom -Path $innerManifestPath -Content ($manifestJson + "`n")

    $externalManifestPath = Join-Path $OutputDirectory $manifestName
    Copy-Item -LiteralPath $innerManifestPath -Destination $externalManifestPath -Force

    $externalBundleArchive = Join-Path $OutputDirectory $bundleArchiveName
    New-DeterministicZip -SourceRoot $bundleParent -DestinationPath $externalBundleArchive

    $checksumTargets = @(
        $externalBlenderArchive,
        $externalUnityArchive,
        $externalBundleArchive,
        $externalManifestPath
    ) | Sort-Object { Split-Path -Leaf $_ }
    $checksumLines = @($checksumTargets | ForEach-Object {
        $hash = (Get-FileHash -LiteralPath $_ -Algorithm SHA256).Hash.ToLowerInvariant()
        "$hash  $(Split-Path -Leaf $_)"
    })
    $checksumPath = Join-Path $OutputDirectory $checksumName
    Write-Utf8WithoutBom -Path $checksumPath -Content (($checksumLines -join "`n") + "`n")

    $availableNativeArtifacts = @($nativeArtifacts | Where-Object { $_.available }).Count
    Write-Host "release_package_ok version=$productVersion dirty=$sourceDirty licenseGate=$licenseGate output=$OutputDirectory files=$($manifest.files.Count) nativeArtifacts=$availableNativeArtifacts/$($nativeArtifacts.Count)"
    Write-Host "release_artifact path=$externalBundleArchive"
    Write-Host "release_artifact path=$externalBlenderArchive"
    Write-Host "release_artifact path=$externalUnityArchive"
    Write-Host "release_manifest path=$externalManifestPath"
    Write-Host "release_checksums path=$checksumPath"
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
