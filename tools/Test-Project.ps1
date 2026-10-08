param(
    [string]$UnityProjectRoot,
    [string]$UnityRuntimeRoot,
    [string]$UnityEditorPath,
    [string]$BlenderRuntimeRoot,
    [switch]$SkipUnityBuild,
    [switch]$SkipRustNative,
    [switch]$RunUnityEditModeTests,
    [switch]$SyncBlenderAddon,
    [switch]$CleanBlenderPythonCache,
    [switch]$RemoveStaleBlenderAddonFiles,
    [switch]$RemoveStaleUnityRuntimeFiles,
    [switch]$VerboseFiles
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($UnityProjectRoot)) {
    throw 'Pass -UnityProjectRoot with the path to your Unity URP test project. See CONTRIBUTING.md for checks that do not require an editor installation.'
}
if (!(Test-Path -LiteralPath $UnityProjectRoot -PathType Container)) {
    throw "Unity project directory not found: $UnityProjectRoot. Pass -UnityProjectRoot with an existing project directory."
}
if ($SyncBlenderAddon -and [string]::IsNullOrWhiteSpace($BlenderRuntimeRoot)) {
    throw 'Pass -BlenderRuntimeRoot with the path to your Blender scripts\addons\blendersync_vnext directory when using -SyncBlenderAddon.'
}

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

$repoRoot = Get-RepoRoot

Write-Host "== Process Helpers =="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Test-ProcessHelpers.ps1')
if ($LASTEXITCODE -ne 0) {
    throw "Test-ProcessHelpers.ps1 failed with exit code $LASTEXITCODE"
}

Write-Host "== Compatibility Matrix Definition =="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Test-CompatibilityMatrix.ps1') -ValidateOnly
if ($LASTEXITCODE -ne 0) {
    throw "Test-CompatibilityMatrix.ps1 -ValidateOnly failed with exit code $LASTEXITCODE"
}

Write-Host "== Blender Python =="
$pythonArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'tools\Test-BlenderPython.ps1'))
if ($VerboseFiles) {
    $pythonArgs += '-VerboseFiles'
}
& powershell @pythonArgs
if ($LASTEXITCODE -ne 0) {
    throw "Test-BlenderPython.ps1 failed with exit code $LASTEXITCODE"
}

Write-Host "== Blender Unit Tests =="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Test-BlenderUnit.ps1')
if ($LASTEXITCODE -ne 0) {
    throw "Test-BlenderUnit.ps1 failed with exit code $LASTEXITCODE"
}

if ($SyncBlenderAddon) {
    Write-Host "== Blender Addon Runtime Sync =="
    $blenderSyncArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'tools\Sync-BlenderAddon.ps1'), '-RuntimeRoot', $BlenderRuntimeRoot)
    if ($CleanBlenderPythonCache) {
        $blenderSyncArgs += '-CleanPythonCache'
    }
    if ($RemoveStaleBlenderAddonFiles) {
        $blenderSyncArgs += '-RemoveStaleFiles'
    }
    if ($VerboseFiles) {
        $blenderSyncArgs += '-VerboseFiles'
    }
    & powershell @blenderSyncArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Sync-BlenderAddon.ps1 failed with exit code $LASTEXITCODE"
    }
}

Write-Host "== Protocol Types =="
$protocolArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'tools\Test-ProtocolTypes.ps1'))
if ($VerboseFiles) {
    $protocolArgs += '-VerboseTypes'
}
& powershell @protocolArgs
if ($LASTEXITCODE -ne 0) {
    throw "Test-ProtocolTypes.ps1 failed with exit code $LASTEXITCODE"
}

Write-Host "== Logging Policy =="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Test-LoggingPolicy.ps1')
if ($LASTEXITCODE -ne 0) {
    throw "Test-LoggingPolicy.ps1 failed with exit code $LASTEXITCODE"
}

if (!$SkipRustNative) {
    Write-Host "== Rust Native =="
    $rustArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'tools\Test-RustNative.ps1'))
    & powershell @rustArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Test-RustNative.ps1 failed with exit code $LASTEXITCODE"
    }
}

Write-Host "== License Bundle =="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Test-LicenseBundle.ps1')
if ($LASTEXITCODE -ne 0) {
    throw "Test-LicenseBundle.ps1 failed with exit code $LASTEXITCODE"
}

Write-Host "== Release Package =="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Test-ReleasePackage.ps1')
if ($LASTEXITCODE -ne 0) {
    throw "Test-ReleasePackage.ps1 failed with exit code $LASTEXITCODE"
}

Write-Host "== Superhive Source-Only Blender Package =="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Test-SuperhiveBlenderPackage.ps1')
if ($LASTEXITCODE -ne 0) {
    throw "Test-SuperhiveBlenderPackage.ps1 failed with exit code $LASTEXITCODE"
}

Write-Host "== Unity Player Boundary =="
& powershell -NoProfile -ExecutionPolicy Bypass -File (Join-Path $repoRoot 'tools\Test-UnityPlayerBoundary.ps1')
if ($LASTEXITCODE -ne 0) {
    throw "Test-UnityPlayerBoundary.ps1 failed with exit code $LASTEXITCODE"
}

Write-Host "== Unity Runtime Sync =="
$syncArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'tools\Sync-UnityRuntime.ps1'), '-UnityProjectRoot', $UnityProjectRoot)
if (![string]::IsNullOrWhiteSpace($UnityRuntimeRoot)) {
    $syncArgs += @('-RuntimeRoot', $UnityRuntimeRoot)
}
if ($SkipUnityBuild) {
    $syncArgs += '-SkipBuild'
}
if ($RemoveStaleUnityRuntimeFiles) {
    $syncArgs += '-RemoveStaleFiles'
}
if ($VerboseFiles) {
    $syncArgs += '-VerboseFiles'
}
& powershell @syncArgs
if ($LASTEXITCODE -ne 0) {
    throw "Sync-UnityRuntime.ps1 failed with exit code $LASTEXITCODE"
}

if ($RunUnityEditModeTests) {
    Write-Host "== Unity EditMode Tests =="
    $editModeArgs = @('-NoProfile', '-ExecutionPolicy', 'Bypass', '-File', (Join-Path $repoRoot 'tools\Test-UnityEditMode.ps1'), '-UnityProjectRoot', $UnityProjectRoot)
    if (![string]::IsNullOrWhiteSpace($UnityEditorPath)) {
        $editModeArgs += @('-UnityEditorPath', $UnityEditorPath)
    }
    & powershell @editModeArgs
    if ($LASTEXITCODE -ne 0) {
        throw "Test-UnityEditMode.ps1 failed with exit code $LASTEXITCODE"
    }
}

Write-Host "project_validation_ok"
