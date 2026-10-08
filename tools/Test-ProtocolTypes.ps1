param(
    [string]$BlenderRoot,
    [string]$UnityScriptsRoot,
    [switch]$VerboseTypes
)

$ErrorActionPreference = 'Stop'

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

function New-StringSet {
    return New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::Ordinal)
}

function Test-ProtocolType {
    param([string]$Value)

    return (
        $Value -like 'scene_sync.*' -or
        $Value -like 'asset_bridge.*' -or
        $Value -like 'large_payload.*' -or
        $Value -like 'unity_mesh.*' -or
        $Value -eq 'session_hello' -or
        $Value -eq 'session_ack' -or
        $Value -like 'session.handshake_*' -or
        $Value -eq 'unity_rig_v1' -or
        $Value -eq 'asset_bridge_import_mvp'
    )
}

function Add-StringLiterals {
    param(
        [string]$Path,
        [System.Collections.Generic.HashSet[string]]$Target
    )

    $content = Get-Content -LiteralPath $Path -Raw
    $protocolLiteralPattern = '["''](scene_sync\.[^"'']+|asset_bridge\.[^"'']+|large_payload\.[^"'']+|unity_mesh\.[^"'']+|session_hello|session_ack|session\.handshake_[^"'']+|unity_rig_v1|asset_bridge_import_mvp)["'']'
    foreach ($match in [regex]::Matches($content, $protocolLiteralPattern)) {
        $value = $match.Groups[1].Value
        if (Test-ProtocolType -Value $value) {
            [void]$Target.Add($value)
        }
    }
}

function Get-BlenderProtocolTypes {
    param([string]$Root)

    $types = New-StringSet
    Get-ChildItem -LiteralPath $Root -Recurse -File -Filter '*.py' | ForEach-Object {
        Add-StringLiterals -Path $_.FullName -Target $types
    }
    return $types
}

function Get-UnityRouterTypes {
    param([string]$RouterPath)

    $types = New-StringSet
    $content = Get-Content -LiteralPath $RouterPath -Raw
    foreach ($match in [regex]::Matches($content, 'case\s+"([^"]+)"\s*:')) {
        [void]$types.Add($match.Groups[1].Value)
    }
    return $types
}

function Get-UnityRouterPrefixes {
    param([string]$RouterPath)

    $prefixes = New-StringSet
    $content = Get-Content -LiteralPath $RouterPath -Raw
    foreach ($match in [regex]::Matches($content, 'StartsWith\("([^"]+)"')) {
        [void]$prefixes.Add($match.Groups[1].Value)
    }
    return $prefixes
}

function Get-UnitySceneSyncDtoTypes {
    param([string]$MessagePath)

    $types = New-StringSet
    $content = Get-Content -LiteralPath $MessagePath -Raw
    foreach ($match in [regex]::Matches($content, 'public\s+string\s+type;\s*//\s*([^\r\n]+)')) {
        $value = $match.Groups[1].Value.Trim()
        if ($value -like 'scene_sync.*') {
            [void]$types.Add($value)
        }
    }
    return $types
}

function Test-IsRouted {
    param(
        [string]$Type,
        [System.Collections.Generic.HashSet[string]]$RouterTypes,
        [System.Collections.Generic.HashSet[string]]$RouterPrefixes
    )

    if ($RouterTypes.Contains($Type)) {
        return $true
    }

    foreach ($prefix in $RouterPrefixes) {
        if ($Type.StartsWith($prefix, [System.StringComparison]::Ordinal)) {
            return $true
        }
    }
    return $false
}

function Add-Issue {
    param(
        [System.Collections.Generic.List[string]]$Issues,
        [string]$Message
    )

    $Issues.Add($Message)
}

$repoRoot = Get-RepoRoot
if ([string]::IsNullOrWhiteSpace($BlenderRoot)) {
    $BlenderRoot = Join-Path $repoRoot 'blender_addon\blendersync_vnext\blender'
}
if ([string]::IsNullOrWhiteSpace($UnityScriptsRoot)) {
    $UnityScriptsRoot = Join-Path $repoRoot 'unity\TriSync\Scripts'
}

$BlenderRoot = (Resolve-Path -LiteralPath $BlenderRoot).Path
$UnityScriptsRoot = (Resolve-Path -LiteralPath $UnityScriptsRoot).Path
$routerPath = Join-Path $UnityScriptsRoot 'SessionCore\SessionMessageRouter.cs'
$messagePath = Join-Path $UnityScriptsRoot 'SceneSyncCore\SceneSyncMessage.cs'

$blenderTypes = Get-BlenderProtocolTypes -Root $BlenderRoot
$routerTypes = Get-UnityRouterTypes -RouterPath $routerPath
$routerPrefixes = Get-UnityRouterPrefixes -RouterPath $routerPath
$dtoTypes = Get-UnitySceneSyncDtoTypes -MessagePath $messagePath

$internalBlenderTypes = New-StringSet
[void]$internalBlenderTypes.Add('scene_sync.mesh_uv_no_change_v1')
[void]$internalBlenderTypes.Add('scene_sync.mesh_positions_no_change_v1')
[void]$internalBlenderTypes.Add('scene_sync.mesh_ref_usage_v1')
[void]$internalBlenderTypes.Add('scene_sync.mesh_content_fingerprint_request_v1')
[void]$internalBlenderTypes.Add('unity_mesh.import_v1')
[void]$internalBlenderTypes.Add('unity_mesh.import_file_v1')
[void]$internalBlenderTypes.Add('unity_mesh.import_binary_file_v1')

$issues = New-Object System.Collections.Generic.List[string]

foreach ($type in ($blenderTypes | Sort-Object)) {
    if ($internalBlenderTypes.Contains($type)) {
        continue
    }

    if (!(Test-IsRouted -Type $type -RouterTypes $routerTypes -RouterPrefixes $routerPrefixes)) {
        Add-Issue -Issues $issues -Message "blender_type_not_routed $type"
    }
}

foreach ($type in ($routerTypes | Sort-Object)) {
    if ($type -like 'scene_sync.*' -and !$dtoTypes.Contains($type)) {
        Add-Issue -Issues $issues -Message "router_scene_sync_type_missing_dto $type"
    }
}

foreach ($type in ($dtoTypes | Sort-Object)) {
    if (!(Test-IsRouted -Type $type -RouterTypes $routerTypes -RouterPrefixes $routerPrefixes)) {
        Add-Issue -Issues $issues -Message "dto_scene_sync_type_not_routed $type"
    }
}

Write-Host "blender_protocol_types=$($blenderTypes.Count)"
Write-Host "unity_router_types=$($routerTypes.Count)"
Write-Host "unity_router_prefixes=$($routerPrefixes.Count)"
Write-Host "unity_scene_sync_dto_types=$($dtoTypes.Count)"
Write-Host "internal_blender_types=$($internalBlenderTypes.Count)"

if ($VerboseTypes) {
    Write-Host "blender_types:"
    $blenderTypes | Sort-Object | ForEach-Object { Write-Host "  $_" }
    Write-Host "router_types:"
    $routerTypes | Sort-Object | ForEach-Object { Write-Host "  $_" }
    Write-Host "dto_types:"
    $dtoTypes | Sort-Object | ForEach-Object { Write-Host "  $_" }
}

if ($issues.Count -gt 0) {
    Write-Host "protocol_type_issues=$($issues.Count)"
    $issues | ForEach-Object { Write-Host $_ }
    throw "Protocol type consistency check failed."
}

Write-Host "protocol_type_check_ok"
