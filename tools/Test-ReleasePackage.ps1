param()

$ErrorActionPreference = 'Stop'
. (Join-Path $PSScriptRoot 'NativeArtifactTargets.ps1')

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

function Assert-True {
    param(
        [bool]$Condition,
        [string]$Message
    )

    if (!$Condition) {
        throw $Message
    }
}

function Get-ZipEntry {
    param(
        [System.IO.Compression.ZipArchive]$Archive,
        [string]$Path
    )

    $entry = $Archive.GetEntry($Path)
    if ($null -eq $entry) {
        throw "ZIP entry is missing: $Path"
    }
    return $entry
}

function Get-StreamSha256 {
    param([System.IO.Stream]$Stream)

    $algorithm = [System.Security.Cryptography.SHA256]::Create()
    try {
        $bytes = $algorithm.ComputeHash($Stream)
        return (($bytes | ForEach-Object { $_.ToString('x2') }) -join '')
    }
    finally {
        $algorithm.Dispose()
    }
}

function Assert-ZipMatchesSourceFile {
    param(
        [System.IO.Compression.ZipArchive]$Archive,
        [string]$ArchivePath,
        [string]$SourcePath
    )

    $entry = Get-ZipEntry -Archive $Archive -Path $ArchivePath
    $expectedHash = (Get-FileHash -LiteralPath $SourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
    $stream = $entry.Open()
    try {
        $actualHash = Get-StreamSha256 -Stream $stream
    }
    finally {
        $stream.Dispose()
    }
    Assert-True -Condition ($actualHash -eq $expectedHash) -Message "Packaged file differs from source: $ArchivePath"
}

function Assert-PackageLicenseBundle {
    param(
        [System.IO.Compression.ZipArchive]$Archive,
        [string]$PackageRoot,
        [string]$RepoRoot
    )

    foreach ($relativePath in @(
        'LICENSE.md',
        'THIRD_PARTY_NOTICES.md',
        'LICENSES/GPL-3.0-or-later.txt',
        'LICENSES/MIT.txt',
        'LICENSES/THIRD_PARTY/Apache-2.0.txt',
        'LICENSES/THIRD_PARTY/Python-Software-Foundation-LICENSE.txt',
        'LICENSES/THIRD_PARTY/Rust-dependency-licenses.txt',
        'LICENSES/THIRD_PARTY/Unity-Render-Pipelines-LICENSE.md',
        'LICENSES/THIRD_PARTY/websockets-LICENSE.txt'
    )) {
        Assert-ZipMatchesSourceFile -Archive $Archive -ArchivePath "$PackageRoot/$relativePath" -SourcePath (Join-Path $RepoRoot $relativePath)
    }
    Assert-True -Condition ($null -eq $Archive.GetEntry("$PackageRoot/LICENSES/BlenderSync-Unity-Commercial-License-1.0.txt")) -Message "Package contains the retired Unity commercial license: $PackageRoot"
}

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$repoRoot = Get-RepoRoot
$version = (Get-Content -LiteralPath (Join-Path $repoRoot 'VERSION') -Raw).Trim()
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('bsrt-' + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
$repeatRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('bsrr-' + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
$bundleName = "TriSync-$version"
$bundleArchivePath = Join-Path $testRoot "$bundleName.zip"
$blenderArchivePath = Join-Path $testRoot "TriSync-Blender-$version.zip"
$unityArchivePath = Join-Path $testRoot "TriSync-Unity-$version.zip"
$manifestPath = Join-Path $testRoot "$bundleName-manifest.json"
$checksumsPath = Join-Path $testRoot "$bundleName-SHA256SUMS.txt"

try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Build-ReleasePackage.ps1') -OutputDirectory $testRoot -AllowDirty
    if ($LASTEXITCODE -ne 0) {
        throw "Build-ReleasePackage.ps1 failed with exit code $LASTEXITCODE."
    }

    foreach ($path in @($bundleArchivePath, $blenderArchivePath, $unityArchivePath, $manifestPath, $checksumsPath)) {
        Assert-True -Condition (Test-Path -LiteralPath $path -PathType Leaf) -Message "Release artifact is missing: $path"
    }

    $manifest = Get-Content -LiteralPath $manifestPath -Raw | ConvertFrom-Json
    Assert-True -Condition ([int]$manifest.schemaVersion -eq 1) -Message 'Release manifest schema mismatch.'
    Assert-True -Condition ([string]$manifest.productVersion -eq $version) -Message 'Release manifest product version mismatch.'
    Assert-True -Condition ([string]$manifest.versions.blenderAddon -eq '1.0.0') -Message 'Blender add-on version mismatch.'
    Assert-True -Condition ([string]$manifest.versions.native -eq '0.2.0') -Message 'Native component version mismatch.'
    Assert-True -Condition ([int]$manifest.versions.sessionProtocol -eq 1) -Message 'Session protocol version mismatch.'
    Assert-True -Condition ([string]$manifest.versions.assetBridgeContract -eq 'asset-bridge-v1') -Message 'AssetBridge contract version mismatch.'
    $expectedLicenseGate = if ($version.Contains('-')) { 'preview-structural' } else { 'release-ready' }
    Assert-True -Condition ([string]$manifest.licenseGate -eq $expectedLicenseGate) -Message "Release license gate mismatch. Expected '$expectedLicenseGate'."
    Assert-True -Condition (@($manifest.files).Count -gt 100) -Message 'Release manifest did not include the package contents.'
    Assert-True -Condition (@($manifest.files | Where-Object { $_.path -eq 'CONTRIBUTING.md' }).Count -eq 1) -Message 'Release manifest must include the contributor guide referenced by the documentation.'
    Assert-True -Condition (@($manifest.files | Where-Object { $_.path -eq 'LICENSES/BlenderSync-Unity-Commercial-License-1.0.txt' }).Count -eq 0) -Message 'Release manifest contains the retired Unity commercial license.'

    $expectedNativeTargets = @(Get-BlenderSyncNativeArtifactTargets)
    $nativeArtifacts = @($manifest.nativeArtifacts)
    Assert-True -Condition ($nativeArtifacts.Count -eq $expectedNativeTargets.Count) -Message 'Release manifest native artifact target count mismatch.'
    foreach ($expected in $expectedNativeTargets) {
        $matches = @($nativeArtifacts | Where-Object { [string]$_.platform -eq [string]$expected.Platform })
        Assert-True -Condition ($matches.Count -eq 1) -Message "Release manifest must contain exactly one native artifact entry for '$($expected.Platform)'."
        $artifact = $matches[0]
        $archivePath = "blendersync_vnext/native/artifacts/$($expected.Platform)/$($expected.PythonAbi)/$($expected.ModuleFile)"
        Assert-True -Condition ([string]$artifact.pythonAbi -eq [string]$expected.PythonAbi) -Message "Native artifact Python ABI mismatch for '$($expected.Platform)'."
        Assert-True -Condition ([string]$artifact.archivePath -eq $archivePath) -Message "Native artifact archive path mismatch for '$($expected.Platform)'."

        $repoRelativePath = "blender_addon/$archivePath"
        $trackedMatches = @(& git -C $repoRoot ls-files -- $repoRelativePath)
        if ($LASTEXITCODE -ne 0) {
            throw "Could not inspect tracked native artifact '$repoRelativePath'."
        }
        $sourcePath = Join-Path $repoRoot $repoRelativePath.Replace('/', '\')
        $expectedAvailable = $trackedMatches.Count -eq 1 -and (Test-Path -LiteralPath $sourcePath -PathType Leaf)
        Assert-True -Condition ([bool]$artifact.available -eq $expectedAvailable) -Message "Native artifact availability mismatch for '$($expected.Platform)'."
        if ($expectedAvailable) {
            Assert-True -Condition ([long]$artifact.bytes -eq [long](Get-Item -LiteralPath $sourcePath).Length) -Message "Native artifact byte count mismatch for '$($expected.Platform)'."
            $expectedHash = (Get-FileHash -LiteralPath $sourcePath -Algorithm SHA256).Hash.ToLowerInvariant()
            Assert-True -Condition ([string]$artifact.sha256 -eq $expectedHash) -Message "Native artifact SHA256 mismatch for '$($expected.Platform)'."
        }
        else {
            Assert-True -Condition ([long]$artifact.bytes -eq 0) -Message "Missing native artifact must report zero bytes for '$($expected.Platform)'."
            Assert-True -Condition ([string]::IsNullOrEmpty([string]$artifact.sha256)) -Message "Missing native artifact must not report a SHA256 for '$($expected.Platform)'."
        }
    }

    $bundle = [System.IO.Compression.ZipFile]::OpenRead($bundleArchivePath)
    try {
        [void](Get-ZipEntry -Archive $bundle -Path "$bundleName/manifest.json")
        [void](Get-ZipEntry -Archive $bundle -Path "$bundleName/blender/TriSync-Blender-$version.zip")
        [void](Get-ZipEntry -Archive $bundle -Path "$bundleName/unity/TriSync/Scripts/UI/SessionPanel.cs")
        [void](Get-ZipEntry -Archive $bundle -Path "$bundleName/unity/TriSync/Scripts/UI/BlenderSyncUserSettingsGUI.cs")
        [void](Get-ZipEntry -Archive $bundle -Path "$bundleName/unity/TriSync/Scripts/Localization/BlenderSyncLocalization.cs")
        [void](Get-ZipEntry -Archive $bundle -Path "$bundleName/unity/TriSync/Scripts/SceneSyncCore/EditModeGuard.cs")
        [void](Get-ZipEntry -Archive $bundle -Path "$bundleName/unity/TriSync/Scripts/SceneSyncCore/SceneSyncTransformSmoother.cs")
        [void](Get-ZipEntry -Archive $bundle -Path "$bundleName/unity/TriSync/Scripts/SceneSyncCore/TransformSmoothingPreferences.cs")
        Assert-True -Condition ($null -eq $bundle.GetEntry("$bundleName/unity/TriSync/Scripts/Runtime/BlenderSyncVNext.Runtime.asmdef")) -Message 'Release bundle contains the retired Runtime assembly.'
        Assert-True -Condition ($null -eq $bundle.GetEntry("$bundleName/unity/BlenderSyncVNext/Scripts/UI/SessionPanel.cs")) -Message 'Release bundle contains the retired Unity package root.'
        Assert-PackageLicenseBundle -Archive $bundle -PackageRoot $bundleName -RepoRoot $repoRoot
        Assert-PackageLicenseBundle -Archive $bundle -PackageRoot "$bundleName/unity/TriSync" -RepoRoot $repoRoot
        Assert-ZipMatchesSourceFile -Archive $bundle -ArchivePath "$bundleName/CONTRIBUTING.md" -SourcePath (Join-Path $repoRoot 'CONTRIBUTING.md')
        [void](Get-ZipEntry -Archive $bundle -Path "$bundleName/docs/QUICK_START.md")

        foreach ($file in @($manifest.files)) {
            $entry = Get-ZipEntry -Archive $bundle -Path "$bundleName/$($file.path)"
            Assert-True -Condition ([long]$entry.Length -eq [long]$file.bytes) -Message "Manifest byte count mismatch: $($file.path)"
            $stream = $entry.Open()
            try {
                $actualHash = Get-StreamSha256 -Stream $stream
            }
            finally {
                $stream.Dispose()
            }
            Assert-True -Condition ($actualHash -eq [string]$file.sha256) -Message "Manifest SHA256 mismatch: $($file.path)"
        }
    }
    finally {
        $bundle.Dispose()
    }

    $blenderArchive = [System.IO.Compression.ZipFile]::OpenRead($blenderArchivePath)
    try {
        [void](Get-ZipEntry -Archive $blenderArchive -Path 'blendersync_vnext/__init__.py')
        [void](Get-ZipEntry -Archive $blenderArchive -Path 'blendersync_vnext/blender/translations.py')
        Assert-PackageLicenseBundle -Archive $blenderArchive -PackageRoot 'blendersync_vnext' -RepoRoot $repoRoot
        foreach ($artifact in $nativeArtifacts) {
            $entry = $blenderArchive.GetEntry([string]$artifact.archivePath)
            if ([bool]$artifact.available) {
                Assert-True -Condition ($null -ne $entry) -Message "Available native artifact is missing from Blender archive: $($artifact.archivePath)"
                Assert-True -Condition ([long]$entry.Length -eq [long]$artifact.bytes) -Message "Native artifact ZIP byte count mismatch: $($artifact.archivePath)"
                $stream = $entry.Open()
                try {
                    $actualHash = Get-StreamSha256 -Stream $stream
                }
                finally {
                    $stream.Dispose()
                }
                Assert-True -Condition ($actualHash -eq [string]$artifact.sha256) -Message "Native artifact ZIP SHA256 mismatch: $($artifact.archivePath)"
            }
            else {
                Assert-True -Condition ($null -eq $entry) -Message "Unavailable native artifact unexpectedly exists in Blender archive: $($artifact.archivePath)"
            }
        }
        Assert-True -Condition (@($blenderArchive.Entries | Where-Object { $_.FullName -match '(^|/)__pycache__(/|$)' }).Count -eq 0) -Message 'Blender archive contains Python cache files.'
    }
    finally {
        $blenderArchive.Dispose()
    }

    $unityArchive = [System.IO.Compression.ZipFile]::OpenRead($unityArchivePath)
    try {
        [void](Get-ZipEntry -Archive $unityArchive -Path 'TriSync/Scripts/UI/SessionPanel.cs')
        [void](Get-ZipEntry -Archive $unityArchive -Path 'TriSync/Scripts/Localization/BlenderSyncLocalization.cs')
        [void](Get-ZipEntry -Archive $unityArchive -Path 'TriSync/Shaders/TriSync_PrincipledLit_URP.shader')
        Assert-PackageLicenseBundle -Archive $unityArchive -PackageRoot 'TriSync' -RepoRoot $repoRoot
        Assert-True -Condition ($null -eq $unityArchive.GetEntry('BlenderSyncVNext/Scripts/UI/SessionPanel.cs')) -Message 'Unity archive contains the retired package root.'
        Assert-True -Condition (@($unityArchive.Entries | Where-Object { $_.FullName -like '*/Registry/*.json' }).Count -eq 0) -Message 'Unity archive contains generated Registry JSON.'
    }
    finally {
        $unityArchive.Dispose()
    }

    $checksumLines = @(Get-Content -LiteralPath $checksumsPath | Where-Object { ![string]::IsNullOrWhiteSpace($_) })
    Assert-True -Condition ($checksumLines.Count -eq 4) -Message 'Release checksum list must contain four artifacts.'
    foreach ($line in $checksumLines) {
        if ($line -notmatch '^(?<hash>[0-9a-f]{64})  (?<name>[^\\/]+)$') {
            throw "Malformed release checksum line: $line"
        }
        $artifactPath = Join-Path $testRoot $Matches.name
        Assert-True -Condition (Test-Path -LiteralPath $artifactPath -PathType Leaf) -Message "Checksummed artifact is missing: $($Matches.name)"
        $actual = (Get-FileHash -LiteralPath $artifactPath -Algorithm SHA256).Hash.ToLowerInvariant()
        Assert-True -Condition ($actual -eq $Matches.hash) -Message "Artifact checksum mismatch: $($Matches.name)"
    }

    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Build-ReleasePackage.ps1') -OutputDirectory $repeatRoot -AllowDirty
    if ($LASTEXITCODE -ne 0) {
        throw "Repeated Build-ReleasePackage.ps1 failed with exit code $LASTEXITCODE."
    }
    foreach ($artifactName in @(
        "$bundleName.zip",
        "TriSync-Blender-$version.zip",
        "TriSync-Unity-$version.zip",
        "$bundleName-manifest.json",
        "$bundleName-SHA256SUMS.txt"
    )) {
        $firstHash = (Get-FileHash -LiteralPath (Join-Path $testRoot $artifactName) -Algorithm SHA256).Hash
        $repeatHash = (Get-FileHash -LiteralPath (Join-Path $repeatRoot $artifactName) -Algorithm SHA256).Hash
        Assert-True -Condition ($firstHash -eq $repeatHash) -Message "Release artifact is not reproducible: $artifactName"
    }

    Write-Host "release_package_test_ok version=$version manifestFiles=$(@($manifest.files).Count)"
}
finally {
    $tempRoot = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    $testRootFull = [System.IO.Path]::GetFullPath($testRoot)
    if (!$testRootFull.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean release test path outside the system temp directory: $testRootFull"
    }
    if (Test-Path -LiteralPath $testRootFull) {
        Remove-Item -LiteralPath $testRootFull -Recurse -Force
    }
    $repeatRootFull = [System.IO.Path]::GetFullPath($repeatRoot)
    if (!$repeatRootFull.StartsWith($tempRoot, [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "Refusing to clean repeated release test path outside the system temp directory: $repeatRootFull"
    }
    if (Test-Path -LiteralPath $repeatRootFull) {
        Remove-Item -LiteralPath $repeatRootFull -Recurse -Force
    }
}
