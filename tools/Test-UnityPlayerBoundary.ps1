param(
    [string]$SourceRoot,
    [string]$ProjectFile,
    [string]$UnityProjectRoot,
    [string]$UnityEditorPath
)

$ErrorActionPreference = 'Stop'

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

function Test-PathWithinRoot {
    param([string]$Root, [string]$Path)

    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    return $pathFull.Equals($rootFull, [System.StringComparison]::OrdinalIgnoreCase) -or
        $pathFull.StartsWith($rootFull + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-NearestAsmdef {
    param([string]$Root, [string]$FilePath)

    $directory = Split-Path -Parent ([System.IO.Path]::GetFullPath($FilePath))
    while (Test-PathWithinRoot -Root $Root -Path $directory) {
        $asmdef = Get-ChildItem -LiteralPath $directory -File -Filter '*.asmdef' | Select-Object -First 1
        if ($null -ne $asmdef) {
            return $asmdef.FullName
        }
        $parent = Split-Path -Parent $directory
        if ([string]::IsNullOrWhiteSpace($parent) -or $parent -eq $directory) {
            break
        }
        $directory = $parent
    }
    return $null
}

$repoRoot = Get-RepoRoot
if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $SourceRoot = Join-Path $repoRoot 'unity\TriSync'
}
$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path

$expectedEditorAssemblies = @('BlenderSyncVNext.Editor', 'BlenderSyncVNext.URP.Editor')
$asmdefByPath = @{}
$asmdefByName = @{}
foreach ($asmdef in Get-ChildItem -LiteralPath $SourceRoot -Recurse -File -Filter '*.asmdef') {
    $parsed = Get-Content -LiteralPath $asmdef.FullName -Raw | ConvertFrom-Json
    if ([string]::IsNullOrWhiteSpace($parsed.name)) {
        throw "Assembly definition has no name: $($asmdef.FullName)"
    }
    $asmdefByPath[$asmdef.FullName] = $parsed
    $asmdefByName[[string]$parsed.name] = $parsed
}

foreach ($name in $expectedEditorAssemblies) {
    if (!$asmdefByName.ContainsKey($name)) {
        throw "Required TriSync Editor assembly definition is missing: $name"
    }
}
foreach ($name in $asmdefByName.Keys) {
    if ($expectedEditorAssemblies -notcontains $name) {
        throw "Unexpected TriSync C# assembly: $name"
    }
}

$sourceCountByAssembly = @{}
$sourceFiles = @(Get-ChildItem -LiteralPath $SourceRoot -Recurse -File -Filter '*.cs')
foreach ($file in $sourceFiles) {
    $asmdefPath = Get-NearestAsmdef -Root $SourceRoot -FilePath $file.FullName
    if ([string]::IsNullOrWhiteSpace($asmdefPath)) {
        throw "Unity product C# file is outside an assembly definition: $($file.FullName)"
    }
    $parsed = $asmdefByPath[$asmdefPath]
    $name = [string]$parsed.name
    if ($expectedEditorAssemblies -notcontains $name) {
        throw "Unity product C# file is not Editor-only: $($file.FullName)"
    }

    $includePlatforms = @($parsed.includePlatforms)
    if ($includePlatforms.Count -ne 1 -or $includePlatforms[0] -ne 'Editor') {
        throw "Editor assembly '$name' must use includePlatforms: [Editor]."
    }
    if (!$sourceCountByAssembly.ContainsKey($name)) {
        $sourceCountByAssembly[$name] = 0
    }
    $sourceCountByAssembly[$name] += 1
}

Write-Host "unity_product_csharp_files=$($sourceFiles.Count)"
foreach ($name in $sourceCountByAssembly.Keys | Sort-Object) {
    Write-Host "unity_assembly_sources name=$name count=$($sourceCountByAssembly[$name])"
}
Write-Host 'unity_product_editor_only_boundary_ok'
