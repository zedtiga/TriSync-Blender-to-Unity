param(
    [string]$UnityProjectRoot,
    [string]$UnityEditorPath,
    [string]$TestSourceRoot,
    [string]$TestRuntimeRoot,
    [string]$ResultDirectory,
    [ValidateRange(1, 86400)]
    [int]$TimeoutSeconds = 600
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($UnityProjectRoot)) {
    throw 'Pass -UnityProjectRoot with the path to your Unity URP test project, and -UnityEditorPath if the editor is not registered with Unity Hub.'
}
if (!(Test-Path -LiteralPath $UnityProjectRoot -PathType Container)) {
    throw "Unity project directory not found: $UnityProjectRoot. Pass -UnityProjectRoot with an existing project directory."
}

. (Join-Path $PSScriptRoot 'ProcessHelpers.ps1')

function Write-UnityLogTail {
    param([string]$Path)

    if (Test-Path -LiteralPath $Path -PathType Leaf) {
        Get-Content -LiteralPath $Path -Tail 80
    }
}

function Get-RequiredNonNegativeTestCount {
    param(
        [System.Xml.XmlElement]$TestRun,
        [string]$Name
    )

    $raw = $TestRun.GetAttribute($Name)
    $value = 0
    if (![int]::TryParse($raw, [ref]$value) -or $value -lt 0) {
        throw "Unity EditMode results contain an invalid '$Name' count: '$raw'"
    }
    return $value
}

function Read-UnityEditModeTestSummary {
    param([string]$Path)

    try {
        [xml]$results = Get-Content -LiteralPath $Path -Raw
    }
    catch {
        throw "Unity EditMode results XML could not be parsed: $($_.Exception.Message)"
    }

    $testRun = $results.SelectSingleNode('/test-run')
    if ($null -eq $testRun) {
        throw "Unity EditMode results XML is missing the 'test-run' root node."
    }

    $result = $testRun.GetAttribute('result')
    if ([string]::IsNullOrWhiteSpace($result)) {
        throw "Unity EditMode results are missing the 'result' attribute."
    }

    return [pscustomobject]@{
        Result = $result
        Total = Get-RequiredNonNegativeTestCount -TestRun $testRun -Name 'total'
        Passed = Get-RequiredNonNegativeTestCount -TestRun $testRun -Name 'passed'
        Failed = Get-RequiredNonNegativeTestCount -TestRun $testRun -Name 'failed'
        Skipped = Get-RequiredNonNegativeTestCount -TestRun $testRun -Name 'skipped'
    }
}

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

function Resolve-UnityEditorPath {
    param(
        [string]$ProjectRoot,
        [string]$ExplicitPath
    )

    if (![string]::IsNullOrWhiteSpace($ExplicitPath)) {
        return (Resolve-Path -LiteralPath $ExplicitPath).Path
    }

    $projectVersionPath = Join-Path $ProjectRoot 'ProjectSettings\ProjectVersion.txt'
    $versionLine = Get-Content -LiteralPath $projectVersionPath | Where-Object { $_ -like 'm_EditorVersion:*' } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($versionLine)) {
        throw "Unity editor version not found in $projectVersionPath"
    }
    $version = ($versionLine -split ':', 2)[1].Trim()

    $installRootFile = Join-Path $env:APPDATA 'UnityHub\secondaryInstallPath.json'
    if (Test-Path -LiteralPath $installRootFile) {
        $installRoot = Get-Content -LiteralPath $installRootFile -Raw | ConvertFrom-Json
        $candidate = Join-Path $installRoot "$version\Editor\Unity.exe"
        if (Test-Path -LiteralPath $candidate -PathType Leaf) {
            return (Resolve-Path -LiteralPath $candidate).Path
        }
    }

    throw "Unity $version was not found. Pass -UnityEditorPath explicitly."
}

function Sync-TestFiles {
    param(
        [string]$Source,
        [string]$Destination
    )

    if (!(Test-Path -LiteralPath $Destination)) {
        New-Item -ItemType Directory -Path $Destination | Out-Null
    }

    $sourceFiles = @(Get-ChildItem -LiteralPath $Source -Recurse -File)
    if ($sourceFiles.Count -eq 0) {
        throw "No Unity EditMode test files found under $Source"
    }
    $sourcePaths = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($file in $sourceFiles) {
        $relativePath = Convert-ToRelativePath -Root $Source -Path $file.FullName
        [void]$sourcePaths.Add($relativePath)
        $target = Join-Path $Destination $relativePath
        $targetDirectory = Split-Path -Parent $target
        if (!(Test-Path -LiteralPath $targetDirectory)) {
            New-Item -ItemType Directory -Path $targetDirectory | Out-Null
        }
        Copy-Item -LiteralPath $file.FullName -Destination $target -Force
    }

    foreach ($file in @(Get-ChildItem -LiteralPath $Destination -Recurse -File)) {
        if ($file.Extension -eq '.meta') {
            $assetPath = $file.FullName.Substring(0, $file.FullName.Length - 5)
            if (!(Test-Path -LiteralPath $assetPath) -and (Test-Path -LiteralPath $file.FullName)) {
                Remove-Item -LiteralPath $file.FullName -Force
            }
            continue
        }

        $relativePath = Convert-ToRelativePath -Root $Destination -Path $file.FullName
        if (!$sourcePaths.Contains($relativePath)) {
            Remove-Item -LiteralPath $file.FullName -Force
            $metaPath = $file.FullName + '.meta'
            if (Test-Path -LiteralPath $metaPath) {
                Remove-Item -LiteralPath $metaPath -Force
            }
        }
    }

    return $sourceFiles.Count
}

$repoRoot = Get-RepoRoot
$UnityProjectRoot = (Resolve-Path -LiteralPath $UnityProjectRoot).Path
if ([string]::IsNullOrWhiteSpace($TestSourceRoot)) {
    $TestSourceRoot = Join-Path $repoRoot 'tests\unity'
}
$TestSourceRoot = (Resolve-Path -LiteralPath $TestSourceRoot).Path
if ([string]::IsNullOrWhiteSpace($TestRuntimeRoot)) {
    $TestRuntimeRoot = Join-Path $UnityProjectRoot 'Assets\BlenderSyncVNextTests'
}
$assetsRoot = [System.IO.Path]::GetFullPath((Join-Path $UnityProjectRoot 'Assets')).TrimEnd('\', '/')
$TestRuntimeRoot = [System.IO.Path]::GetFullPath($TestRuntimeRoot).TrimEnd('\', '/')
if (
    $TestRuntimeRoot.Equals($assetsRoot, [System.StringComparison]::OrdinalIgnoreCase) -or
    !$TestRuntimeRoot.StartsWith($assetsRoot + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
) {
    throw "Test runtime root must be a child of the Unity project's Assets folder: $TestRuntimeRoot"
}

$unityPath = Resolve-UnityEditorPath -ProjectRoot $UnityProjectRoot -ExplicitPath $UnityEditorPath
$syncedFiles = Sync-TestFiles -Source $TestSourceRoot -Destination $TestRuntimeRoot

if ([string]::IsNullOrWhiteSpace($ResultDirectory)) {
    $ResultDirectory = Join-Path $repoRoot 'tmp\unity-editmode-tests'
}
elseif (![System.IO.Path]::IsPathRooted($ResultDirectory)) {
    $ResultDirectory = Join-Path $repoRoot $ResultDirectory
}
$ResultDirectory = [System.IO.Path]::GetFullPath($ResultDirectory)
if (!(Test-Path -LiteralPath $ResultDirectory)) {
    New-Item -ItemType Directory -Path $ResultDirectory | Out-Null
}
$resultPath = Join-Path $ResultDirectory 'results.xml'
$logPath = Join-Path $ResultDirectory 'unity.log'
foreach ($artifactPath in @($resultPath, $logPath)) {
    if (Test-Path -LiteralPath $artifactPath) {
        Remove-Item -LiteralPath $artifactPath -Force
    }
}

Write-Host "unity_editor=$unityPath"
Write-Host "unity_project=$UnityProjectRoot"
Write-Host "test_source=$TestSourceRoot"
Write-Host "test_runtime=$TestRuntimeRoot"
Write-Host "test_files=$syncedFiles"
Write-Host "result_directory=$ResultDirectory"

$unityArguments = @(
    '-batchmode',
    '-nographics',
    '-projectPath', $UnityProjectRoot,
    '-runTests',
    '-testPlatform', 'EditMode',
    '-testFilter', 'BlenderSyncVNext.Tests',
    '-testResults', $resultPath,
    '-logFile', $logPath
)
$unityArgumentLine = (@($unityArguments | ForEach-Object { Convert-ToProcessArgument -Value ([string]$_) }) -join ' ')
$unityProcess = Start-Process -FilePath $unityPath -ArgumentList $unityArgumentLine -PassThru -WindowStyle Hidden
try {
    $unityExitCode = Wait-ChildProcess `
        -Process $unityProcess `
        -TimeoutSeconds $TimeoutSeconds `
        -Description 'Unity EditMode test process'
}
catch {
    Write-UnityLogTail -Path $logPath
    throw
}

if (!(Test-Path -LiteralPath $resultPath -PathType Leaf)) {
    Write-UnityLogTail -Path $logPath
    throw "Unity did not write EditMode test results. Exit code: $unityExitCode"
}

try {
    $summary = Read-UnityEditModeTestSummary -Path $resultPath
}
catch {
    Write-UnityLogTail -Path $logPath
    throw
}

Write-Host "unity_editmode_result=$($summary.Result) total=$($summary.Total) passed=$($summary.Passed) failed=$($summary.Failed) skipped=$($summary.Skipped)"

$failureReason = $null
if ($summary.Total -le 0) {
    $failureReason = "Unity EditMode test discovery returned zero tests."
}
elseif (![string]::Equals($summary.Result, 'Passed', [System.StringComparison]::OrdinalIgnoreCase)) {
    $failureReason = "Unity EditMode test run result was '$($summary.Result)', expected 'Passed'."
}
elseif ($unityExitCode -ne 0 -or $summary.Failed -ne 0) {
    $failureReason = "Unity EditMode tests failed with exit code $unityExitCode and failed count $($summary.Failed)."
}

if ($null -ne $failureReason) {
    Write-UnityLogTail -Path $logPath
    throw $failureReason
}

Write-Host "unity_editmode_tests_ok"
