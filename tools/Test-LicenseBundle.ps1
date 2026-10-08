[CmdletBinding()]
param(
    [switch]$RequireReleaseReady
)

$ErrorActionPreference = 'Stop'

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

function Assert-FileContains {
    param(
        [string]$Path,
        [string]$Expected
    )
    $content = Get-Content -LiteralPath $Path -Raw -Encoding UTF8
    if (!$content.Contains($Expected)) {
        throw "Required license text '$Expected' is missing from $Path"
    }
}

$repoRoot = Get-RepoRoot
$requiredFiles = @(
    'LICENSE.md',
    'THIRD_PARTY_NOTICES.md',
    'LICENSES\GPL-3.0-or-later.txt',
    'LICENSES\MIT.txt',
    'LICENSES\THIRD_PARTY\Apache-2.0.txt',
    'LICENSES\THIRD_PARTY\Python-Software-Foundation-LICENSE.txt',
    'LICENSES\THIRD_PARTY\Rust-dependency-licenses.txt',
    'LICENSES\THIRD_PARTY\Unity-Render-Pipelines-LICENSE.md',
    'LICENSES\THIRD_PARTY\websockets-LICENSE.txt'
)
foreach ($relativePath in $requiredFiles) {
    $path = Join-Path $repoRoot $relativePath
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Required license file is missing: $relativePath"
    }
}

$retiredCommercialLicense = Join-Path $repoRoot 'LICENSES\BlenderSync-Unity-Commercial-License-1.0.txt'
if (Test-Path -LiteralPath $retiredCommercialLicense) {
    throw 'Retired Unity commercial license must not be present.'
}

$licenseOverviewPath = Join-Path $repoRoot 'LICENSE.md'
$mitLicensePath = Join-Path $repoRoot 'LICENSES\MIT.txt'
Assert-FileContains -Path $mitLicensePath -Expected 'Copyright (c) 2026 TriSync contributors'
Assert-FileContains -Path $mitLicensePath -Expected 'Permission is hereby granted, free of charge'
Assert-FileContains -Path $licenseOverviewPath -Expected '## Unity Companion: MIT'
Assert-FileContains -Path $licenseOverviewPath -Expected 'TriSync C# tooling is'
Assert-FileContains -Path $licenseOverviewPath -Expected 'Editor-only and is not included in Unity player builds.'
Assert-FileContains -Path $licenseOverviewPath -Expected 'not covered by the MIT grant above or the GPL declaration.'

$retiredLicenseReferences = @(
    'LICENSE.md',
    'README.md',
    'THIRD_PARTY_NOTICES.md',
    'docs\RELEASE.md'
) | ForEach-Object {
    Select-String -LiteralPath (Join-Path $repoRoot $_) -SimpleMatch -Pattern @(
        'BlenderSync-Unity-Commercial-License',
        'BlenderSync Unity Companion Commercial License',
        'BlenderSync commercial license'
    )
}
if ($retiredLicenseReferences.Count -gt 0) {
    $retiredLicenseReferences | ForEach-Object {
        Write-Host "retired_license_reference $($_.Path):$($_.LineNumber) $($_.Line.Trim())"
    }
    throw 'Retired Unity commercial license references remain in active licensing documents.'
}

$noticePath = Join-Path $repoRoot 'THIRD_PARTY_NOTICES.md'
$versionPath = Join-Path $repoRoot 'blender_addon\blendersync_vnext\vendor\websockets\version.py'
$versionContent = Get-Content -LiteralPath $versionPath -Raw -Encoding UTF8
$versionMatch = [regex]::Match($versionContent, '(?m)^tag = version = commit = "([^"]+)"\r?$')
if (!$versionMatch.Success) {
    throw 'Could not determine the vendored websockets version.'
}
Assert-FileContains -Path $noticePath -Expected "### websockets $($versionMatch.Groups[1].Value)"

$urpDerivedFiles = @(
    'unity/TriSync/Shaders/BlenderSyncDepthOnlyPass.hlsl',
    'unity/TriSync/Shaders/BlenderSyncLitDepthNormalsPass.hlsl',
    'unity/TriSync/Shaders/BlenderSyncLitForwardPass.hlsl',
    'unity/TriSync/Shaders/BlenderSyncLitGBufferPass.hlsl',
    'unity/TriSync/Shaders/BlenderSyncLitInput.hlsl',
    'unity/TriSync/Shaders/BlenderSyncLitMetaPass.hlsl',
    'unity/TriSync/Shaders/BlenderSyncObjectMotionVectorsPass.hlsl',
    'unity/TriSync/Shaders/BlenderSyncShadowCasterPass.hlsl',
    'unity/TriSync/Shaders/BlenderSyncUniversal2DPass.hlsl',
    'unity/TriSync/Shaders/TriSync_PrincipledLit_URP.shader',
    'unity/TriSync/Scripts/Editor/BlenderSyncPrincipledLitShaderGUI.cs'
)
foreach ($relativePath in $urpDerivedFiles) {
    if (!(Test-Path -LiteralPath (Join-Path $repoRoot $relativePath) -PathType Leaf)) {
        throw "Declared URP-derived file is missing: $relativePath"
    }
    Assert-FileContains -Path $noticePath -Expected $relativePath
    $sourcePath = Join-Path $repoRoot $relativePath
    Assert-FileContains -Path $sourcePath -Expected 'Modified from Unity Universal Render Pipeline 17.3.0 sources under the Unity Companion License.'
    Assert-FileContains -Path $sourcePath -Expected 'See THIRD_PARTY_NOTICES.md for provenance and terms.'
}
Assert-FileContains -Path (Join-Path $repoRoot 'LICENSES\THIRD_PARTY\Unity-Render-Pipelines-LICENSE.md') -Expected 'Unity Companion License'

$unityScriptsRoot = Join-Path $repoRoot 'unity\TriSync\Scripts'
$blockedSourcePattern = 'Unity C# reference source|Unity_Reference_Only_License|unity-reference-only-license'
$blockedMatches = @(Get-ChildItem -LiteralPath $unityScriptsRoot -Recurse -File -Filter '*.cs' |
    Select-String -Pattern $blockedSourcePattern)
if ($blockedMatches.Count -gt 0) {
    $blockedMatches | ForEach-Object { Write-Host "blocked_provenance $($_.Path):$($_.LineNumber) $($_.Line.Trim())" }
    throw 'Unity Reference Only provenance markers remain in product source.'
}
$blockedSourceFiles = @(
    'unity\TriSync\Scripts\RootMotion\UnityAvatarAutoMapper.cs',
    'unity\TriSync\Scripts\RootMotion\UnityAvatarBipedMapper.cs'
)
foreach ($relativePath in $blockedSourceFiles) {
    if (Test-Path -LiteralPath (Join-Path $repoRoot $relativePath)) {
        throw "Unity Reference Only source file is present: $relativePath"
    }
}

$cargoManifest = Join-Path $repoRoot 'blender_addon\blendersync_vnext\native\blendersync_native\Cargo.toml'
Assert-FileContains -Path $cargoManifest -Expected 'license = "GPL-3.0-or-later"'

& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Update-RustThirdPartyLicenses.ps1') -Check
if ($LASTEXITCODE -ne 0) {
    throw "Rust dependency license validation failed with exit code $LASTEXITCODE"
}

$noticeContent = Get-Content -LiteralPath $noticePath -Raw -Encoding UTF8
$releaseBlockers = @([regex]::Matches($noticeContent, '(?im)^Status:\s*release blocker\b.*$'))
if ($RequireReleaseReady) {
    $reasons = New-Object System.Collections.Generic.List[string]
    if ($releaseBlockers.Count -gt 0) {
        $reasons.Add("third-party notice blockers: $($releaseBlockers.Count)")
    }
    if ($reasons.Count -gt 0) {
        throw "License bundle is not release-ready. $($reasons -join '; ')"
    }
}

$rustLicensePath = Join-Path $repoRoot 'LICENSES\THIRD_PARTY\Rust-dependency-licenses.txt'
$rustPackageCount = @(Select-String -LiteralPath $rustLicensePath -Pattern '^Package:').Count
if ($rustPackageCount -le 0) {
    throw 'Rust dependency license aggregate contains no package entries.'
}
$ready = $releaseBlockers.Count -eq 0
Write-Host "license_bundle_ok releaseReady=$ready rustPackages=$rustPackageCount urpDerivedFiles=$($urpDerivedFiles.Count)"
