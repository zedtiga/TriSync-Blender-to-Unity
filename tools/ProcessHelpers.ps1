function Convert-ToProcessArgument {
    param([AllowNull()][AllowEmptyString()][string]$Value)

    if ($null -eq $Value -or $Value.Length -eq 0) {
        return '""'
    }
    if ($Value -notmatch '[\s"]') {
        return $Value
    }

    # MSVCRT doubles backslashes before a quote, including the closing quote.
    $builder = New-Object System.Text.StringBuilder
    [void]$builder.Append([char]'"')
    $backslashes = 0
    foreach ($character in $Value.ToCharArray()) {
        if ($character -eq [char]'\') {
            $backslashes += 1
            continue
        }
        if ($character -eq [char]'"') {
            [void]$builder.Append([char]'\', ($backslashes * 2) + 1)
            [void]$builder.Append([char]'"')
            $backslashes = 0
            continue
        }
        if ($backslashes -gt 0) {
            [void]$builder.Append([char]'\', $backslashes)
            $backslashes = 0
        }
        [void]$builder.Append($character)
    }
    if ($backslashes -gt 0) {
        [void]$builder.Append([char]'\', $backslashes * 2)
    }
    [void]$builder.Append([char]'"')
    return $builder.ToString()
}

function Start-RedirectedChildProcess {
    param(
        [Parameter(Mandatory = $true)]
        [string]$FilePath,
        [Parameter(Mandatory = $true)]
        [string]$ArgumentLine
    )

    # PowerShell 5.1 can lose ExitCode on Start-Process objects that own redirects.
    $startInfo = New-Object System.Diagnostics.ProcessStartInfo
    $startInfo.FileName = $FilePath
    $startInfo.Arguments = $ArgumentLine
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true

    $process = New-Object System.Diagnostics.Process
    $process.StartInfo = $startInfo
    if (!$process.Start()) {
        throw "Failed to start redirected child process '$FilePath'."
    }

    return [pscustomobject]@{
        Process = $process
        StandardOutputTask = $process.StandardOutput.ReadToEndAsync()
        StandardErrorTask = $process.StandardError.ReadToEndAsync()
    }
}

function Complete-RedirectedChildProcess {
    param(
        [Parameter(Mandatory = $true)]
        [object]$Capture,
        [Parameter(Mandatory = $true)]
        [string]$StandardOutputPath,
        [Parameter(Mandatory = $true)]
        [string]$StandardErrorPath,
        [ValidateRange(1, 60)]
        [int]$TimeoutSeconds = 5
    )

    $tasks = [System.Threading.Tasks.Task[]]@(
        $Capture.StandardOutputTask,
        $Capture.StandardErrorTask
    )
    $timeoutMilliseconds = [int]([int64]$TimeoutSeconds * 1000)
    if (![System.Threading.Tasks.Task]::WaitAll($tasks, $timeoutMilliseconds)) {
        throw "Redirected child process streams did not close within $TimeoutSeconds second(s)."
    }

    [System.IO.File]::WriteAllText($StandardOutputPath, [string]$Capture.StandardOutputTask.Result)
    [System.IO.File]::WriteAllText($StandardErrorPath, [string]$Capture.StandardErrorTask.Result)
}

function Stop-ChildProcessTree {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process
    )

    $processId = $Process.Id
    if ([string]::Equals($env:OS, 'Windows_NT', [System.StringComparison]::OrdinalIgnoreCase)) {
        $taskkillPath = Join-Path $env:SystemRoot 'System32\taskkill.exe'
        if (!(Test-Path -LiteralPath $taskkillPath -PathType Leaf)) {
            throw "taskkill.exe is unavailable; could not terminate process tree rooted at pid=$processId."
        }

        $taskkillProcess = Start-Process `
            -FilePath $taskkillPath `
            -ArgumentList "/PID $processId /T /F" `
            -PassThru `
            -WindowStyle Hidden
        $taskkillProcess.WaitForExit()
        if ($taskkillProcess.ExitCode -ne 0 -and !$Process.HasExited) {
            throw "taskkill.exe exited with code $($taskkillProcess.ExitCode) for pid=$processId."
        }
        return
    }

    if (!$Process.HasExited) {
        Stop-Process -Id $processId -Force -ErrorAction Stop
    }
}

function Wait-ChildProcess {
    param(
        [Parameter(Mandatory = $true)]
        [System.Diagnostics.Process]$Process,
        [Parameter(Mandatory = $true)]
        [ValidateRange(1, 86400)]
        [int]$TimeoutSeconds,
        [Parameter(Mandatory = $true)]
        [string]$Description
    )

    $timeoutMilliseconds = [int]([int64]$TimeoutSeconds * 1000)
    if ($Process.WaitForExit($timeoutMilliseconds)) {
        $Process.WaitForExit()
        return $Process.ExitCode
    }

    $processId = $Process.Id
    $killError = $null
    try {
        Stop-ChildProcessTree -Process $Process
    }
    catch {
        if (!$Process.HasExited) {
            $killError = $_.Exception.Message
        }
    }
    try {
        if ($Process.WaitForExit(5000)) {
            $Process.WaitForExit()
        }
        elseif ([string]::IsNullOrWhiteSpace($killError)) {
            $killError = "Process did not exit within 5 seconds after termination was requested."
        }
    }
    catch {
        if ([string]::IsNullOrWhiteSpace($killError)) {
            $killError = $_.Exception.Message
        }
    }

    $detail = if ([string]::IsNullOrWhiteSpace($killError)) { '' } else { " Kill error: $killError" }
    throw "$Description timed out after $TimeoutSeconds second(s) (pid=$processId).$detail"
}
