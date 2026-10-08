param()

$ErrorActionPreference = 'Stop'

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

Add-Type -AssemblyName System.IO.Compression
Add-Type -AssemblyName System.IO.Compression.FileSystem

$repoRoot = Get-RepoRoot
$testRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('bssupert-' + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
$repeatRoot = Join-Path ([System.IO.Path]::GetTempPath()) ('bssuperr-' + [System.Guid]::NewGuid().ToString('N').Substring(0, 8))
$forbiddenExtensions = @('.pyd', '.so', '.dylib', '.dll', '.exe', '.pyc', '.bin', '.obj', '.lib', '.a', '.o')

try {
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Build-SuperhiveBlenderPackage.ps1') -OutputDirectory $testRoot -SourceCommit HEAD
    if ($LASTEXITCODE -ne 0) {
        throw "Build-SuperhiveBlenderPackage.ps1 failed with exit code $LASTEXITCODE."
    }
    & powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Build-SuperhiveBlenderPackage.ps1') -OutputDirectory $repeatRoot -SourceCommit HEAD
    if ($LASTEXITCODE -ne 0) {
        throw "Repeated Build-SuperhiveBlenderPackage.ps1 failed with exit code $LASTEXITCODE."
    }

    $zipPath = Get-ChildItem -LiteralPath $testRoot -File -Filter '*.zip' | Select-Object -First 1
    $manifestPath = Get-ChildItem -LiteralPath $testRoot -File -Filter '*-manifest.json' | Select-Object -First 1
    $checksumsPath = Get-ChildItem -LiteralPath $testRoot -File -Filter '*-SHA256SUMS.txt' | Select-Object -First 1
    Assert-True -Condition ($null -ne $zipPath) -Message 'Source-only Blender ZIP is missing.'
    Assert-True -Condition ($null -ne $manifestPath) -Message 'Source-only Blender manifest is missing.'
    Assert-True -Condition ($null -ne $checksumsPath) -Message 'Source-only Blender checksum list is missing.'

    $manifest = Get-Content -LiteralPath $manifestPath.FullName -Raw | ConvertFrom-Json
    Assert-True -Condition ([string]$manifest.distributionVariant -eq 'superhive-source-only') -Message 'Source-only distribution marker mismatch.'
    Assert-True -Condition ([bool]$manifest.nativeArtifactsIncluded -eq $false) -Message 'Source-only manifest includes native artifacts.'
    Assert-True -Condition ([bool]$manifest.nativeSourceIncluded -eq $false) -Message 'Source-only manifest includes native build source.'
    Assert-True -Condition ([int]$manifest.forbiddenCompiledFileCount -eq 0) -Message 'Source-only manifest reports compiled files.'
    Assert-True -Condition (@($manifest.files).Count -gt 100) -Message 'Source-only manifest is unexpectedly small.'

    foreach ($file in @($manifest.files)) {
        $extension = [System.IO.Path]::GetExtension([string]$file.path).ToLowerInvariant()
        Assert-True -Condition ($forbiddenExtensions -notcontains $extension) -Message "Source-only manifest contains forbidden file '$($file.path)'."
        Assert-True -Condition (-not ([string]$file.path -like '*/native/artifacts/*')) -Message "Source-only manifest contains native artifact path '$($file.path)'."
        Assert-True -Condition (-not ([string]$file.path -like '*/native/blendersync_native/*')) -Message "Source-only manifest contains native source path '$($file.path)'."
    }

    $archive = [System.IO.Compression.ZipFile]::OpenRead($zipPath.FullName)
    try {
        Assert-True -Condition ($null -ne $archive.GetEntry('blendersync_vnext/__init__.py')) -Message 'Source-only ZIP is missing the add-on entry point.'
        Assert-True -Condition (@($archive.Entries | Where-Object { $_.FullName -like '*/native/artifacts/*' }).Count -eq 0) -Message 'Source-only ZIP contains native artifact paths.'
        Assert-True -Condition (@($archive.Entries | Where-Object { $_.FullName -like '*/native/blendersync_native/*' }).Count -eq 0) -Message 'Source-only ZIP contains native source files.'
        Assert-True -Condition ($null -eq $archive.GetEntry('blendersync_vnext/SOURCE_ONLY_NATIVE.md')) -Message 'Source-only ZIP contains retired native acquisition instructions.'
        foreach ($entry in $archive.Entries) {
            $extension = [System.IO.Path]::GetExtension($entry.FullName).ToLowerInvariant()
            Assert-True -Condition ($forbiddenExtensions -notcontains $extension) -Message "Source-only ZIP contains forbidden file '$($entry.FullName)'."
        }
    }
    finally {
        $archive.Dispose()
    }

    $repeatZip = Get-ChildItem -LiteralPath $repeatRoot -File -Filter '*.zip' | Select-Object -First 1
    $repeatManifest = Get-ChildItem -LiteralPath $repeatRoot -File -Filter '*-manifest.json' | Select-Object -First 1
    Assert-True -Condition ((Get-FileHash -LiteralPath $zipPath.FullName -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $repeatZip.FullName -Algorithm SHA256).Hash) -Message 'Source-only ZIP is not reproducible.'
    Assert-True -Condition ((Get-FileHash -LiteralPath $manifestPath.FullName -Algorithm SHA256).Hash -eq (Get-FileHash -LiteralPath $repeatManifest.FullName -Algorithm SHA256).Hash) -Message 'Source-only manifest is not reproducible.'

    Write-Host "superhive_package_test_ok version=$($manifest.productVersion) files=$(@($manifest.files).Count) compiledFiles=0"
}
finally {
    $tempPrefix = [System.IO.Path]::GetFullPath([System.IO.Path]::GetTempPath()).TrimEnd('\', '/') + [System.IO.Path]::DirectorySeparatorChar
    foreach ($path in @($testRoot, $repeatRoot)) {
        $full = [System.IO.Path]::GetFullPath($path)
        if (!$full.StartsWith($tempPrefix, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to clean test path outside the system temp directory: $full"
        }
        if (Test-Path -LiteralPath $full) {
            Remove-Item -LiteralPath $full -Recurse -Force
        }
    }
}
