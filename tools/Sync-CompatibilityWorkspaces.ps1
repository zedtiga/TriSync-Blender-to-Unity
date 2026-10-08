[CmdletBinding()]
param(
    [string]$MatrixPath,
    [ValidateSet('All', 'Blender', 'Unity')]
    [string]$Product = 'All',
    [switch]$VerifyOnly,
    [switch]$PreferProcessEnvironment
)

$ErrorActionPreference = 'Stop'

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

function Get-ConfiguredPath {
    param([string]$EnvironmentVariable)

    $scopes = if ($PreferProcessEnvironment) {
        @('Process', 'User', 'Machine')
    }
    else {
        @('User', 'Process', 'Machine')
    }
    foreach ($scope in $scopes) {
        $value = [string][Environment]::GetEnvironmentVariable($EnvironmentVariable, $scope)
        if (![string]::IsNullOrWhiteSpace($value)) {
            return $value.Trim()
        }
    }
    throw "Required environment variable is not configured: $EnvironmentVariable"
}

function Invoke-Script {
    param(
        [string]$Path,
        [string[]]$Arguments,
        [string]$Description
    )

    & powershell -NoProfile -ExecutionPolicy Bypass -File $Path @Arguments
    if ($LASTEXITCODE -ne 0) {
        throw "$Description failed with exit code $LASTEXITCODE."
    }
}

function Assert-VersionPrefix {
    param(
        [string]$Actual,
        [string]$Expected,
        [string]$Context
    )

    if (
        ![string]::Equals($Actual, $Expected, [System.StringComparison]::OrdinalIgnoreCase) -and
        !$Actual.StartsWith($Expected + '.', [System.StringComparison]::OrdinalIgnoreCase)
    ) {
        throw "$Context version mismatch: expected $Expected.x, found '$Actual'."
    }
}

function Get-UnityProjectVersion {
    param([string]$ProjectRoot)

    $path = Join-Path $ProjectRoot 'ProjectSettings\ProjectVersion.txt'
    $line = Get-Content -LiteralPath $path | Where-Object { $_ -like 'm_EditorVersion:*' } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($line)) {
        throw "Unity project version is missing from $path"
    }
    return ($line -split ':', 2)[1].Trim()
}

function Get-UnityUrpVersion {
    param([string]$ProjectRoot)

    $path = Join-Path $ProjectRoot 'Packages\manifest.json'
    $manifest = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    $version = [string]$manifest.dependencies.'com.unity.render-pipelines.universal'
    if ([string]::IsNullOrWhiteSpace($version)) {
        throw "Unity project does not declare URP in $path"
    }
    return $version.Trim()
}

function Invoke-BlenderHelper {
    param(
        [string]$Executable,
        [string]$HelperPath,
        [string[]]$Arguments,
        [string]$Description
    )

    $priorErrorActionPreference = $ErrorActionPreference
    try {
        $ErrorActionPreference = 'Continue'
        $output = @(& $Executable --background --python-exit-code 1 --python $HelperPath -- @Arguments 2>&1)
        $exitCode = $LASTEXITCODE
    }
    finally {
        $ErrorActionPreference = $priorErrorActionPreference
    }
    foreach ($line in $output) {
        Write-Host $line
    }
    if ($exitCode -ne 0) {
        throw "$Description failed with exit code $exitCode."
    }
    return ,$output
}

function Get-BlenderUserAddonRoot {
    param(
        [string]$Executable,
        [string]$HelperPath
    )

    $marker = 'BLENDERSYNC_USER_ADDONS='
    $output = Invoke-BlenderHelper `
        -Executable $Executable `
        -HelperPath $HelperPath `
        -Arguments @('--mode', 'resolve-user-addon') `
        -Description "Resolving Blender's normal add-on directory"
    $result = @($output | ForEach-Object { [string]$_ } | Where-Object { $_.StartsWith($marker) }) | Select-Object -Last 1
    if ([string]::IsNullOrWhiteSpace($result)) {
        throw "Blender did not report its normal add-on directory."
    }
    return $result.Substring($marker.Length).Trim()
}

function Get-NormalizedFullPath {
    param([string]$Path)

    return [System.IO.Path]::GetFullPath($Path).TrimEnd('\', '/')
}

function Test-PathWithinRoot {
    param(
        [string]$Path,
        [string]$Root
    )

    $pathFull = Get-NormalizedFullPath -Path $Path
    $rootFull = Get-NormalizedFullPath -Path $Root
    return (
        [string]::Equals($pathFull, $rootFull, [System.StringComparison]::OrdinalIgnoreCase) -or
        $pathFull.StartsWith($rootFull + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
    )
}

function Set-BlenderNormalAddonLink {
    param(
        [string]$Executable,
        [string]$RuntimeRoot,
        [string]$HelperPath
    )

    $runtimeRoot = Get-NormalizedFullPath -Path (Resolve-Path -LiteralPath $RuntimeRoot).Path
    $userAddonRoot = Get-BlenderUserAddonRoot -Executable $Executable -HelperPath $HelperPath
    if ([string]::IsNullOrWhiteSpace($userAddonRoot)) {
        throw "Blender returned an empty normal add-on directory."
    }
    [System.IO.Directory]::CreateDirectory($userAddonRoot) | Out-Null
    $userAddonRoot = Get-NormalizedFullPath -Path (Resolve-Path -LiteralPath $userAddonRoot).Path
    $linkPath = Join-Path $userAddonRoot 'blendersync_vnext'
    if (!(Test-PathWithinRoot -Path $linkPath -Root $userAddonRoot)) {
        throw "Refusing to manage Blender add-on path outside '$userAddonRoot': $linkPath"
    }

    if (Test-Path -LiteralPath $linkPath) {
        $item = Get-Item -LiteralPath $linkPath -Force
        $targets = @($item.Target | ForEach-Object { [string]$_ })
        $targetMatches = @($targets | Where-Object {
            ![string]::IsNullOrWhiteSpace($_) -and
            [string]::Equals(
                (Get-NormalizedFullPath -Path $_),
                $runtimeRoot,
                [System.StringComparison]::OrdinalIgnoreCase
            )
        }).Count -gt 0
        if ($item.LinkType -in @('Junction', 'SymbolicLink') -and $targetMatches) {
            Write-Host "blender_normal_addon_link_current path=$linkPath target=$runtimeRoot"
            return $linkPath
        }

        $versionConfigRoot = Split-Path -Parent (Split-Path -Parent $userAddonRoot)
        $backupRoot = Join-Path $versionConfigRoot 'blendersync-managed-backups'
        [System.IO.Directory]::CreateDirectory($backupRoot) | Out-Null
        $backupPath = Join-Path $backupRoot ("blendersync_vnext-" + (Get-Date -Format 'yyyyMMdd-HHmmssfff'))
        if (
            !(Test-PathWithinRoot -Path $linkPath -Root $versionConfigRoot) -or
            !(Test-PathWithinRoot -Path $backupPath -Root $versionConfigRoot)
        ) {
            throw "Refusing to back up Blender add-on outside '$versionConfigRoot'."
        }
        Move-Item -LiteralPath $linkPath -Destination $backupPath
        Write-Host "blender_normal_addon_backup source=$linkPath destination=$backupPath"
    }

    $link = New-Item -ItemType Junction -Path $linkPath -Target $runtimeRoot
    if ($null -eq $link -or !(Test-Path -LiteralPath $linkPath -PathType Container)) {
        throw "Could not create Blender normal add-on link: $linkPath -> $runtimeRoot"
    }
    Write-Host "blender_normal_addon_link_ok path=$linkPath target=$runtimeRoot"
    return $linkPath
}

function Enable-BlenderAddonForNormalLaunch {
    param(
        [string]$Executable,
        [string]$RuntimeRoot,
        [string]$HelperPath
    )

    $null = Invoke-BlenderHelper `
        -Executable $Executable `
        -HelperPath $HelperPath `
        -Arguments @('--mode', 'enable', '--runtime-root', $RuntimeRoot) `
        -Description "Persisting TriSync add-on preference for '$Executable'"

    $null = Invoke-BlenderHelper `
        -Executable $Executable `
        -HelperPath $HelperPath `
        -Arguments @('--mode', 'verify', '--runtime-root', $RuntimeRoot) `
        -Description "Verifying TriSync normal startup for '$Executable'"
}

function Write-BlenderLauncher {
    param(
        [string]$ApplicationRoot,
        [string]$RuntimeRoot,
        [string]$SourceRoot
    )

    $launcherPath = Join-Path $ApplicationRoot 'BlenderSync Test.cmd'
    $launcher = @(
        '@echo off',
        'setlocal',
        'start "" "%~dp0blender.exe" --addons blendersync_vnext %*'
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($launcherPath, $launcher + "`r`n", [System.Text.Encoding]::ASCII)

    $revision = (& git -C $SourceRoot rev-parse --short HEAD 2>$null | Select-Object -First 1)
    if ([string]::IsNullOrWhiteSpace($revision)) {
        $revision = 'unknown'
    }
    $status = @(& git -C $SourceRoot status --porcelain 2>$null)
    if ($status.Count -gt 0) {
        $revision += '+dirty'
    }
    $infoPath = Join-Path $ApplicationRoot 'BLENDERSYNC_MANAGED_WORKSPACE.txt'
    $info = @(
        'TriSync managed compatibility workspace',
        "Source: $SourceRoot",
        "Revision: $revision",
        "Launcher: $launcherPath",
        "Addon: $RuntimeRoot",
        'Normal blender.exe startup uses this application-local add-on and the saved enabled preference.'
    ) -join "`r`n"
    [System.IO.File]::WriteAllText($infoPath, $info + "`r`n", [System.Text.Encoding]::UTF8)
    return $launcherPath
}

function Sync-BlenderProfile {
    param(
        [object]$Profile,
        [string]$RepoRoot,
        [switch]$VerifyOnly
    )

    $environmentVariable = [string]$Profile.executableEnvironmentVariable
    $executable = Get-ConfiguredPath -EnvironmentVariable $environmentVariable
    if (!(Test-Path -LiteralPath $executable -PathType Leaf)) {
        throw "$environmentVariable does not point to a Blender executable: $executable"
    }
    $actualVersion = [string](Get-Item -LiteralPath $executable).VersionInfo.ProductVersion
    Assert-VersionPrefix `
        -Actual $actualVersion `
        -Expected ([string]$Profile.version) `
        -Context "Blender executable '$($Profile.id)'"

    $applicationRoot = Split-Path -Parent (Resolve-Path -LiteralPath $executable).Path
    $versionRoot = Join-Path $applicationRoot ([string]$Profile.version)
    if (!(Test-Path -LiteralPath $versionRoot -PathType Container)) {
        throw "Blender application version directory is missing for '$($Profile.id)': $versionRoot"
    }
    $runtimeRoot = Join-Path $versionRoot 'scripts\addons\blendersync_vnext'
    $helperPath = Join-Path $RepoRoot 'tools\blender_normal_addon_setup.py'
    if (!(Test-Path -LiteralPath $helperPath -PathType Leaf)) {
        throw "Blender normal add-on helper is missing: $helperPath"
    }
    $syncScript = Join-Path $RepoRoot 'tools\Sync-BlenderAddon.ps1'
    $arguments = @(
        '-RuntimeRoot', $runtimeRoot,
        '-RemoveStaleFiles'
    )
    if ($VerifyOnly) {
        $arguments += '-SkipSync'
    }
    else {
        $arguments += '-CleanPythonCache'
    }
    Invoke-Script `
        -Path $syncScript `
        -Arguments $arguments `
        -Description "Blender workspace sync '$($Profile.id)'"

    $launcherPath = Join-Path $applicationRoot 'BlenderSync Test.cmd'
    if ($VerifyOnly) {
        if (!(Test-Path -LiteralPath $launcherPath -PathType Leaf)) {
            throw "Blender test launcher is missing for '$($Profile.id)': $launcherPath"
        }
    }
    else {
        $launcherPath = Write-BlenderLauncher `
            -ApplicationRoot $applicationRoot `
            -RuntimeRoot $runtimeRoot `
            -SourceRoot $RepoRoot
        $normalAddonLink = Set-BlenderNormalAddonLink `
            -Executable $executable `
            -RuntimeRoot $runtimeRoot `
            -HelperPath $helperPath
        Enable-BlenderAddonForNormalLaunch `
            -Executable $executable `
            -RuntimeRoot $runtimeRoot `
            -HelperPath $helperPath
    }

    Write-Host "compatibility_workspace_blender_ok id=$($Profile.id) executable=$executable addon=$runtimeRoot normalAddonLink=$normalAddonLink normalLaunchEnabled=$([bool](!$VerifyOnly)) launcher=$launcherPath"
}

function Sync-UnityProfile {
    param(
        [object]$Profile,
        [string]$RepoRoot,
        [switch]$VerifyOnly
    )

    $environmentVariable = [string]$Profile.projectEnvironmentVariable
    $projectRoot = Get-ConfiguredPath -EnvironmentVariable $environmentVariable
    if (!(Test-Path -LiteralPath $projectRoot -PathType Container)) {
        throw "$environmentVariable does not point to a Unity project: $projectRoot"
    }
    $projectRoot = (Resolve-Path -LiteralPath $projectRoot).Path
    $editorEnvironmentVariable = [string]$Profile.editorEnvironmentVariable
    $editor = Get-ConfiguredPath -EnvironmentVariable $editorEnvironmentVariable
    if (!(Test-Path -LiteralPath $editor -PathType Leaf)) {
        throw "$editorEnvironmentVariable does not point to a Unity editor: $editor"
    }
    $editorVersion = [string](Get-Item -LiteralPath $editor).VersionInfo.ProductVersion
    $projectVersion = Get-UnityProjectVersion -ProjectRoot $projectRoot
    $urpVersion = Get-UnityUrpVersion -ProjectRoot $projectRoot
    Assert-VersionPrefix -Actual $editorVersion -Expected ([string]$Profile.version) -Context "Unity editor '$($Profile.id)'"
    Assert-VersionPrefix -Actual $projectVersion -Expected ([string]$Profile.version) -Context "Unity project '$($Profile.id)'"
    Assert-VersionPrefix -Actual $urpVersion -Expected ([string]$Profile.urpVersion) -Context "Unity URP '$($Profile.id)'"
    $runtimeRoot = Join-Path $projectRoot 'Assets\TriSync'
    $syncScript = Join-Path $RepoRoot 'tools\Sync-UnityRuntime.ps1'
    $arguments = @(
        '-RuntimeRoot', $runtimeRoot,
        '-UnityProjectRoot', $projectRoot,
        '-SkipProjectPatch',
        '-SkipBuild',
        '-RemoveStaleFiles'
    )
    if ($VerifyOnly) {
        $arguments += '-SkipSync'
    }
    Invoke-Script `
        -Path $syncScript `
        -Arguments $arguments `
        -Description "Unity workspace sync '$($Profile.id)'"

    Write-Host "compatibility_workspace_unity_ok id=$($Profile.id) editor=$editor project=$projectRoot urp=$urpVersion runtime=$runtimeRoot"
}

$repoRoot = Get-RepoRoot
if ([string]::IsNullOrWhiteSpace($MatrixPath)) {
    $MatrixPath = Join-Path $repoRoot 'tools\compatibility-matrix.json'
}
$MatrixPath = (Resolve-Path -LiteralPath $MatrixPath).Path
$matrix = Get-Content -LiteralPath $MatrixPath -Raw | ConvertFrom-Json
$profiles = @($matrix.profiles | Where-Object {
    $Product -eq 'All' -or [string]::Equals([string]$_.product, $Product, [System.StringComparison]::OrdinalIgnoreCase)
})
if ($profiles.Count -eq 0) {
    throw 'No compatibility profiles matched the requested product.'
}

$blockedProcessNames = switch ($Product) {
    'Blender' { @('blender') }
    'Unity' { @('Unity') }
    default { @('blender', 'Unity') }
}
$runningEditors = @(Get-Process -Name $blockedProcessNames -ErrorAction SilentlyContinue)
if ($runningEditors.Count -gt 0 -and !$VerifyOnly) {
    $detail = $runningEditors | ForEach-Object { "$($_.ProcessName):$($_.Id)" }
    throw "Close Blender and Unity before synchronizing compatibility workspaces: $($detail -join ', ')"
}

foreach ($profile in $profiles) {
    if ([string]$profile.product -eq 'blender') {
        Sync-BlenderProfile -Profile $profile -RepoRoot $repoRoot -VerifyOnly:$VerifyOnly
    }
    elseif ([string]$profile.product -eq 'unity') {
        Sync-UnityProfile -Profile $profile -RepoRoot $repoRoot -VerifyOnly:$VerifyOnly
    }
}

Write-Host "compatibility_workspaces_ok profiles=$($profiles.Count) product=$Product verifyOnly=$([bool]$VerifyOnly)"
