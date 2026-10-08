param(
    [string]$SourceRoot,
    [string]$RuntimeRoot,
    [string]$UnityProjectRoot,
    [switch]$SkipSync,
    [switch]$SkipProjectPatch,
    [switch]$SkipBuild,
    [switch]$RemoveStaleFiles,
    [switch]$VerboseFiles
)

$ErrorActionPreference = 'Stop'

if ([string]::IsNullOrWhiteSpace($UnityProjectRoot)) {
    throw 'Pass -UnityProjectRoot with the path to an existing Unity URP test project.'
}
if (!(Test-Path -LiteralPath $UnityProjectRoot -PathType Container)) {
    throw "Unity project directory not found: $UnityProjectRoot. Pass -UnityProjectRoot with an existing project directory."
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

function Test-ExcludedRelativePath {
    param([string]$RelativePath)

    return (
        $RelativePath -like 'DebugSnapshots\*' -or
        $RelativePath -like 'Registry\*.json' -or
        $RelativePath -like 'Resources\AnimationClips\*' -or
        $RelativePath -like 'Resources\AnimationControllers\*' -or
        $RelativePath -like 'Resources\Avatars\*' -or
        $RelativePath -like 'Resources\Materials\*' -or
        $RelativePath -eq 'Resources\Materials.meta' -or
        $RelativePath -like 'Resources\Meshes\*' -or
        $RelativePath -like 'Resources\RiggedObjects\*' -or
        $RelativePath -like 'Resources\Textures\*' -or
        $RelativePath -eq 'Resources\Textures.meta'
    )
}

function Test-EditorRelativePath {
    param([string]$RelativePath)

    $parts = $RelativePath -split '[\\/]'
    return $parts -contains 'Editor'
}

function Test-PathWithinRoot {
    param(
        [string]$Root,
        [string]$Path
    )

    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    $pathFull = [System.IO.Path]::GetFullPath($Path)
    return $pathFull.Equals($rootFull, [System.StringComparison]::OrdinalIgnoreCase) -or
        $pathFull.StartsWith($rootFull + [System.IO.Path]::DirectorySeparatorChar, [System.StringComparison]::OrdinalIgnoreCase)
}

function Get-NearestAsmdef {
    param(
        [string]$Source,
        [string]$RelativePath
    )

    $filePath = Join-Path $Source $RelativePath
    $directory = Split-Path -Parent ([System.IO.Path]::GetFullPath($filePath))
    while (Test-PathWithinRoot -Root $Source -Path $directory) {
        $asmdef = Get-ChildItem -LiteralPath $directory -File -Filter '*.asmdef' -ErrorAction SilentlyContinue |
            Select-Object -First 1
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

function Get-SyncSourceFiles {
    param([string]$Root)

    Get-ChildItem -LiteralPath $Root -Recurse -File | Where-Object {
        $rel = Convert-ToRelativePath -Root $Root -Path $_.FullName
        -not (Test-ExcludedRelativePath -RelativePath $rel)
    }
}

function Sync-Files {
    param(
        [string]$Source,
        [string]$Destination,
        [object[]]$Files
    )

    $copied = 0
    foreach ($file in $Files) {
        $rel = Convert-ToRelativePath -Root $Source -Path $file.FullName
        $target = Join-Path $Destination $rel
        $targetDir = Split-Path -Parent $target
        if (!(Test-Path -LiteralPath $targetDir)) {
            New-Item -ItemType Directory -Path $targetDir | Out-Null
        }

        $shouldCopy = $true
        if (Test-Path -LiteralPath $target) {
            $srcHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash
            $dstHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash
            $shouldCopy = $srcHash -ne $dstHash
        }

        if ($shouldCopy) {
            Copy-Item -LiteralPath $file.FullName -Destination $target -Force
            $copied += 1
            if ($VerboseFiles) {
                Write-Host "copied $rel"
            }
        }
    }

    return $copied
}

function Compare-UnityMirror {
    param(
        [string]$Source,
        [string]$Destination,
        [object[]]$Files
    )

    $diff = New-Object System.Collections.Generic.List[string]
    foreach ($file in $Files) {
        $rel = Convert-ToRelativePath -Root $Source -Path $file.FullName
        $target = Join-Path $Destination $rel
        if (!(Test-Path -LiteralPath $target)) {
            $diff.Add("missing_runtime $rel")
            continue
        }

        $srcHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $file.FullName).Hash
        $dstHash = (Get-FileHash -Algorithm SHA256 -LiteralPath $target).Hash
        if ($srcHash -ne $dstHash) {
            $diff.Add("different $rel")
        }
    }

    return $diff.ToArray()
}

function Get-StaleRuntimeFiles {
    param(
        [string]$Source,
        [string]$Destination,
        [object[]]$Files
    )

    $sourceRelativePaths = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($file in $Files) {
        $rel = Convert-ToRelativePath -Root $Source -Path $file.FullName
        [void]$sourceRelativePaths.Add($rel)
    }

    $stale = New-Object System.Collections.Generic.List[object]
    Get-ChildItem -LiteralPath $Destination -Recurse -File -Force | ForEach-Object {
        $rel = Convert-ToRelativePath -Root $Destination -Path $_.FullName
        if (Test-ExcludedRelativePath -RelativePath $rel) {
            return
        }
        if ($rel -like '*.meta') {
            $assetPath = $_.FullName.Substring(0, $_.FullName.Length - 5)
            if (Test-Path -LiteralPath $assetPath) {
                return
            }
        }
        if (!$sourceRelativePaths.Contains($rel)) {
            $stale.Add([pscustomobject]@{
                RelativePath = $rel
                FullName = $_.FullName
            })
        }
    }

    return $stale.ToArray()
}

function Remove-StaleRuntimeFiles {
    param(
        [string]$Root,
        [object[]]$Files
    )

    $removed = 0
    $rootFull = [System.IO.Path]::GetFullPath($Root).TrimEnd('\', '/')
    foreach ($file in $Files) {
        $filePath = [System.IO.Path]::GetFullPath($file.FullName)
        if (!$filePath.StartsWith($rootFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            throw "Refusing to remove stale file outside runtime root: $filePath"
        }
        Remove-Item -LiteralPath $filePath -Force
        $removed += 1
        if ($VerboseFiles) {
            Write-Host "removed_stale $($file.RelativePath)"
        }
    }
    return $removed
}

function Get-ExpectedCompileIncludes {
    param(
        [string]$Source,
        [switch]$EditorOnly
    )

    $expected = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $csFiles = Get-ChildItem -LiteralPath (Join-Path $Source 'Scripts') -Recurse -File -Filter '*.cs'
    foreach ($file in $csFiles) {
        $rel = Convert-ToRelativePath -Root $Source -Path $file.FullName
        if (Test-ExcludedRelativePath -RelativePath $rel) {
            continue
        }

        if ($null -ne (Get-NearestAsmdef -Source $Source -RelativePath $rel)) {
            continue
        }

        $isEditorFile = Test-EditorRelativePath -RelativePath $rel
        if ($EditorOnly -and !$isEditorFile) {
            continue
        }
        if (!$EditorOnly -and $isEditorFile) {
            continue
        }

        [void]$expected.Add('Assets\TriSync\' + $rel)
    }
    return $expected
}

function Remove-StaleCompileItems {
    param(
        [string]$Source,
        [string]$ProjectFile,
        [switch]$EditorOnly
    )

    if (!(Test-Path -LiteralPath $ProjectFile)) {
        throw "Project file not found: $ProjectFile"
    }

    $expected = Get-ExpectedCompileIncludes -Source $Source -EditorOnly:$EditorOnly
    $content = Get-Content -LiteralPath $ProjectFile -Raw
    $removed = New-Object System.Collections.Generic.List[string]
    $pattern = '(?m)^\s*<Compile Include="(Assets\\TriSync\\[^"]+\.cs)"\s*/>\r?\n?'
    $updated = [regex]::Replace($content, $pattern, {
        param($match)
        $include = $match.Groups[1].Value
        if ($expected.Contains($include)) {
            return $match.Value
        }
        $removed.Add($include)
        return ''
    })

    if ($removed.Count -gt 0) {
        Set-Content -LiteralPath $ProjectFile -Value $updated -NoNewline
    }
    return $removed.ToArray()
}

function Add-MissingCompileItems {
    param(
        [string]$Source,
        [string]$ProjectFile,
        [switch]$EditorOnly
    )

    if (!(Test-Path -LiteralPath $ProjectFile)) {
        throw "Project file not found: $ProjectFile"
    }

    $content = Get-Content -LiteralPath $ProjectFile -Raw
    $existing = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($match in [regex]::Matches($content, '<Compile Include="([^"]+)"')) {
        [void]$existing.Add($match.Groups[1].Value)
    }

    $missing = New-Object System.Collections.Generic.List[string]
    $expected = Get-ExpectedCompileIncludes -Source $Source -EditorOnly:$EditorOnly
    foreach ($include in $expected) {
        if (!$existing.Contains($include)) {
            $missing.Add($include)
        }
    }

    if ($missing.Count -eq 0) {
        return @()
    }

    $insert = ($missing | Sort-Object | ForEach-Object { "    <Compile Include=`"$_`" />" }) -join [Environment]::NewLine
    $insert = $insert + [Environment]::NewLine

    $compileItemGroupPattern = '(?s)(<ItemGroup>\s*(?:<Compile Include="[^"]+"\s*/>\s*)+)(</ItemGroup>)'
    $match = [regex]::Match($content, $compileItemGroupPattern)
    if ($match.Success) {
        $updated = $content.Substring(0, $match.Groups[2].Index) + $insert + $content.Substring($match.Groups[2].Index)
    }
    else {
        $projectCloseIndex = $content.LastIndexOf('</Project>', [System.StringComparison]::OrdinalIgnoreCase)
        if ($projectCloseIndex -lt 0) {
            throw "Could not find Project closing element in $ProjectFile"
        }
        $itemGroup = "  <ItemGroup>" + [Environment]::NewLine + $insert + "  </ItemGroup>" + [Environment]::NewLine
        $updated = $content.Substring(0, $projectCloseIndex) + $itemGroup + $content.Substring($projectCloseIndex)
    }
    Set-Content -LiteralPath $ProjectFile -Value $updated -NoNewline
    return $missing.ToArray()
}

function Get-ExpectedCompileIncludesForAsmdef {
    param(
        [string]$Source,
        [string]$AsmdefPath
    )

    $expected = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    $asmdefFull = [System.IO.Path]::GetFullPath($AsmdefPath)
    foreach ($file in Get-ChildItem -LiteralPath (Join-Path $Source 'Scripts') -Recurse -File -Filter '*.cs') {
        $rel = Convert-ToRelativePath -Root $Source -Path $file.FullName
        if (Test-ExcludedRelativePath -RelativePath $rel) {
            continue
        }
        $nearest = Get-NearestAsmdef -Source $Source -RelativePath $rel
        if ([string]::IsNullOrWhiteSpace($nearest)) {
            continue
        }
        if ([System.IO.Path]::GetFullPath($nearest).Equals($asmdefFull, [System.StringComparison]::OrdinalIgnoreCase)) {
            [void]$expected.Add('Assets\TriSync\' + $rel)
        }
    }
    return ,$expected
}

function Remove-StaleCompileItemsByExpected {
    param(
        [string]$ProjectFile,
        [object]$Expected
    )

    if (!(Test-Path -LiteralPath $ProjectFile)) {
        throw "Project file not found: $ProjectFile"
    }
    $content = Get-Content -LiteralPath $ProjectFile -Raw
    $removed = New-Object System.Collections.Generic.List[string]
    $pattern = '(?m)^\s*<Compile Include="(Assets\\TriSync\\[^"]+\.cs)"\s*/>\r?\n?'
    $updated = [regex]::Replace($content, $pattern, {
        param($match)
        $include = $match.Groups[1].Value
        if ($Expected.Contains($include)) {
            return $match.Value
        }
        $removed.Add($include)
        return ''
    })
    if ($removed.Count -gt 0) {
        Set-Content -LiteralPath $ProjectFile -Value $updated -NoNewline
    }
    return $removed.ToArray()
}

function Add-MissingCompileItemsByExpected {
    param(
        [string]$ProjectFile,
        [object]$Expected
    )

    if (!(Test-Path -LiteralPath $ProjectFile)) {
        throw "Project file not found: $ProjectFile"
    }
    $content = Get-Content -LiteralPath $ProjectFile -Raw
    $existing = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($match in [regex]::Matches($content, '<Compile Include="([^"]+)"')) {
        [void]$existing.Add($match.Groups[1].Value)
    }
    $missing = New-Object System.Collections.Generic.List[string]
    foreach ($include in $Expected) {
        if (!$existing.Contains($include)) {
            $missing.Add($include)
        }
    }
    if ($missing.Count -eq 0) {
        return @()
    }

    $insert = ($missing | Sort-Object | ForEach-Object { "    <Compile Include=`"$_`" />" }) -join [Environment]::NewLine
    $insert += [Environment]::NewLine
    $compileItemGroupPattern = '(?s)(<ItemGroup>\s*(?:<Compile Include="[^"]+"\s*/>\s*)+)(</ItemGroup>)'
    $match = [regex]::Match($content, $compileItemGroupPattern)
    if ($match.Success) {
        $updated = $content.Substring(0, $match.Groups[2].Index) + $insert + $content.Substring($match.Groups[2].Index)
    }
    else {
        $projectCloseIndex = $content.LastIndexOf('</Project>', [System.StringComparison]::OrdinalIgnoreCase)
        if ($projectCloseIndex -lt 0) {
            throw "Could not find Project closing element in $ProjectFile"
        }
        $itemGroup = "  <ItemGroup>" + [Environment]::NewLine + $insert + "  </ItemGroup>" + [Environment]::NewLine
        $updated = $content.Substring(0, $projectCloseIndex) + $itemGroup + $content.Substring($projectCloseIndex)
    }
    Set-Content -LiteralPath $ProjectFile -Value $updated -NoNewline
    return $missing.ToArray()
}

function Remove-MissingAssetCompileItems {
    param(
        [string]$ProjectFile,
        [string]$ProjectRoot
    )

    if (!(Test-Path -LiteralPath $ProjectFile)) {
        throw "Project file not found: $ProjectFile"
    }
    $content = Get-Content -LiteralPath $ProjectFile -Raw
    $removed = New-Object System.Collections.Generic.List[string]
    $pattern = '(?m)^\s*<Compile Include="(Assets[\\/][^"]+\.cs)"\s*/>\r?\n?'
    $updated = [regex]::Replace($content, $pattern, {
        param($match)
        $include = $match.Groups[1].Value
        $sourcePath = Join-Path $ProjectRoot ($include -replace '/', [System.IO.Path]::DirectorySeparatorChar)
        if (Test-Path -LiteralPath $sourcePath -PathType Leaf) {
            return $match.Value
        }
        $removed.Add($include)
        return ''
    })
    if ($removed.Count -gt 0) {
        Set-Content -LiteralPath $ProjectFile -Value $updated -NoNewline
    }
    return $removed.ToArray()
}

function Remove-MissingAssetItems {
    param(
        [string]$ProjectFile,
        [string]$ProjectRoot
    )

    if (!(Test-Path -LiteralPath $ProjectFile)) {
        throw "Project file not found: $ProjectFile"
    }
    $content = Get-Content -LiteralPath $ProjectFile -Raw
    $removed = New-Object System.Collections.Generic.List[string]
    $pattern = '(?m)^\s*<(?<kind>None|Content|EmbeddedResource) Include="(?<include>Assets[\\/][^"]+)"\s*/>\r?\n?'
    $updated = [regex]::Replace($content, $pattern, {
        param($match)
        $include = $match.Groups['include'].Value
        $sourcePath = Join-Path $ProjectRoot ($include -replace '/', [System.IO.Path]::DirectorySeparatorChar)
        if (Test-Path -LiteralPath $sourcePath) {
            return $match.Value
        }
        $removed.Add($include)
        return ''
    })
    if ($removed.Count -gt 0) {
        Set-Content -LiteralPath $ProjectFile -Value $updated -NoNewline
    }
    return $removed.ToArray()
}

function Get-LocalAsmdefProjectMap {
    param([string]$Source)

    $projectsByReference = @{}
    foreach ($asmdef in Get-ChildItem -LiteralPath (Join-Path $Source 'Scripts') -Recurse -File -Filter '*.asmdef') {
        $parsed = Get-Content -LiteralPath $asmdef.FullName -Raw | ConvertFrom-Json
        $projectName = "$($parsed.name).csproj"
        $projectsByReference[[string]$parsed.name] = $projectName

        $metaPath = $asmdef.FullName + '.meta'
        if (Test-Path -LiteralPath $metaPath) {
            $meta = Get-Content -LiteralPath $metaPath -Raw
            $guidMatch = [regex]::Match($meta, '(?m)^guid:\s*([0-9a-fA-F]+)\s*$')
            if ($guidMatch.Success) {
                $projectsByReference["GUID:$($guidMatch.Groups[1].Value)"] = $projectName
            }
        }
    }
    return $projectsByReference
}

function Get-ExpectedProjectReferencesForAsmdef {
    param(
        [object]$ParsedAsmdef,
        [hashtable]$ProjectsByReference
    )

    $expected = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($reference in @($ParsedAsmdef.references)) {
        $referenceKey = [string]$reference
        if ($ProjectsByReference.ContainsKey($referenceKey)) {
            [void]$expected.Add([string]$ProjectsByReference[$referenceKey])
        }
    }
    return ,$expected
}

function Remove-StaleProjectReferencesByExpected {
    param(
        [string]$ProjectFile,
        [object]$Expected,
        [object]$LocalProjectFiles
    )

    if (!(Test-Path -LiteralPath $ProjectFile)) {
        throw "Project file not found: $ProjectFile"
    }
    $content = Get-Content -LiteralPath $ProjectFile -Raw
    $removed = New-Object System.Collections.Generic.List[string]
    $pattern = '(?ms)^\s*<ProjectReference Include="([^"]+)"(?:\s*/>|\s*>.*?</ProjectReference>)\r?\n?'
    $updated = [regex]::Replace($content, $pattern, {
        param($match)
        $include = $match.Groups[1].Value
        $projectName = [System.IO.Path]::GetFileName($include)
        if ($Expected.Contains($projectName)) {
            return $match.Value
        }
        $isTriSyncProject = $projectName.StartsWith(
            'BlenderSyncVNext.',
            [System.StringComparison]::OrdinalIgnoreCase)
        if (!$LocalProjectFiles.Contains($projectName) -and !$isTriSyncProject) {
            return $match.Value
        }
        $removed.Add($projectName)
        return ''
    })
    if ($removed.Count -gt 0) {
        Set-Content -LiteralPath $ProjectFile -Value $updated -NoNewline
    }
    return $removed.ToArray()
}

function Add-MissingProjectReferencesByExpected {
    param(
        [string]$ProjectFile,
        [object]$Expected
    )

    if (!(Test-Path -LiteralPath $ProjectFile)) {
        throw "Project file not found: $ProjectFile"
    }
    $content = Get-Content -LiteralPath $ProjectFile -Raw
    $existing = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($match in [regex]::Matches($content, '<ProjectReference Include="([^"]+)"')) {
        [void]$existing.Add([System.IO.Path]::GetFileName($match.Groups[1].Value))
    }

    $missing = New-Object System.Collections.Generic.List[string]
    foreach ($projectName in $Expected) {
        if (!$existing.Contains($projectName)) {
            $missing.Add($projectName)
        }
    }
    if ($missing.Count -eq 0) {
        return @()
    }

    $insert = ($missing | Sort-Object | ForEach-Object { "    <ProjectReference Include=`"$_`" />" }) -join [Environment]::NewLine
    $insert += [Environment]::NewLine
    $projectReferenceGroupPattern = '(?s)(<ItemGroup>\s*(?:<ProjectReference Include="[^"]+"\s*/>\s*)+)(</ItemGroup>)'
    $match = [regex]::Match($content, $projectReferenceGroupPattern)
    if ($match.Success) {
        $updated = $content.Substring(0, $match.Groups[2].Index) + $insert + $content.Substring($match.Groups[2].Index)
    }
    else {
        $projectCloseIndex = $content.LastIndexOf('</Project>', [System.StringComparison]::OrdinalIgnoreCase)
        if ($projectCloseIndex -lt 0) {
            throw "Could not find Project closing element in $ProjectFile"
        }
        $itemGroup = "  <ItemGroup>" + [Environment]::NewLine + $insert + "  </ItemGroup>" + [Environment]::NewLine
        $updated = $content.Substring(0, $projectCloseIndex) + $itemGroup + $content.Substring($projectCloseIndex)
    }
    Set-Content -LiteralPath $ProjectFile -Value $updated -NoNewline
    return $missing.ToArray()
}

function Remove-RetiredTriSyncProjectReferences {
    param(
        [string]$ProjectFile,
        [object]$ActiveProjectFiles
    )

    if (!(Test-Path -LiteralPath $ProjectFile)) {
        throw "Project file not found: $ProjectFile"
    }
    $content = Get-Content -LiteralPath $ProjectFile -Raw
    $removed = New-Object System.Collections.Generic.List[string]
    $pattern = '(?ms)^\s*<ProjectReference Include="([^"]+)"(?:\s*/>|\s*>.*?</ProjectReference>)\r?\n?'
    $updated = [regex]::Replace($content, $pattern, {
        param($match)
        $projectName = [System.IO.Path]::GetFileName($match.Groups[1].Value)
        $isTriSyncProject = $projectName.StartsWith(
            'BlenderSyncVNext.',
            [System.StringComparison]::OrdinalIgnoreCase)
        if (!$isTriSyncProject -or $ActiveProjectFiles.Contains($projectName)) {
            return $match.Value
        }
        $removed.Add($projectName)
        return ''
    })
    if ($removed.Count -gt 0) {
        Set-Content -LiteralPath $ProjectFile -Value $updated -NoNewline
    }
    return $removed.ToArray()
}

function Test-ProjectHasCompileItems {
    param([string]$ProjectFile)

    if (!(Test-Path -LiteralPath $ProjectFile)) {
        return $false
    }
    $content = Get-Content -LiteralPath $ProjectFile -Raw
    return [regex]::IsMatch($content, '<Compile Include="[^"]+"')
}

$repoRoot = Get-RepoRoot
if ([string]::IsNullOrWhiteSpace($SourceRoot)) {
    $SourceRoot = Join-Path $repoRoot 'unity\TriSync'
}

$SourceRoot = (Resolve-Path -LiteralPath $SourceRoot).Path
$UnityProjectRoot = (Resolve-Path -LiteralPath $UnityProjectRoot).Path
if ([string]::IsNullOrWhiteSpace($RuntimeRoot)) {
    $RuntimeRoot = Join-Path $UnityProjectRoot 'Assets\TriSync'
}
if (!(Test-Path -LiteralPath $RuntimeRoot)) {
    New-Item -ItemType Directory -Path $RuntimeRoot | Out-Null
}
$RuntimeRoot = (Resolve-Path -LiteralPath $RuntimeRoot).Path

Write-Host "source=$SourceRoot"
Write-Host "runtime=$RuntimeRoot"
Write-Host "unity_project=$UnityProjectRoot"

$sourceFiles = @(Get-SyncSourceFiles -Root $SourceRoot)
Write-Host "source_files=$($sourceFiles.Count)"

if (!$SkipSync) {
    $copied = Sync-Files -Source $SourceRoot -Destination $RuntimeRoot -Files $sourceFiles
    Write-Host "copied=$copied"
}

$staleFiles = @(Get-StaleRuntimeFiles -Source $SourceRoot -Destination $RuntimeRoot -Files $sourceFiles)
Write-Host "stale_files=$($staleFiles.Count)"
if ($staleFiles.Count -gt 0) {
    if ($RemoveStaleFiles) {
        $removedStale = 0
        while ($staleFiles.Count -gt 0) {
            $removedStale += Remove-StaleRuntimeFiles -Root $RuntimeRoot -Files $staleFiles
            $staleFiles = @(Get-StaleRuntimeFiles -Source $SourceRoot -Destination $RuntimeRoot -Files $sourceFiles)
        }
        Write-Host "removed_stale_files=$removedStale"
        Write-Host "stale_files_after_remove=$($staleFiles.Count)"
    }
    if ($staleFiles.Count -gt 0) {
        $staleFiles | Select-Object -First 50 | ForEach-Object { Write-Host "stale_runtime $($_.RelativePath)" }
        throw "Unity runtime has stale files. Re-run with -RemoveStaleFiles to delete them."
    }
}

if (!$SkipProjectPatch) {
    $missingCompileItemsRemovedTotal = 0
    foreach ($generatedProject in Get-ChildItem -LiteralPath $UnityProjectRoot -File -Filter '*.csproj') {
        $missingCompileItems = @(Remove-MissingAssetCompileItems `
            -ProjectFile $generatedProject.FullName `
            -ProjectRoot $UnityProjectRoot)
        $missingCompileItemsRemovedTotal += $missingCompileItems.Count
        if ($missingCompileItems.Count -gt 0 -or $VerboseFiles) {
            Write-Host "missing_asset_compile_items project=$($generatedProject.Name) removed=$($missingCompileItems.Count)"
        }
        if ($VerboseFiles) {
            $missingCompileItems | ForEach-Object { Write-Host "missing_asset_compile_removed $($generatedProject.Name) $_" }
        }
        $missingAssetItems = @(Remove-MissingAssetItems `
            -ProjectFile $generatedProject.FullName `
            -ProjectRoot $UnityProjectRoot)
        if ($missingAssetItems.Count -gt 0 -or $VerboseFiles) {
            Write-Host "missing_asset_items project=$($generatedProject.Name) removed=$($missingAssetItems.Count)"
        }
        if ($VerboseFiles) {
            $missingAssetItems | ForEach-Object { Write-Host "missing_asset_item_removed $($generatedProject.Name) $_" }
        }
    }
    Write-Host "missing_asset_compile_items_removed=$missingCompileItemsRemovedTotal"

    $assemblyProject = Join-Path $UnityProjectRoot 'Assembly-CSharp.csproj'
    $runtimeRemoved = @(Remove-StaleCompileItems -Source $SourceRoot -ProjectFile $assemblyProject)
    Write-Host "runtime_compile_items_removed=$($runtimeRemoved.Count)"
    if ($VerboseFiles) {
        $runtimeRemoved | ForEach-Object { Write-Host "runtime_compile_removed $_" }
    }
    $runtimeAdded = @(Add-MissingCompileItems -Source $SourceRoot -ProjectFile $assemblyProject)
    Write-Host "runtime_compile_items_added=$($runtimeAdded.Count)"
    if ($VerboseFiles) {
        $runtimeAdded | ForEach-Object { Write-Host "runtime_compile_added $_" }
    }

    $editorProject = Join-Path $UnityProjectRoot 'Assembly-CSharp-Editor.csproj'
    $editorRemoved = @(Remove-StaleCompileItems -Source $SourceRoot -ProjectFile $editorProject -EditorOnly)
    Write-Host "editor_compile_items_removed=$($editorRemoved.Count)"
    if ($VerboseFiles) {
        $editorRemoved | ForEach-Object { Write-Host "editor_compile_removed $_" }
    }
    Write-Host "compile_items_removed=$($runtimeRemoved.Count + $editorRemoved.Count)"
    $editorAdded = @(Add-MissingCompileItems -Source $SourceRoot -ProjectFile $editorProject -EditorOnly)
    Write-Host "editor_compile_items_added=$($editorAdded.Count)"
    if ($VerboseFiles) {
        $editorAdded | ForEach-Object { Write-Host "editor_compile_added $_" }
    }

    Write-Host "compile_items_added=$($runtimeAdded.Count + $editorAdded.Count)"

    $asmdefRemovedTotal = 0
    $asmdefAddedTotal = 0
    $asmdefProjectReferencesRemovedTotal = 0
    $asmdefProjectReferencesAddedTotal = 0
    $projectsByReference = Get-LocalAsmdefProjectMap -Source $SourceRoot
    $localProjectFiles = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($projectName in $projectsByReference.Values) {
        [void]$localProjectFiles.Add([string]$projectName)
    }
    $retiredProjectReferencesRemovedTotal = 0
    foreach ($generatedProject in Get-ChildItem -LiteralPath $UnityProjectRoot -File -Filter '*.csproj') {
        $retiredProjectReferences = @(Remove-RetiredTriSyncProjectReferences `
            -ProjectFile $generatedProject.FullName `
            -ActiveProjectFiles $localProjectFiles)
        $retiredProjectReferencesRemovedTotal += $retiredProjectReferences.Count
        if ($retiredProjectReferences.Count -gt 0 -or $VerboseFiles) {
            Write-Host "retired_product_project_references project=$($generatedProject.Name) removed=$($retiredProjectReferences.Count)"
        }
        if ($VerboseFiles) {
            $retiredProjectReferences | ForEach-Object { Write-Host "retired_product_project_reference_removed $($generatedProject.Name) $_" }
        }
    }
    Write-Host "retired_product_project_references_removed=$retiredProjectReferencesRemovedTotal"
    foreach ($asmdef in Get-ChildItem -LiteralPath (Join-Path $SourceRoot 'Scripts') -Recurse -File -Filter '*.asmdef') {
        $parsed = Get-Content -LiteralPath $asmdef.FullName -Raw | ConvertFrom-Json
        $projectFile = Join-Path $UnityProjectRoot "$($parsed.name).csproj"
        if (!(Test-Path -LiteralPath $projectFile)) {
            throw "Asmdef project not found: $projectFile. Open Unity once to regenerate project files."
        }
        $expected = Get-ExpectedCompileIncludesForAsmdef -Source $SourceRoot -AsmdefPath $asmdef.FullName
        $removed = @(Remove-StaleCompileItemsByExpected -ProjectFile $projectFile -Expected $expected)
        $added = @(Add-MissingCompileItemsByExpected -ProjectFile $projectFile -Expected $expected)
        $asmdefRemovedTotal += $removed.Count
        $asmdefAddedTotal += $added.Count
        Write-Host "asmdef_compile name=$($parsed.name) expected=$($expected.Count) removed=$($removed.Count) added=$($added.Count)"
        if ($VerboseFiles) {
            $removed | ForEach-Object { Write-Host "asmdef_compile_removed $($parsed.name) $_" }
            $added | ForEach-Object { Write-Host "asmdef_compile_added $($parsed.name) $_" }
        }

        $expectedProjectReferences = Get-ExpectedProjectReferencesForAsmdef `
            -ParsedAsmdef $parsed `
            -ProjectsByReference $projectsByReference
        $removedProjectReferences = @(Remove-StaleProjectReferencesByExpected `
            -ProjectFile $projectFile `
            -Expected $expectedProjectReferences `
            -LocalProjectFiles $localProjectFiles)
        $addedProjectReferences = @(Add-MissingProjectReferencesByExpected `
            -ProjectFile $projectFile `
            -Expected $expectedProjectReferences)
        $asmdefProjectReferencesRemovedTotal += $removedProjectReferences.Count
        $asmdefProjectReferencesAddedTotal += $addedProjectReferences.Count
        Write-Host "asmdef_project_references name=$($parsed.name) expected=$($expectedProjectReferences.Count) removed=$($removedProjectReferences.Count) added=$($addedProjectReferences.Count)"
        if ($VerboseFiles) {
            $removedProjectReferences | ForEach-Object { Write-Host "asmdef_project_reference_removed $($parsed.name) $_" }
            $addedProjectReferences | ForEach-Object { Write-Host "asmdef_project_reference_added $($parsed.name) $_" }
        }
    }
    Write-Host "asmdef_compile_items_removed=$asmdefRemovedTotal"
    Write-Host "asmdef_compile_items_added=$asmdefAddedTotal"
    Write-Host "asmdef_project_references_removed=$asmdefProjectReferencesRemovedTotal"
    Write-Host "asmdef_project_references_added=$asmdefProjectReferencesAddedTotal"
}

$diff = @(Compare-UnityMirror -Source $SourceRoot -Destination $RuntimeRoot -Files $sourceFiles)
Write-Host "mirror_diffs=$($diff.Count)"
if ($diff.Count -gt 0) {
    $diff | Select-Object -First 50 | ForEach-Object { Write-Host $_ }
    throw "Unity runtime mirror differs from source."
}

if (!$SkipBuild) {
    $buildProjects = New-Object 'System.Collections.Generic.HashSet[string]' ([System.StringComparer]::OrdinalIgnoreCase)
    foreach ($asmdef in Get-ChildItem -LiteralPath (Join-Path $SourceRoot 'Scripts') -Recurse -File -Filter '*.asmdef') {
        $parsed = Get-Content -LiteralPath $asmdef.FullName -Raw | ConvertFrom-Json
        $projectFile = Join-Path $UnityProjectRoot "$($parsed.name).csproj"
        if (!(Test-Path -LiteralPath $projectFile)) {
            throw "Asmdef project not found: $projectFile. Open Unity once to regenerate project files."
        }
        [void]$buildProjects.Add($projectFile)
    }

    foreach ($projectName in @('Assembly-CSharp.csproj', 'Assembly-CSharp-Editor.csproj')) {
        $projectFile = Join-Path $UnityProjectRoot $projectName
        if (Test-ProjectHasCompileItems -ProjectFile $projectFile) {
            [void]$buildProjects.Add($projectFile)
        }
        else {
            Write-Host "dotnet_build_skip_empty project=$projectName"
        }
    }

    foreach ($buildProject in @($buildProjects | Sort-Object)) {
        Write-Host "dotnet_build_project=$([System.IO.Path]::GetFileName($buildProject))"
        dotnet build $buildProject --nologo --verbosity minimal --warnaserror
        if ($LASTEXITCODE -ne 0) {
            throw "dotnet build failed for '$buildProject' with exit code $LASTEXITCODE"
        }
    }
}

Write-Host "sync_validation_ok"
