[CmdletBinding()]
param(
    [switch]$Check
)

$ErrorActionPreference = 'Stop'

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

function Add-Line {
    param(
        [System.Collections.Generic.List[string]]$Lines,
        [string]$Value = ''
    )
    $Lines.Add($Value)
}

function Get-SelectedLicenseFiles {
    param([object]$Package)

    $packageRoot = Split-Path -Parent $Package.manifest_path
    $available = @(Get-ChildItem -LiteralPath $packageRoot -File | Where-Object {
        $_.Name -match '^(LICENSE|COPYING|NOTICE)'
    })

    if ($Package.name -eq 'target-lexicon') {
        return @($available | Where-Object Name -eq 'LICENSE')
    }

    if ($Package.name -eq 'unicode-ident') {
        return @(
            Get-Item -LiteralPath (Join-Path $packageRoot 'LICENSE-MIT'),
                (Join-Path $packageRoot 'LICENSE-UNICODE')
        )
    }

    $selected = @($available | Where-Object { $_.Name -match '^LICENSE-MIT(?:\.txt)?$' })
    if ($selected.Count -gt 0) {
        return $selected
    }

    return @($available | Where-Object {
        $_.Name -in @('LICENSE', 'LICENSE.txt', 'COPYING') -and
        (Get-Content -LiteralPath $_.FullName -Raw -Encoding UTF8) -match 'Permission is hereby granted|MIT License'
    } | Select-Object -First 1)
}

$repoRoot = Get-RepoRoot
$manifestPath = Join-Path $repoRoot 'blender_addon\blendersync_vnext\native\blendersync_native\Cargo.toml'
$outputPath = Join-Path $repoRoot 'LICENSES\THIRD_PARTY\Rust-dependency-licenses.txt'

$metadataRaw = & cargo metadata --offline --locked --format-version 1 --manifest-path $manifestPath
if ($LASTEXITCODE -ne 0) {
    throw "cargo metadata failed with exit code $LASTEXITCODE"
}
$metadata = $metadataRaw | ConvertFrom-Json
$packages = @($metadata.packages | Where-Object { $null -ne $_.source } | Sort-Object name)
if ($packages.Count -eq 0) {
    throw 'No locked Rust dependencies were found.'
}

$lines = New-Object System.Collections.Generic.List[string]
Add-Line $lines 'BLENDERSYNC NATIVE HELPER - THIRD-PARTY RUST LICENSES'
Add-Line $lines
Add-Line $lines 'Generated from Cargo.lock and local crate license files.'
Add-Line $lines 'Do not edit manually. Run tools/Update-RustThirdPartyLicenses.ps1.'
Add-Line $lines

foreach ($package in $packages) {
    if ([string]::IsNullOrWhiteSpace($package.license)) {
        throw "Rust dependency '$($package.name) $($package.version)' has no license expression."
    }

    $licenseFiles = @(Get-SelectedLicenseFiles -Package $package)
    if ($licenseFiles.Count -eq 0) {
        throw "No supported license file found for '$($package.name) $($package.version)' ($($package.license))."
    }

    Add-Line $lines ('=' * 78)
    Add-Line $lines "Package: $($package.name) $($package.version)"
    Add-Line $lines "Declared license: $($package.license)"
    if ($package.name -eq 'target-lexicon') {
        Add-Line $lines 'Selected terms: Apache-2.0 WITH LLVM-exception'
    }
    elseif ($package.name -eq 'unicode-ident') {
        Add-Line $lines 'Selected terms: MIT AND Unicode-3.0'
    }
    else {
        Add-Line $lines 'Selected terms: MIT'
    }
    Add-Line $lines

    foreach ($licenseFile in $licenseFiles) {
        Add-Line $lines "--- $($licenseFile.Name) ---"
        $licenseContent = (Get-Content -LiteralPath $licenseFile.FullName -Raw -Encoding UTF8) -replace "`r`n", "`n"
        $licenseContent = $licenseContent.TrimEnd("`r", "`n")
        foreach ($line in $licenseContent -split "`n") {
            Add-Line $lines $line
        }
        Add-Line $lines
    }
}

$lastLineIndex = $lines.Count - 1
while ($lastLineIndex -ge 0 -and $lines[$lastLineIndex] -eq '') {
    $lines.RemoveAt($lastLineIndex)
    $lastLineIndex -= 1
}
$generated = [string]::Join("`n", $lines) + "`n"
if ($Check) {
    if (!(Test-Path -LiteralPath $outputPath -PathType Leaf)) {
        throw "Rust dependency license aggregate is missing: $outputPath"
    }
    $current = (Get-Content -LiteralPath $outputPath -Raw -Encoding UTF8) -replace "`r`n", "`n"
    if ($current -ne $generated) {
        throw 'Rust dependency licenses are stale. Run tools/Update-RustThirdPartyLicenses.ps1.'
    }
    Write-Host "rust_dependency_license_check_ok packages=$($packages.Count)"
    exit 0
}

$outputDir = Split-Path -Parent $outputPath
if (!(Test-Path -LiteralPath $outputDir)) {
    New-Item -ItemType Directory -Path $outputDir -Force | Out-Null
}
$utf8NoBom = New-Object System.Text.UTF8Encoding($false)
[System.IO.File]::WriteAllText($outputPath, $generated, $utf8NoBom)
Write-Host "rust_dependency_licenses_updated packages=$($packages.Count) path=$outputPath"
