[CmdletBinding()]
param(
    [string]$MatrixPath,
    [string[]]$Profile,
    [ValidateSet('All', 'Blender', 'Unity')]
    [string]$Product = 'All',
    [string]$ArtifactRoot,
    [switch]$ValidateOnly,
    [switch]$List,
    [switch]$AllowMissing,
    [ValidateRange(1, 86400)]
    [int]$BlenderTimeoutSeconds = 300,
    [ValidateRange(1, 86400)]
    [int]$UnityTimeoutSeconds = 600
)

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'ProcessHelpers.ps1')

function Get-RepoRoot {
    $scriptDir = Split-Path -Parent $PSCommandPath
    return (Resolve-Path -LiteralPath (Join-Path $scriptDir '..')).Path
}

function Read-ReleaseMetadata {
    param([string]$RepoRoot)

    $toolPath = Join-Path $RepoRoot 'tools\release_metadata.py'
    $raw = & uv run python $toolPath --repo-root $RepoRoot
    if ($LASTEXITCODE -ne 0) {
        throw "Release metadata validation failed with exit code $LASTEXITCODE."
    }
    try {
        return (($raw -join "`n") | ConvertFrom-Json)
    }
    catch {
        throw "Release metadata output could not be parsed: $($_.Exception.Message)"
    }
}

function Get-PropertyValue {
    param(
        [object]$Object,
        [string]$Name
    )

    if ($null -eq $Object) {
        return $null
    }
    $property = $Object.PSObject.Properties[$Name]
    if ($null -eq $property) {
        return $null
    }
    return $property.Value
}

function Get-RequiredText {
    param(
        [object]$Object,
        [string]$Name,
        [string]$Context
    )

    $value = [string](Get-PropertyValue -Object $Object -Name $Name)
    $value = $value.Trim()
    if ([string]::IsNullOrWhiteSpace($value)) {
        throw "$Context is missing required string '$Name'."
    }
    return $value
}

function Assert-EnvironmentVariableName {
    param(
        [string]$Name,
        [string]$Context
    )

    if ($Name -notmatch '^[A-Z][A-Z0-9_]+$') {
        throw "$Context has invalid environment variable name '$Name'."
    }
}

function Assert-RequiredChecks {
    param(
        [object]$ProfileObject,
        [string[]]$Required,
        [string]$Context
    )

    $checks = @((Get-PropertyValue -Object $ProfileObject -Name 'checks') | ForEach-Object { ([string]$_).Trim() })
    if ($checks.Count -eq 0) {
        throw "$Context must define at least one check."
    }
    if (@($checks | Where-Object { [string]::IsNullOrWhiteSpace($_) }).Count -gt 0) {
        throw "$Context contains an empty check name."
    }
    if (@($checks | Select-Object -Unique).Count -ne $checks.Count) {
        throw "$Context contains a duplicate check name."
    }
    foreach ($requiredCheck in $Required) {
        if ($checks -notcontains $requiredCheck) {
            throw "$Context is missing required check '$requiredCheck'."
        }
    }
}

function Assert-ChecksMatch {
    param(
        [object[]]$Expected,
        [object[]]$Actual,
        [string]$Context
    )

    $expectedChecks = @($Expected | ForEach-Object { ([string]$_).Trim() })
    $actualChecks = @($Actual | ForEach-Object { ([string]$_).Trim() })
    if (@($actualChecks | Where-Object { [string]::IsNullOrWhiteSpace($_) }).Count -gt 0) {
        throw "$Context produced an empty check name."
    }
    if (@($actualChecks | Select-Object -Unique).Count -ne $actualChecks.Count) {
        throw "$Context produced a duplicate check name."
    }

    $missing = @($expectedChecks | Where-Object { $actualChecks -notcontains $_ })
    $unexpected = @($actualChecks | Where-Object { $expectedChecks -notcontains $_ })
    if ($missing.Count -gt 0 -or $unexpected.Count -gt 0) {
        $missingText = if ($missing.Count -eq 0) { '-' } else { $missing -join ',' }
        $unexpectedText = if ($unexpected.Count -eq 0) { '-' } else { $unexpected -join ',' }
        throw "$Context check mismatch: missing=$missingText unexpected=$unexpectedText"
    }
}

function Read-And-ValidateMatrix {
    param([string]$Path)

    try {
        $matrix = Get-Content -LiteralPath $Path -Raw | ConvertFrom-Json
    }
    catch {
        throw "Compatibility matrix JSON could not be parsed: $($_.Exception.Message)"
    }

    $schemaVersion = Get-PropertyValue -Object $matrix -Name 'schemaVersion'
    if ($schemaVersion -ne 1) {
        throw "Compatibility matrix schemaVersion must be 1, found '$schemaVersion'."
    }

    $profiles = @((Get-PropertyValue -Object $matrix -Name 'profiles'))
    if ($profiles.Count -eq 0) {
        throw 'Compatibility matrix must define at least one profile.'
    }

    $ids = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $environmentVariables = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($profileObject in $profiles) {
        $id = Get-RequiredText -Object $profileObject -Name 'id' -Context 'Compatibility profile'
        $context = "Compatibility profile '$id'"
        if (!$ids.Add($id)) {
            throw "Compatibility profile id '$id' is duplicated."
        }
        if ($id -notmatch '^[a-z0-9]+(?:[.-][a-z0-9]+)*$') {
            throw "$context has invalid id syntax."
        }

        $product = (Get-RequiredText -Object $profileObject -Name 'product' -Context $context).ToLowerInvariant()
        [void](Get-RequiredText -Object $profileObject -Name 'label' -Context $context)
        $version = Get-RequiredText -Object $profileObject -Name 'version' -Context $context
        if ($version -notmatch '^\d+\.\d+$') {
            throw "$context has invalid major/minor version '$version'."
        }
        $verificationStatus = Get-RequiredText -Object $profileObject -Name 'verificationStatus' -Context $context
        if ($verificationStatus -notin @('candidate', 'verified', 'current-baseline')) {
            throw "$context has invalid verificationStatus '$verificationStatus'."
        }

        if ($product -eq 'blender') {
            $executableEnvironmentVariable = Get-RequiredText -Object $profileObject -Name 'executableEnvironmentVariable' -Context $context
            Assert-EnvironmentVariableName -Name $executableEnvironmentVariable -Context $context
            if (!$environmentVariables.Add($executableEnvironmentVariable)) {
                throw "$context reuses environment variable '$executableEnvironmentVariable'."
            }
            Assert-RequiredChecks -ProfileObject $profileObject -Context $context -Required @(
                'addon-register',
                'mesh-api',
                'geometry-nodes-api',
                'evaluated-mesh-api',
                'evaluated-material-api',
                'animation-action-api',
                'ui-icon-api',
                'native-probe',
                'addon-unregister'
            )
            continue
        }

        if ($product -eq 'unity') {
            $urpVersion = Get-RequiredText -Object $profileObject -Name 'urpVersion' -Context $context
            if ($urpVersion -notmatch '^\d+\.\d+$') {
                throw "$context has invalid URP major/minor version '$urpVersion'."
            }
            foreach ($propertyName in @('editorEnvironmentVariable', 'projectEnvironmentVariable')) {
                $environmentVariable = Get-RequiredText -Object $profileObject -Name $propertyName -Context $context
                Assert-EnvironmentVariableName -Name $environmentVariable -Context $context
                if (!$environmentVariables.Add($environmentVariable)) {
                    throw "$context reuses environment variable '$environmentVariable'."
                }
            }
            Assert-RequiredChecks -ProfileObject $profileObject -Context $context -Required @(
                'editor-compile',
                'editmode'
            )
            continue
        }

        throw "$context has unsupported product '$product'."
    }

    $interopPairs = @((Get-PropertyValue -Object $matrix -Name 'interopPairs'))
    if ($interopPairs.Count -eq 0) {
        throw 'Compatibility matrix must define at least one interop pair.'
    }
    $pairIds = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $pairEndpoints = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($pair in $interopPairs) {
        $pairId = Get-RequiredText -Object $pair -Name 'id' -Context 'Compatibility interop pair'
        $context = "Compatibility interop pair '$pairId'"
        if (!$pairIds.Add($pairId)) {
            throw "Compatibility interop pair id '$pairId' is duplicated."
        }
        $blenderProfileId = Get-RequiredText -Object $pair -Name 'blenderProfile' -Context $context
        $unityProfileId = Get-RequiredText -Object $pair -Name 'unityProfile' -Context $context
        if (!$pairEndpoints.Add("$blenderProfileId|$unityProfileId")) {
            throw "$context duplicates the endpoint combination '$blenderProfileId' + '$unityProfileId'."
        }
        $blenderProfile = $profiles | Where-Object { [string]::Equals([string]$_.id, $blenderProfileId, [System.StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
        $unityProfile = $profiles | Where-Object { [string]::Equals([string]$_.id, $unityProfileId, [System.StringComparison]::OrdinalIgnoreCase) } | Select-Object -First 1
        if ($null -eq $blenderProfile -or [string]$blenderProfile.product -ne 'blender') {
            throw "$context references invalid Blender profile '$blenderProfileId'."
        }
        if ($null -eq $unityProfile -or [string]$unityProfile.product -ne 'unity') {
            throw "$context references invalid Unity profile '$unityProfileId'."
        }
        $verificationStatus = Get-RequiredText -Object $pair -Name 'verificationStatus' -Context $context
        if ($verificationStatus -notin @('candidate', 'verified', 'current-baseline')) {
            throw "$context has invalid verificationStatus '$verificationStatus'."
        }
        $check = Get-RequiredText -Object $pair -Name 'check' -Context $context
        if ($check -ne 'level-1-smoke') {
            throw "$context must use the 'level-1-smoke' check."
        }
    }

    $currentBlenderProfiles = @($profiles | Where-Object {
        [string]$_.product -eq 'blender' -and [string]$_.verificationStatus -eq 'current-baseline'
    })
    $currentUnityProfiles = @($profiles | Where-Object {
        [string]$_.product -eq 'unity' -and [string]$_.verificationStatus -eq 'current-baseline'
    })
    if ($currentBlenderProfiles.Count -ne 1 -or $currentUnityProfiles.Count -ne 1) {
        throw 'Compatibility matrix must define exactly one current-baseline Blender profile and one current-baseline Unity profile.'
    }
    $currentBlenderId = [string]$currentBlenderProfiles[0].id
    $currentUnityId = [string]$currentUnityProfiles[0].id
    $blenderProfiles = @($profiles | Where-Object { [string]$_.product -eq 'blender' })
    $unityProfiles = @($profiles | Where-Object { [string]$_.product -eq 'unity' })
    $floorBlenderId = [string](@($blenderProfiles | Sort-Object { [version]([string]$_.version) })[0].id)
    $floorUnityId = [string](@($unityProfiles | Sort-Object { [version]([string]$_.version) })[0].id)
    $ceilingBlenderId = [string](@($blenderProfiles | Sort-Object { [version]([string]$_.version) } -Descending)[0].id)
    $ceilingUnityId = [string](@($unityProfiles | Sort-Object { [version]([string]$_.version) } -Descending)[0].id)

    $expectedPairEndpoints = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($blenderProfile in $blenderProfiles) {
        $null = $expectedPairEndpoints.Add("$([string]$blenderProfile.id)|$floorUnityId")
        $null = $expectedPairEndpoints.Add("$([string]$blenderProfile.id)|$ceilingUnityId")
    }
    foreach ($unityProfile in $unityProfiles) {
        $null = $expectedPairEndpoints.Add("$floorBlenderId|$([string]$unityProfile.id)")
        $null = $expectedPairEndpoints.Add("$ceilingBlenderId|$([string]$unityProfile.id)")
    }
    $null = $expectedPairEndpoints.Add("$currentBlenderId|$currentUnityId")

    $missingPairEndpoints = @($expectedPairEndpoints | Where-Object { !$pairEndpoints.Contains($_) })
    $unexpectedPairEndpoints = @($pairEndpoints | Where-Object { !$expectedPairEndpoints.Contains($_) })
    if ($missingPairEndpoints.Count -gt 0 -or $unexpectedPairEndpoints.Count -gt 0) {
        $missingText = if ($missingPairEndpoints.Count -gt 0) { $missingPairEndpoints -join ', ' } else { '(none)' }
        $unexpectedText = if ($unexpectedPairEndpoints.Count -gt 0) { $unexpectedPairEndpoints -join ', ' } else { '(none)' }
        throw "Compatibility interop coverage must match perimeter-plus-current exactly. Missing: $missingText. Unexpected: $unexpectedText."
    }

    $floorPair = @($interopPairs | Where-Object { [string]$_.id -eq 'floor-floor' })
    if ($floorPair.Count -ne 1) {
        throw "Compatibility matrix must define exactly one 'floor-floor' interop pair."
    }
    if (
        [string]$floorPair[0].blenderProfile -ne $floorBlenderId -or
        [string]$floorPair[0].unityProfile -ne $floorUnityId
    ) {
        throw "Compatibility interop pair 'floor-floor' must use '$floorBlenderId' + '$floorUnityId'."
    }

    $ceilingPair = @($interopPairs | Where-Object { [string]$_.id -eq 'ceiling-ceiling' })
    if ($ceilingPair.Count -ne 1) {
        throw "Compatibility matrix must define exactly one 'ceiling-ceiling' interop pair."
    }
    if (
        [string]$ceilingPair[0].blenderProfile -ne $ceilingBlenderId -or
        [string]$ceilingPair[0].unityProfile -ne $ceilingUnityId
    ) {
        throw "Compatibility interop pair 'ceiling-ceiling' must use '$ceilingBlenderId' + '$ceilingUnityId'."
    }

    $currentPair = @($interopPairs | Where-Object { [string]$_.id -eq 'current-current' })
    if ($currentPair.Count -ne 1) {
        throw "Compatibility matrix must define exactly one 'current-current' interop pair."
    }
    if (
        [string]$currentPair[0].blenderProfile -ne $currentBlenderId -or
        [string]$currentPair[0].unityProfile -ne $currentUnityId -or
        [string]$currentPair[0].verificationStatus -ne 'current-baseline'
    ) {
        throw "Compatibility interop pair 'current-current' must use '$currentBlenderId' + '$currentUnityId' with current-baseline status."
    }

    return [pscustomobject]@{
        Matrix = $matrix
        Profiles = $profiles
        InteropPairs = $interopPairs
    }
}

function Get-ConfiguredPath {
    param([string]$EnvironmentVariable)

    return [string][Environment]::GetEnvironmentVariable($EnvironmentVariable, 'Process')
}

function Get-ProfileConfiguration {
    param([object]$ProfileObject)

    $product = ([string]$ProfileObject.product).ToLowerInvariant()
    if ($product -eq 'blender') {
        $environmentVariable = [string]$ProfileObject.executableEnvironmentVariable
        $path = (Get-ConfiguredPath -EnvironmentVariable $environmentVariable).Trim()
        if ([string]::IsNullOrWhiteSpace($path)) {
            return [pscustomobject]@{
                State = 'missing'
                Detail = $environmentVariable
                Executable = $null
                Editor = $null
                Project = $null
            }
        }
        if (!(Test-Path -LiteralPath $path -PathType Leaf)) {
            return [pscustomobject]@{
                State = 'invalid'
                Detail = "$environmentVariable does not point to a file"
                Executable = $path
                Editor = $null
                Project = $null
            }
        }
        return [pscustomobject]@{
            State = 'ready'
            Detail = $environmentVariable
            Executable = (Resolve-Path -LiteralPath $path).Path
            Editor = $null
            Project = $null
        }
    }

    $editorEnvironmentVariable = [string]$ProfileObject.editorEnvironmentVariable
    $projectEnvironmentVariable = [string]$ProfileObject.projectEnvironmentVariable
    $editor = (Get-ConfiguredPath -EnvironmentVariable $editorEnvironmentVariable).Trim()
    $project = (Get-ConfiguredPath -EnvironmentVariable $projectEnvironmentVariable).Trim()
    $missing = @()
    if ([string]::IsNullOrWhiteSpace($editor)) {
        $missing += $editorEnvironmentVariable
    }
    if ([string]::IsNullOrWhiteSpace($project)) {
        $missing += $projectEnvironmentVariable
    }
    if ($missing.Count -gt 0) {
        return [pscustomobject]@{
            State = 'missing'
            Detail = ($missing -join ',')
            Executable = $null
            Editor = $editor
            Project = $project
        }
    }
    if (!(Test-Path -LiteralPath $editor -PathType Leaf)) {
        return [pscustomobject]@{
            State = 'invalid'
            Detail = "$editorEnvironmentVariable does not point to a file"
            Executable = $null
            Editor = $editor
            Project = $project
        }
    }
    if (!(Test-Path -LiteralPath $project -PathType Container)) {
        return [pscustomobject]@{
            State = 'invalid'
            Detail = "$projectEnvironmentVariable does not point to a directory"
            Executable = $null
            Editor = $editor
            Project = $project
        }
    }
    return [pscustomobject]@{
        State = 'ready'
        Detail = "$editorEnvironmentVariable,$projectEnvironmentVariable"
        Executable = $null
        Editor = (Resolve-Path -LiteralPath $editor).Path
        Project = (Resolve-Path -LiteralPath $project).Path
    }
}

function Get-UnityProjectVersion {
    param([string]$ProjectRoot)

    $path = Join-Path $ProjectRoot 'ProjectSettings\ProjectVersion.txt'
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Unity project version file is missing: $path"
    }
    $line = Get-Content -LiteralPath $path | Where-Object { $_ -like 'm_EditorVersion:*' } | Select-Object -First 1
    if ([string]::IsNullOrWhiteSpace($line)) {
        throw "Unity project version is missing from $path"
    }
    return ($line -split ':', 2)[1].Trim()
}

function Get-UnityUrpVersion {
    param([string]$ProjectRoot)

    $path = Join-Path $ProjectRoot 'Packages\manifest.json'
    if (!(Test-Path -LiteralPath $path -PathType Leaf)) {
        throw "Unity package manifest is missing: $path"
    }
    try {
        $manifest = Get-Content -LiteralPath $path -Raw | ConvertFrom-Json
    }
    catch {
        throw "Unity package manifest could not be parsed: $($_.Exception.Message)"
    }
    $dependencies = Get-PropertyValue -Object $manifest -Name 'dependencies'
    $urp = [string](Get-PropertyValue -Object $dependencies -Name 'com.unity.render-pipelines.universal')
    $urp = $urp.Trim()
    if ([string]::IsNullOrWhiteSpace($urp)) {
        throw "Unity project does not declare com.unity.render-pipelines.universal in $path"
    }
    return $urp
}

function Assert-VersionPrefix {
    param(
        [string]$Actual,
        [string]$ExpectedMajorMinor,
        [string]$Context
    )

    if (!$Actual.StartsWith($ExpectedMajorMinor + '.', [System.StringComparison]::OrdinalIgnoreCase)) {
        throw "$Context version mismatch: expected $ExpectedMajorMinor.x, found '$Actual'."
    }
}

function Write-BlenderSmokeLogTails {
    param(
        [string]$StandardOutputPath,
        [string]$StandardErrorPath
    )

    foreach ($log in @(
        [pscustomobject]@{ Name = 'stdout'; Path = $StandardOutputPath },
        [pscustomobject]@{ Name = 'stderr'; Path = $StandardErrorPath }
    )) {
        if ((Test-Path -LiteralPath $log.Path -PathType Leaf) -and (Get-Item -LiteralPath $log.Path).Length -gt 0) {
            Write-Host "blender_smoke_$($log.Name)_tail path=$($log.Path)"
            Get-Content -LiteralPath $log.Path -Tail 80
        }
    }
}

function Invoke-BlenderProfile {
    param(
        [object]$ProfileObject,
        [object]$Configuration,
        [object]$ReleaseMetadata,
        [string]$RepoRoot,
        [string]$ProfileArtifactRoot,
        [int]$TimeoutSeconds
    )

    if (!(Test-Path -LiteralPath $ProfileArtifactRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $ProfileArtifactRoot | Out-Null
    }
    $resultPath = Join-Path $ProfileArtifactRoot 'blender-smoke.json'
    $standardOutputPath = Join-Path $ProfileArtifactRoot 'blender-smoke.stdout.log'
    $standardErrorPath = Join-Path $ProfileArtifactRoot 'blender-smoke.stderr.log'
    foreach ($artifactPath in @($resultPath, $standardOutputPath, $standardErrorPath)) {
        if (Test-Path -LiteralPath $artifactPath) {
            Remove-Item -LiteralPath $artifactPath -Force
        }
    }
    $smokePath = Join-Path $RepoRoot 'tests\blender\compatibility_smoke.py'
    $addonParent = Join-Path $RepoRoot 'blender_addon'
    $arguments = @(
        '--background',
        '--factory-startup',
        '--python-exit-code', '1',
        '--python', $smokePath,
        '--',
        '--addon-parent', $addonParent,
        '--expected-version', ([string]$ProfileObject.version),
        '--result', $resultPath
    )

    $argumentLine = (@($arguments | ForEach-Object { Convert-ToProcessArgument -Value ([string]$_) }) -join ' ')
    $blenderCapture = Start-RedirectedChildProcess `
        -FilePath $Configuration.Executable `
        -ArgumentLine $argumentLine
    $blenderProcess = $blenderCapture.Process
    $waitError = $null
    try {
        $exitCode = Wait-ChildProcess `
            -Process $blenderProcess `
            -TimeoutSeconds $TimeoutSeconds `
            -Description "Blender compatibility smoke '$($ProfileObject.id)'"
    }
    catch {
        $waitError = $_
    }
    $captureError = $null
    try {
        Complete-RedirectedChildProcess `
            -Capture $blenderCapture `
            -StandardOutputPath $standardOutputPath `
            -StandardErrorPath $standardErrorPath
    }
    catch {
        $captureError = $_.Exception.Message
    }
    if ($null -ne $waitError) {
        if (![string]::IsNullOrWhiteSpace($captureError)) {
            Write-Warning "Blender smoke log capture failed: $captureError"
        }
        Write-BlenderSmokeLogTails -StandardOutputPath $standardOutputPath -StandardErrorPath $standardErrorPath
        throw $waitError
    }
    if (![string]::IsNullOrWhiteSpace($captureError)) {
        throw "Blender smoke log capture failed for profile '$($ProfileObject.id)': $captureError"
    }
    if (!(Test-Path -LiteralPath $resultPath -PathType Leaf)) {
        Write-BlenderSmokeLogTails -StandardOutputPath $standardOutputPath -StandardErrorPath $standardErrorPath
        throw "Blender smoke did not write a result for profile '$($ProfileObject.id)'. Exit code: $exitCode"
    }
    try {
        $result = Get-Content -LiteralPath $resultPath -Raw | ConvertFrom-Json
    }
    catch {
        Write-BlenderSmokeLogTails -StandardOutputPath $standardOutputPath -StandardErrorPath $standardErrorPath
        throw "Blender smoke result could not be parsed for profile '$($ProfileObject.id)': $($_.Exception.Message)"
    }
    if ($exitCode -ne 0 -or ![string]::Equals([string]$result.status, 'passed', [System.StringComparison]::OrdinalIgnoreCase)) {
        Write-BlenderSmokeLogTails -StandardOutputPath $standardOutputPath -StandardErrorPath $standardErrorPath
        throw "Blender smoke failed for profile '$($ProfileObject.id)' with exit code $exitCode and status '$($result.status)': $($result.error)"
    }
    Assert-ChecksMatch `
        -Expected @($ProfileObject.checks) `
        -Actual @((Get-PropertyValue -Object $result -Name 'checks')) `
        -Context "Blender smoke profile '$($ProfileObject.id)'"

    $nativeAvailable = [bool](Get-PropertyValue -Object $result.native -Name 'available')
    $nativeCacheTag = [string](Get-PropertyValue -Object $result.native -Name 'cacheTag')
    $nativePlatformTag = [string](Get-PropertyValue -Object $result.native -Name 'platformTag')
    $nativeVersion = [string](Get-PropertyValue -Object $result.native -Name 'version')
    $nativeCapabilities = @((Get-PropertyValue -Object $result.native -Name 'capabilities'))
    if ($nativeAvailable) {
        $expectedNativeVersion = [string](Get-PropertyValue -Object $ReleaseMetadata -Name 'nativeVersion')
        if (![string]::Equals($nativeVersion, $expectedNativeVersion, [System.StringComparison]::Ordinal)) {
            throw "Blender smoke profile '$($ProfileObject.id)' loaded native version '$nativeVersion', expected '$expectedNativeVersion'."
        }
        Assert-ChecksMatch `
            -Expected @((Get-PropertyValue -Object $ReleaseMetadata -Name 'nativeCapabilities')) `
            -Actual $nativeCapabilities `
            -Context "Blender native capabilities for profile '$($ProfileObject.id)'"
    }
    Write-Host "compatibility_blender_ok id=$($ProfileObject.id) version=$($result.blenderVersion) python=$($result.pythonVersion) native=$nativeAvailable nativeVersion=$nativeVersion nativeCapabilities=$($nativeCapabilities.Count) platformTag=$nativePlatformTag cacheTag=$nativeCacheTag"
}

function Invoke-UnityProfile {
    param(
        [object]$ProfileObject,
        [object]$Configuration,
        [string]$RepoRoot,
        [string]$ProfileArtifactRoot,
        [int]$TimeoutSeconds
    )

    $expectedUnity = [string]$ProfileObject.version
    $expectedUrp = [string]$ProfileObject.urpVersion
    $projectVersion = Get-UnityProjectVersion -ProjectRoot $Configuration.Project
    $urpVersion = Get-UnityUrpVersion -ProjectRoot $Configuration.Project
    $editorVersion = [string](Get-Item -LiteralPath $Configuration.Editor).VersionInfo.ProductVersion
    Assert-VersionPrefix -Actual $projectVersion -ExpectedMajorMinor $expectedUnity -Context 'Unity project'
    Assert-VersionPrefix -Actual $editorVersion -ExpectedMajorMinor $expectedUnity -Context 'Unity editor'
    Assert-VersionPrefix -Actual $urpVersion -ExpectedMajorMinor $expectedUrp -Context 'URP package'

    if (!(Test-Path -LiteralPath $ProfileArtifactRoot -PathType Container)) {
        New-Item -ItemType Directory -Path $ProfileArtifactRoot | Out-Null
    }
    $runtimeRoot = Join-Path $Configuration.Project 'Assets\TriSync'
    $syncScript = Join-Path $RepoRoot 'tools\Sync-UnityRuntime.ps1'
    $syncArguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $syncScript,
        '-RuntimeRoot', $runtimeRoot,
        '-UnityProjectRoot', $Configuration.Project,
        '-RemoveStaleFiles'
    )
    & powershell @syncArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Unity runtime compile failed for profile '$($ProfileObject.id)' with exit code $LASTEXITCODE."
    }

    $editModeScript = Join-Path $RepoRoot 'tools\Test-UnityEditMode.ps1'
    $editModeArguments = @(
        '-NoProfile',
        '-ExecutionPolicy', 'Bypass',
        '-File', $editModeScript,
        '-UnityProjectRoot', $Configuration.Project,
        '-UnityEditorPath', $Configuration.Editor,
        '-ResultDirectory', $ProfileArtifactRoot,
        '-TimeoutSeconds', $TimeoutSeconds
    )
    & powershell @editModeArguments
    if ($LASTEXITCODE -ne 0) {
        throw "Unity EditMode failed for profile '$($ProfileObject.id)' with exit code $LASTEXITCODE."
    }

    Write-Host "compatibility_unity_ok id=$($ProfileObject.id) unity=$projectVersion urp=$urpVersion"
}

$repoRoot = Get-RepoRoot
if ($ValidateOnly -and $List) {
    throw '-ValidateOnly and -List cannot be used together.'
}
if ([string]::IsNullOrWhiteSpace($MatrixPath)) {
    $MatrixPath = Join-Path $repoRoot 'tools\compatibility-matrix.json'
}
$MatrixPath = (Resolve-Path -LiteralPath $MatrixPath).Path
$definition = Read-And-ValidateMatrix -Path $MatrixPath
$allProfiles = @($definition.Profiles)
$interopPairs = @($definition.InteropPairs)

Write-Host "compatibility_matrix_definition_ok schema=1 profiles=$($allProfiles.Count) blender=$(@($allProfiles | Where-Object { $_.product -eq 'blender' }).Count) unity=$(@($allProfiles | Where-Object { $_.product -eq 'unity' }).Count) interopPairs=$($interopPairs.Count)"
if ($ValidateOnly) {
    return
}

$requestedIds = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
foreach ($rawProfile in @($Profile)) {
    foreach ($part in @([string]$rawProfile -split ',')) {
        $id = $part.Trim()
        if (![string]::IsNullOrWhiteSpace($id)) {
            [void]$requestedIds.Add($id)
        }
    }
}
if ($requestedIds.Count -gt 0) {
    $knownIds = @($allProfiles | ForEach-Object { [string]$_.id })
    $unknownIds = @($requestedIds | Where-Object { $knownIds -notcontains $_ })
    if ($unknownIds.Count -gt 0) {
        throw "Unknown compatibility profile(s): $($unknownIds -join ', ')"
    }
}

$selectedProfiles = @($allProfiles | Where-Object {
    $matchesProduct = $Product -eq 'All' -or [string]::Equals([string]$_.product, $Product, [System.StringComparison]::OrdinalIgnoreCase)
    $matchesId = $requestedIds.Count -eq 0 -or $requestedIds.Contains([string]$_.id)
    return $matchesProduct -and $matchesId
})
if ($selectedProfiles.Count -eq 0) {
    throw 'No compatibility profiles matched the requested filters.'
}

if ($List) {
    foreach ($profileObject in $selectedProfiles) {
        $configuration = Get-ProfileConfiguration -ProfileObject $profileObject
        $urp = if ([string]$profileObject.product -eq 'unity') { [string]$profileObject.urpVersion } else { '-' }
        Write-Host "compatibility_profile id=$($profileObject.id) product=$($profileObject.product) version=$($profileObject.version) urp=$urp status=$($profileObject.verificationStatus) configuration=$($configuration.State) env=$($configuration.Detail)"
    }
    if ($Product -eq 'All' -and $requestedIds.Count -eq 0) {
        foreach ($pair in $interopPairs) {
            Write-Host "compatibility_interop_pair id=$($pair.id) blender=$($pair.blenderProfile) unity=$($pair.unityProfile) status=$($pair.verificationStatus) check=$($pair.check)"
        }
    }
    return
}

$releaseMetadata = $null
if (@($selectedProfiles | Where-Object { $_.product -eq 'blender' }).Count -gt 0) {
    $releaseMetadata = Read-ReleaseMetadata -RepoRoot $repoRoot
}

if ([string]::IsNullOrWhiteSpace($ArtifactRoot)) {
    $ArtifactRoot = Join-Path $repoRoot 'tmp\compatibility-matrix'
}
elseif (![System.IO.Path]::IsPathRooted($ArtifactRoot)) {
    $ArtifactRoot = Join-Path $repoRoot $ArtifactRoot
}
$ArtifactRoot = [System.IO.Path]::GetFullPath($ArtifactRoot)
if (!(Test-Path -LiteralPath $ArtifactRoot -PathType Container)) {
    New-Item -ItemType Directory -Path $ArtifactRoot | Out-Null
}

$results = New-Object 'System.Collections.Generic.List[object]'
foreach ($profileObject in $selectedProfiles) {
    $configuration = Get-ProfileConfiguration -ProfileObject $profileObject
    if ($configuration.State -eq 'missing' -and $AllowMissing) {
        $results.Add([pscustomobject]@{
            Id = [string]$profileObject.id
            Product = [string]$profileObject.product
            Status = 'skipped'
            Detail = "missing:$($configuration.Detail)"
        })
        continue
    }
    if ($configuration.State -ne 'ready') {
        $results.Add([pscustomobject]@{
            Id = [string]$profileObject.id
            Product = [string]$profileObject.product
            Status = 'failed'
            Detail = "$($configuration.State):$($configuration.Detail)"
        })
        continue
    }

    $profileArtifactRoot = Join-Path $ArtifactRoot ([string]$profileObject.id)
    try {
        if ([string]$profileObject.product -eq 'blender') {
            Invoke-BlenderProfile `
                -ProfileObject $profileObject `
                -Configuration $configuration `
                -ReleaseMetadata $releaseMetadata `
                -RepoRoot $repoRoot `
                -ProfileArtifactRoot $profileArtifactRoot `
                -TimeoutSeconds $BlenderTimeoutSeconds
        }
        else {
            Invoke-UnityProfile `
                -ProfileObject $profileObject `
                -Configuration $configuration `
                -RepoRoot $repoRoot `
                -ProfileArtifactRoot $profileArtifactRoot `
                -TimeoutSeconds $UnityTimeoutSeconds
        }
        $results.Add([pscustomobject]@{
            Id = [string]$profileObject.id
            Product = [string]$profileObject.product
            Status = 'passed'
            Detail = [string]$profileObject.version
        })
    }
    catch {
        $results.Add([pscustomobject]@{
            Id = [string]$profileObject.id
            Product = [string]$profileObject.product
            Status = 'failed'
            Detail = $_.Exception.Message
        })
    }
}

foreach ($result in $results) {
    Write-Host "compatibility_result id=$($result.Id) product=$($result.Product) status=$($result.Status) detail=$($result.Detail)"
}
$passed = @($results | Where-Object { $_.Status -eq 'passed' })
$failed = @($results | Where-Object { $_.Status -eq 'failed' })
$skipped = @($results | Where-Object { $_.Status -eq 'skipped' })
if ($failed.Count -gt 0) {
    throw "Compatibility matrix failed: $($failed.Id -join ', ')"
}
if ($passed.Count -eq 0) {
    throw 'Compatibility matrix did not execute any configured profile.'
}

Write-Host "compatibility_matrix_ok passed=$($passed.Count) skipped=$($skipped.Count) manualInteropPairs=$($interopPairs.Count)"
