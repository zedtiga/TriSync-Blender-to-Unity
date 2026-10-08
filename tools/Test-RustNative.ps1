param(
    [string]$CrateRoot,
    [switch]$Release
)

$ErrorActionPreference = 'Stop'

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

$repoRoot = Get-RepoRoot
if ([string]::IsNullOrWhiteSpace($CrateRoot)) {
    $CrateRoot = Join-Path $repoRoot 'blender_addon\blendersync_vnext\native\blendersync_native'
}

$CrateRoot = (Resolve-Path -LiteralPath $CrateRoot).Path
Write-Host "crate_root=$CrateRoot"

$env:PYO3_NO_PYTHON = '1'
& cargo --version
if ($LASTEXITCODE -ne 0) {
    throw "cargo --version failed with exit code $LASTEXITCODE"
}

$cargoArgs = @('check')
if ($Release) {
    $cargoArgs += '--release'
}

& cargo @cargoArgs --manifest-path (Join-Path $CrateRoot 'Cargo.toml')
if ($LASTEXITCODE -ne 0) {
    throw "cargo check failed with exit code $LASTEXITCODE"
}

Write-Host "rust_native_check_ok"
