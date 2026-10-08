param()

$ErrorActionPreference = 'Stop'

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

$repoRoot = Get-RepoRoot
$testRoot = Join-Path $repoRoot 'tests\python'

if (!(Test-Path -LiteralPath $testRoot -PathType Container)) {
    throw "Python test root not found: $testRoot"
}

& uv run python -m unittest discover -s $testRoot -p 'test_*.py' -v
if ($LASTEXITCODE -ne 0) {
    throw "Blender Python unit tests failed with exit code $LASTEXITCODE"
}

Write-Host "blender_unit_tests_ok"
