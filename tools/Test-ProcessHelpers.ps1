[CmdletBinding()]
param()

$ErrorActionPreference = 'Stop'

. (Join-Path $PSScriptRoot 'ProcessHelpers.ps1')

function Assert-Equal {
    param(
        [string]$Expected,
        [string]$Actual,
        [string]$Context
    )

    if (![string]::Equals($Expected, $Actual, [System.StringComparison]::Ordinal)) {
        throw "$Context mismatch. Expected '$Expected', found '$Actual'."
    }
}

Assert-Equal -Context 'empty argument' -Expected '""' -Actual (Convert-ToProcessArgument -Value '')
Assert-Equal -Context 'plain argument' -Expected 'plain' -Actual (Convert-ToProcessArgument -Value 'plain')
Assert-Equal -Context 'spaced path' -Expected '"C:\Project Files\Unity"' -Actual (Convert-ToProcessArgument -Value 'C:\Project Files\Unity')
Assert-Equal -Context 'trailing slash path' -Expected '"C:\Project Files\Unity\\"' -Actual (Convert-ToProcessArgument -Value 'C:\Project Files\Unity\')
Assert-Equal -Context 'embedded quote' -Expected '"say \"hello\""' -Actual (Convert-ToProcessArgument -Value 'say "hello"')

$powerShellPath = Join-Path $PSHOME 'powershell.exe'
$completedProcess = Start-Process `
    -FilePath $powerShellPath `
    -ArgumentList '-NoProfile -Command "exit 7"' `
    -PassThru `
    -WindowStyle Hidden
$completedExitCode = Wait-ChildProcess `
    -Process $completedProcess `
    -TimeoutSeconds 10 `
    -Description 'Process helper completion probe'
if ($completedExitCode -ne 7) {
    throw "Process helper completion probe returned exit code $completedExitCode, expected 7."
}

$redirectProbeRoot = Join-Path ([System.IO.Path]::GetTempPath()) ("blendersync-process-helper-$([guid]::NewGuid().ToString('N'))")
New-Item -ItemType Directory -Path $redirectProbeRoot | Out-Null
$redirectedScript = "[Console]::Out.WriteLine('stdout-probe'); [Console]::Error.WriteLine('stderr-probe'); exit 9"
$encodedRedirectedScript = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($redirectedScript))
$redirectedStandardOutputPath = Join-Path $redirectProbeRoot 'stdout.log'
$redirectedStandardErrorPath = Join-Path $redirectProbeRoot 'stderr.log'
$redirectedCapture = Start-RedirectedChildProcess `
    -FilePath $powerShellPath `
    -ArgumentLine "-NoProfile -EncodedCommand $encodedRedirectedScript"
$redirectedProcess = $redirectedCapture.Process
try {
    $redirectedExitCode = Wait-ChildProcess `
        -Process $redirectedProcess `
        -TimeoutSeconds 10 `
        -Description 'Process helper redirected completion probe'
    Complete-RedirectedChildProcess `
        -Capture $redirectedCapture `
        -StandardOutputPath $redirectedStandardOutputPath `
        -StandardErrorPath $redirectedStandardErrorPath
    if ($redirectedExitCode -ne 9) {
        throw "Process helper redirected completion probe returned exit code '$redirectedExitCode', expected 9."
    }
    Assert-Equal -Context 'redirected stdout' -Expected 'stdout-probe' -Actual ((Get-Content -LiteralPath $redirectedStandardOutputPath -Raw).Trim())
    Assert-Equal -Context 'redirected stderr' -Expected 'stderr-probe' -Actual ((Get-Content -LiteralPath $redirectedStandardErrorPath -Raw).Trim())
}
finally {
    if (!$redirectedProcess.HasExited) {
        Stop-Process -Id $redirectedProcess.Id -Force -ErrorAction SilentlyContinue
        [void]$redirectedProcess.WaitForExit(5000)
    }
    Remove-Item -LiteralPath $redirectProbeRoot -Recurse -Force -ErrorAction SilentlyContinue
}

$childPidPath = Join-Path ([System.IO.Path]::GetTempPath()) ("blendersync-process-helper-$([guid]::NewGuid().ToString('N')).pid")
$escapedPowerShellPath = $powerShellPath.Replace("'", "''")
$escapedChildPidPath = $childPidPath.Replace("'", "''")
$timeoutScript = @"
`$child = Start-Process -FilePath '$escapedPowerShellPath' -ArgumentList '-NoProfile -Command "Start-Sleep -Seconds 30"' -PassThru -WindowStyle Hidden
[System.IO.File]::WriteAllText('$escapedChildPidPath', [string]`$child.Id)
Start-Sleep -Seconds 30
"@
$encodedTimeoutScript = [Convert]::ToBase64String([Text.Encoding]::Unicode.GetBytes($timeoutScript))
$timeoutProcess = Start-Process `
    -FilePath $powerShellPath `
    -ArgumentList "-NoProfile -EncodedCommand $encodedTimeoutScript" `
    -PassThru `
    -WindowStyle Hidden
$childProcess = $null
$timedOut = $false
$processTreeTerminated = $false
try {
    $childPidDeadline = [DateTime]::UtcNow.AddSeconds(5)
    while (!(Test-Path -LiteralPath $childPidPath -PathType Leaf) -and [DateTime]::UtcNow -lt $childPidDeadline) {
        Start-Sleep -Milliseconds 50
    }
    if (!(Test-Path -LiteralPath $childPidPath -PathType Leaf)) {
        throw 'Process helper timeout probe did not publish its child process id.'
    }
    $childProcessId = [int](Get-Content -LiteralPath $childPidPath -Raw)
    $childProcess = [System.Diagnostics.Process]::GetProcessById($childProcessId)

    [void](Wait-ChildProcess `
        -Process $timeoutProcess `
        -TimeoutSeconds 1 `
        -Description 'Process helper timeout probe')
}
catch {
    if ($_.Exception.Message -notlike 'Process helper timeout probe timed out*') {
        throw
    }
    $timedOut = $true
    if (!$timeoutProcess.HasExited) {
        [void]$timeoutProcess.WaitForExit(5000)
    }
    if ($null -ne $childProcess -and !$childProcess.HasExited) {
        [void]$childProcess.WaitForExit(5000)
    }
    $processTreeTerminated = $timeoutProcess.HasExited -and $null -ne $childProcess -and $childProcess.HasExited
}
finally {
    if ($null -ne $childProcess -and !$childProcess.HasExited) {
        Stop-Process -Id $childProcess.Id -Force -ErrorAction SilentlyContinue
        [void]$childProcess.WaitForExit(5000)
    }
    if (!$timeoutProcess.HasExited) {
        Stop-Process -Id $timeoutProcess.Id -Force -ErrorAction SilentlyContinue
        [void]$timeoutProcess.WaitForExit(5000)
    }
    Remove-Item -LiteralPath $childPidPath -Force -ErrorAction SilentlyContinue
}
if (!$timedOut -or !$processTreeTerminated) {
    throw 'Process helper timeout probe did not time out and terminate its process tree.'
}

Write-Host 'process_helpers_ok'
