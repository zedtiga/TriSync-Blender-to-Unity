param(
    [string]$BlenderRoot,
    [string]$UnityScriptsRoot,
    [string]$DebtFile
)

$ErrorActionPreference = 'Stop'

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

$repoRoot = Get-RepoRoot
if ([string]::IsNullOrWhiteSpace($BlenderRoot)) {
    $BlenderRoot = Join-Path $repoRoot 'blender_addon\blendersync_vnext\blender'
}
if ([string]::IsNullOrWhiteSpace($UnityScriptsRoot)) {
    $UnityScriptsRoot = Join-Path $repoRoot 'unity\TriSync\Scripts'
}
if ([string]::IsNullOrWhiteSpace($DebtFile)) {
    $DebtFile = Join-Path $repoRoot 'tools\logging-debt.json'
}

$BlenderRoot = (Resolve-Path -LiteralPath $BlenderRoot).Path
$UnityScriptsRoot = (Resolve-Path -LiteralPath $UnityScriptsRoot).Path
$DebtFile = (Resolve-Path -LiteralPath $DebtFile).Path

$script = @'
import ast
import json
import pathlib
import re
import sys


blender_root = pathlib.Path(sys.argv[1])
unity_root = pathlib.Path(sys.argv[2])
debt_path = pathlib.Path(sys.argv[3])
# The exception boundary must remain independent from the facade so it can
# report a facade failure without recursively re-entering that facade.
allowed_blender = {"common/log.py": 2, "common/exception_boundary.py": 2}
allowed_unity = {"Diagnostics/BlenderSyncLog.cs": 4}


def relative(path: pathlib.Path, root: pathlib.Path) -> str:
    return path.relative_to(root).as_posix()


def is_print_call(node: ast.AST) -> bool:
    if not isinstance(node, ast.Call):
        return False
    function = node.func
    if isinstance(function, ast.Name):
        return function.id == "print"
    return (
        isinstance(function, ast.Attribute)
        and function.attr == "print"
        and isinstance(function.value, ast.Name)
        and function.value.id == "builtins"
    )


def scan_blender() -> dict[str, int]:
    counts = {}
    for path in sorted(blender_root.rglob("*.py")):
        tree = ast.parse(
            path.read_text(encoding="utf-8-sig"),
            filename=str(path),
            feature_version=(3, 11),
        )
        count = sum(1 for node in ast.walk(tree) if is_print_call(node))
        if count:
            counts[relative(path, blender_root)] = count
    return counts


DEBUG_CALL = re.compile(r"\bDebug\s*\.\s*(?:Log|LogWarning|LogError|LogException)\s*\(")


def scan_unity() -> dict[str, int]:
    counts = {}
    for path in sorted(unity_root.rglob("*.cs")):
        count = len(DEBUG_CALL.findall(path.read_text(encoding="utf-8-sig")))
        if count:
            counts[relative(path, unity_root)] = count
    return counts


def without_allowed(counts: dict[str, int], allowed: set[str]) -> dict[str, int]:
    return {path: count for path, count in counts.items() if path not in allowed}


def compare(name: str, expected: dict, actual: dict) -> list[str]:
    issues = []
    for path in sorted(set(expected) | set(actual)):
        expected_count = int(expected.get(path, 0))
        actual_count = int(actual.get(path, 0))
        if actual_count > expected_count:
            issues.append(
                f"{name}_logging_debt_increased path={path} expected={expected_count} actual={actual_count}"
            )
        elif actual_count < expected_count:
            issues.append(
                f"{name}_logging_debt_reduced_update_baseline path={path} expected={expected_count} actual={actual_count}"
            )
    return issues


debt = json.loads(debt_path.read_text(encoding="utf-8-sig"))
if debt.get("schemaVersion") != 1:
    raise RuntimeError(f"unsupported_logging_debt_schema:{debt.get('schemaVersion')}")

all_blender = scan_blender()
all_unity = scan_unity()
actual_blender = without_allowed(all_blender, set(allowed_blender))
actual_unity = without_allowed(all_unity, set(allowed_unity))
issues = []
issues.extend(compare("blender", debt.get("blender") or {}, actual_blender))
issues.extend(compare("unity", debt.get("unity") or {}, actual_unity))

for path, expected_count in sorted(allowed_blender.items()):
    actual_count = all_blender.get(path, 0)
    if actual_count != expected_count:
        issues.append(
            f"blender_allowed_console_sink_changed path={path} expected={expected_count} actual={actual_count}"
        )
for path, expected_count in sorted(allowed_unity.items()):
    actual_count = all_unity.get(path, 0)
    if actual_count != expected_count:
        issues.append(
            f"unity_allowed_console_sink_changed path={path} expected={expected_count} actual={actual_count}"
        )

print(f"blender_direct_print_debt={sum(actual_blender.values())} files={len(actual_blender)}")
print(f"unity_direct_debug_debt={sum(actual_unity.values())} files={len(actual_unity)}")
print(f"blender_facade_console_calls={sum(all_blender.get(path, 0) for path in allowed_blender)}")
print(f"unity_facade_console_calls={sum(all_unity.get(path, 0) for path in allowed_unity)}")
if issues:
    for issue in issues:
        print(issue)
    raise SystemExit(1)
print("logging_policy_check_ok")
'@

$tempScript = New-TemporaryFile
try {
    Set-Content -LiteralPath $tempScript.FullName -Value $script -NoNewline
    & uv run python $tempScript.FullName $BlenderRoot $UnityScriptsRoot $DebtFile
    if ($LASTEXITCODE -ne 0) {
        throw "Logging policy check failed with exit code $LASTEXITCODE"
    }
}
finally {
    Remove-Item -LiteralPath $tempScript.FullName -Force -ErrorAction SilentlyContinue
}
