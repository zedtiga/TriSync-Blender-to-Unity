param(
    [string]$AddonRoot,
    [switch]$ExcludeVendor,
    [switch]$VerboseFiles
)

$ErrorActionPreference = 'Stop'

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

$repoRoot = Get-RepoRoot
if ([string]::IsNullOrWhiteSpace($AddonRoot)) {
    $AddonRoot = Join-Path $repoRoot 'blender_addon\blendersync_vnext'
}

$AddonRoot = (Resolve-Path -LiteralPath $AddonRoot).Path
$files = @(Get-ChildItem -LiteralPath $AddonRoot -Recurse -File -Filter '*.py' | Where-Object {
    $rel = Convert-ToRelativePath -Root $AddonRoot -Path $_.FullName
    if ($rel -like '__pycache__\*' -or $rel -like '*\__pycache__\*') {
        return $false
    }
    if ($ExcludeVendor -and $rel -like 'vendor\*') {
        return $false
    }
    return $true
})

Write-Host "addon_root=$AddonRoot"
Write-Host "python_files=$($files.Count)"

if ($files.Count -eq 0) {
    throw "No Python files found under $AddonRoot"
}

if ($VerboseFiles) {
    $files | ForEach-Object {
        $rel = Convert-ToRelativePath -Root $AddonRoot -Path $_.FullName
        Write-Host "ast_parse $rel"
    }
}

& uv run python --version
if ($LASTEXITCODE -ne 0) {
    throw "uv run python --version failed with exit code $LASTEXITCODE"
}

$script = @'
import ast
import pathlib
import sys

ok = True
for raw_path in sys.argv[1:]:
    path = pathlib.Path(raw_path)
    try:
        ast.parse(
            path.read_text(encoding="utf-8-sig"),
            filename=str(path),
            feature_version=(3, 11),
        )
    except SyntaxError as exc:
        ok = False
        print(f"{path}:{exc.lineno}:{exc.offset}: syntax_error: {exc.msg}", file=sys.stderr)
    except Exception as exc:
        ok = False
        print(f"{path}: read_or_parse_failed: {exc}", file=sys.stderr)

if not ok:
    raise SystemExit(1)
'@

$paths = @($files | ForEach-Object { $_.FullName })
$tempScript = New-TemporaryFile
try {
    Set-Content -LiteralPath $tempScript.FullName -Value $script -NoNewline
    & uv run python $tempScript.FullName @paths
    if ($LASTEXITCODE -ne 0) {
        throw "Blender Python AST parse failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -LiteralPath $tempScript.FullName -Force -ErrorAction SilentlyContinue
}

Write-Host "blender_python_check_ok"
