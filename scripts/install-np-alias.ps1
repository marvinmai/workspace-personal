# Installs an `np` shortcut for Notepad++ into the current user's PowerShell
# profile, if Notepad++ is installed on this machine.
#
# - Idempotent: re-running keeps the shortcut in sync with the discovered path.
# - Non-fatal: if Notepad++ is not found, prints a hint and returns successfully.
# - Never overwrites an existing `np` function/alias in $PROFILE.

[CmdletBinding()]
param(
    [switch] $Quiet
)

$ErrorActionPreference = 'Stop'

function Write-Info($msg) { if (-not $Quiet) { Write-Host $msg } }

$isWindowsPlatform = if (Test-Path Variable:\IsWindows) { [bool]$IsWindows } else { $true }

function Find-NotepadPlusPlus {
    if (-not $isWindowsPlatform) {
        return $null
    }

    $candidates = @(
        "$env:ProgramFiles\Notepad++\notepad++.exe",
        "${env:ProgramFiles(x86)}\Notepad++\notepad++.exe",
        "$env:LOCALAPPDATA\Programs\Notepad++\notepad++.exe"
    )
    foreach ($p in $candidates) {
        if ($p -and (Test-Path -LiteralPath $p)) { return (Resolve-Path -LiteralPath $p).Path }
    }

    $registryPaths = @(
        'HKLM:\SOFTWARE\Microsoft\Windows\CurrentVersion\App Paths\notepad++.exe',
        'HKLM:\SOFTWARE\WOW6432Node\Microsoft\Windows\CurrentVersion\App Paths\notepad++.exe'
    )
    foreach ($reg in $registryPaths) {
        $regItem = Get-ItemProperty -Path $reg -ErrorAction SilentlyContinue
        $val = if ($regItem) { $regItem.'(default)' } else { $null }
        if ($val -and (Test-Path -LiteralPath $val)) { return $val }
    }

    $onPath = Get-Command notepad++.exe -ErrorAction SilentlyContinue
    if ($onPath) { return $onPath.Source }

    return $null
}

$nppPath = Find-NotepadPlusPlus
if (-not $nppPath) {
    Write-Info "Notepad++ not found -- skipping 'np' shortcut installation."
    Write-Info "Install Notepad++ from https://notepad-plus-plus.org/ and re-run this script."
    return
}
Write-Info "Found Notepad++ at: $nppPath"

$beginMarker = '# >>> workspace-personal: np alias >>>'
$endMarker   = '# <<< workspace-personal: np alias <<<'

function Resolve-ProfilePaths {
    param([Parameter(Mandatory = $true)][string]$Interpreter)

    $command = Get-Command $Interpreter -ErrorAction SilentlyContinue
    if (-not $command) {
        return @()
    }
    $commandPath = if ($command.Source) { $command.Source } else { $command.Path }
    if (-not $commandPath) {
        return @()
    }

    $profileQuery = '$paths = @($PROFILE.CurrentUserAllHosts, $PROFILE.CurrentUserCurrentHost, ' +
        '$PROFILE.AllUsersAllHosts, $PROFILE.AllUsersCurrentHost); ' +
        '[Convert]::ToBase64String([Text.Encoding]::UTF8.GetBytes(($paths -join [char]10)))'

    $startInfo = [System.Diagnostics.ProcessStartInfo]::new()
    $startInfo.FileName = $commandPath
    $startInfo.UseShellExecute = $false
    $startInfo.CreateNoWindow = $true
    $startInfo.RedirectStandardOutput = $true
    $startInfo.RedirectStandardError = $true
    if ($null -ne $startInfo.PSObject.Properties['ArgumentList']) {
        [void]$startInfo.ArgumentList.Add('-NoProfile')
        [void]$startInfo.ArgumentList.Add('-NonInteractive')
        [void]$startInfo.ArgumentList.Add('-Command')
        [void]$startInfo.ArgumentList.Add($profileQuery)
    } else {
        $startInfo.Arguments = '-NoProfile -NonInteractive -Command "' +
            $profileQuery.Replace('"', '\"') + '"'
    }

    $process = [System.Diagnostics.Process]::new()
    try {
        $process.StartInfo = $startInfo
        if (-not $process.Start()) {
            throw "PowerShell profile discovery could not start '$commandPath'."
        }
        $stdoutTask = $process.StandardOutput.ReadToEndAsync()
        $stderrTask = $process.StandardError.ReadToEndAsync()
        if (-not $process.WaitForExit(15000)) {
            $process.Kill()
            $process.WaitForExit()
            throw "PowerShell profile discovery timed out for '$commandPath'."
        }
        $output = $stdoutTask.GetAwaiter().GetResult()
        $errorOutput = $stderrTask.GetAwaiter().GetResult()
        if ($process.ExitCode -ne 0) {
            $detail = if ($errorOutput) { ": $($errorOutput.Trim())" } else { '' }
            throw "PowerShell profile discovery failed for '$commandPath' " +
                "with exit code $($process.ExitCode)$detail"
        }
    } finally {
        $process.Dispose()
    }

    $payload = $output.Trim()
    if ($payload -notmatch '^[A-Za-z0-9+/]+={0,2}$' -or
        ($payload.Length % 4) -ne 0) {
        throw "PowerShell profile discovery returned malformed Base64 data."
    }
    try {
        $bytes = [Convert]::FromBase64String($payload)
        $decoded = [System.Text.UTF8Encoding]::new($false, $true).GetString($bytes)
    } catch [FormatException] {
        throw "PowerShell profile discovery returned malformed Base64 data."
    } catch [System.Text.DecoderFallbackException] {
        throw "PowerShell profile discovery returned invalid UTF-8 data."
    }

    $paths = @([System.Text.RegularExpressions.Regex]::Split($decoded, "`n"))
    if ($paths.Count -ne 4) {
        throw "PowerShell profile discovery returned $($paths.Count) paths; expected exactly four."
    }
    $seen = @{}
    $validated = @()
    foreach ($path in $paths) {
        if ($path.EndsWith("`r")) {
            $path = $path.Substring(0, $path.Length - 1)
        }
        if (-not $path -or [string]::IsNullOrWhiteSpace($path) -or
            $path.Contains("`r") -or $path.Contains([char]0)) {
            throw "PowerShell profile discovery returned an empty or invalid path."
        }
        if (-not [System.IO.Path]::IsPathRooted($path)) {
            throw "PowerShell profile discovery returned a non-rooted path: '$path'."
        }
        try {
            $fullPath = [System.IO.Path]::GetFullPath($path)
        } catch {
            throw "PowerShell profile discovery returned an invalid path: '$path'."
        }
        $key = $fullPath.ToUpperInvariant()
        if ($seen.ContainsKey($key)) {
            throw "PowerShell profile discovery returned duplicate profile paths."
        }
        $seen[$key] = $true
        $validated += $path
    }
    return @($validated)
}

function Get-CurrentProfilePaths {
    return @(
        $PROFILE.CurrentUserAllHosts,
        $PROFILE.CurrentUserCurrentHost,
        $PROFILE.AllUsersAllHosts,
        $PROFILE.AllUsersCurrentHost
    )
}

function Test-BytePrefix {
    param(
        [Parameter(Mandatory = $true)][byte[]]$Bytes,
        [Parameter(Mandatory = $true)][byte[]]$Prefix
    )

    if ($Bytes.Length -lt $Prefix.Length) {
        return $false
    }
    for ($i = 0; $i -lt $Prefix.Length; $i++) {
        if ($Bytes[$i] -ne $Prefix[$i]) {
            return $false
        }
    }
    return $true
}

function New-ProfileEncoding {
    if ($PSVersionTable.PSEdition -eq 'Desktop') {
        return [System.Text.UTF8Encoding]::new($true, $true)
    }
    return [System.Text.UTF8Encoding]::new($false, $true)
}

function Get-ProfileEncoding {
    param([Parameter(Mandatory = $true)][byte[]]$Bytes)

    if (Test-BytePrefix $Bytes ([byte[]](0xFF, 0xFE, 0x00, 0x00))) {
        return [System.Text.UTF32Encoding]::new($false, $true, $true)
    }
    if (Test-BytePrefix $Bytes ([byte[]](0x00, 0x00, 0xFE, 0xFF))) {
        return [System.Text.UTF32Encoding]::new($true, $true, $true)
    }
    if (Test-BytePrefix $Bytes ([byte[]](0xFF, 0xFE))) {
        return [System.Text.UnicodeEncoding]::new($false, $true, $true)
    }
    if (Test-BytePrefix $Bytes ([byte[]](0xFE, 0xFF))) {
        return [System.Text.UnicodeEncoding]::new($true, $true, $true)
    }
    if (Test-BytePrefix $Bytes ([byte[]](0xEF, 0xBB, 0xBF))) {
        return [System.Text.UTF8Encoding]::new($true, $true)
    }
    return [System.Text.UTF8Encoding]::new($false, $true)
}

function Read-Profile {
    param([Parameter(Mandatory = $true)][string]$Path)

    if (-not [System.IO.File]::Exists($Path)) {
        return $null
    }

    $bytes = [System.IO.File]::ReadAllBytes($Path)
    $encoding = Get-ProfileEncoding $bytes
    $preambleLength = $encoding.GetPreamble().Length
    $content = $encoding.GetString(
        $bytes, $preambleLength, $bytes.Length - $preambleLength
    )
    return [pscustomobject]@{
        Content  = $content
        Encoding = $encoding
    }
}

function Write-Profile {
    param(
        [Parameter(Mandatory = $true)][string]$Path,
        [Parameter(Mandatory = $true)][string]$Content,
        [Parameter(Mandatory = $true)][System.Text.Encoding]$Encoding
    )

    $preamble = $Encoding.GetPreamble()
    $body = $Encoding.GetBytes($Content)
    $bytes = [byte[]]::new($preamble.Length + $body.Length)
    [System.Array]::Copy($preamble, 0, $bytes, 0, $preamble.Length)
    [System.Array]::Copy($body, 0, $bytes, $preamble.Length, $body.Length)
    [System.IO.File]::WriteAllBytes($Path, $bytes)
}

function Assert-ManagedMarkers {
    param(
        [Parameter(Mandatory = $true)][string]$Content,
        [Parameter(Mandatory = $true)][string]$Path
    )

    $beginPattern = '(?m)# >>> workspace-personal: (?<name>.*?) >>>'
    $endPattern = '(?m)# <<< workspace-personal: (?<name>.*?) <<<'
    $events = @(
        [regex]::Matches($Content, $beginPattern) | ForEach-Object {
            [pscustomobject]@{
                Position = $_.Index
                Kind     = 'begin'
                Name     = $_.Groups['name'].Value
            }
        }
        [regex]::Matches($Content, $endPattern) | ForEach-Object {
            [pscustomobject]@{
                Position = $_.Index
                Kind     = 'end'
                Name     = $_.Groups['name'].Value
            }
        }
    ) | Sort-Object Position

    $activeName = $null
    $completed = @{}
    foreach ($event in $events) {
        if ([string]::IsNullOrWhiteSpace($event.Name)) {
            throw "Malformed workspace-personal markers in '$Path': marker name is empty."
        }
        if ($event.Kind -eq 'begin') {
            if ($null -ne $activeName) {
                throw "Malformed workspace-personal markers in '$Path': nested managed blocks are not supported."
            }
            if ($completed.ContainsKey($event.Name)) {
                throw "Malformed workspace-personal markers in '$Path': managed block '$($event.Name)' appears more than once."
            }
            $activeName = $event.Name
        } elseif ($null -eq $activeName) {
            throw "Malformed workspace-personal markers in '$Path': end marker '$($event.Name)' has no matching begin marker."
        } elseif ($activeName -ne $event.Name) {
            throw "Malformed workspace-personal markers in '$Path': end marker '$($event.Name)' does not match active block '$activeName'."
        } else {
            $completed[$activeName] = $true
            $activeName = $null
        }
    }
    if ($null -ne $activeName) {
        throw "Malformed workspace-personal markers in '$Path': begin marker '$activeName' has no matching end marker."
    }
}

function Assert-ProfileMarkerPreflight {
    param([Parameter(Mandatory = $true)][object[]]$Paths)

    $seen = @{}
    foreach ($rawPath in $Paths) {
        if ([string]::IsNullOrWhiteSpace([string]$rawPath)) {
            continue
        }
        $path = [System.IO.Path]::GetFullPath([string]$rawPath)
        $key = $path.ToLowerInvariant()
        if ($seen.ContainsKey($key)) {
            continue
        }
        $seen[$key] = $true
        if (Test-Path -LiteralPath $path -PathType Container) {
            throw "Profile path '$path' exists but is not a file. No changes were written."
        }
        $profileInfo = Read-Profile -Path $path
        if ($null -ne $profileInfo) {
            Assert-ManagedMarkers -Content ([string]$profileInfo.Content) -Path $path
        }
    }
}

function Test-ProfileCommandConflict {
    param(
        [Parameter(Mandatory = $true)][string]$Content,
        [Parameter(Mandatory = $true)][string]$CommandName
    )

    $escapedName = [regex]::Escape($CommandName)
    $functionPattern = '(?im)^\s*function\s+' + $escapedName + '\b'
    $positionalAliasPattern = '(?im)^\s*(?:Set|New)-Alias\s+["'']?' +
        $escapedName + '["'']?(?=\s|$)'
    $namedAliasPattern = '(?im)^\s*(?:Set|New)-Alias\b[^\r\n]*?(?<!\S)-Name' +
        '(?:\s+|[:=]\s*)["'']?' + $escapedName + '["'']?(?=\s|$)'

    return (
        ($Content -match $functionPattern) -or
        ($Content -match $positionalAliasPattern) -or
        ($Content -match $namedAliasPattern)
    )
}

$targetAllHostsProfiles = @($PROFILE.CurrentUserAllHosts)
$scanProfilePaths = @(Get-CurrentProfilePaths)
if ($isWindowsPlatform) {
    foreach ($interpreter in @('pwsh', 'powershell')) {
        $resolved = @(Resolve-ProfilePaths $interpreter)
        if ($resolved.Count -gt 0) {
            $targetAllHostsProfiles += $resolved[0]
            $scanProfilePaths += $resolved
        }
    }
}
$targetAllHostsProfiles = @(
    $targetAllHostsProfiles |
        Where-Object { $_ -and -not [string]::IsNullOrWhiteSpace([string]$_) } |
        Select-Object -Unique
)
if ($targetAllHostsProfiles.Count -eq 0) {
    Write-Info 'No PowerShell profile could be resolved - skipping installation.'
    return
}

$scanProfilePaths += $targetAllHostsProfiles

foreach ($target in $targetAllHostsProfiles) {
    $profileDir = Split-Path -Parent $target
    $scanProfilePaths += @(
        (Join-Path $profileDir 'Microsoft.PowerShell_profile.ps1'),
        (Join-Path $profileDir 'Microsoft.PowerShellISE_profile.ps1'),
        (Join-Path $profileDir 'Microsoft.VSCode_profile.ps1')
    )
}

Assert-ProfileMarkerPreflight -Paths (
    @($targetAllHostsProfiles) + @($scanProfilePaths)
)

$scanProfilePaths = $scanProfilePaths |
    Where-Object { $_ -and (Test-Path -LiteralPath $_ -PathType Leaf) } |
    Select-Object -Unique

$escapedPath = $nppPath.Replace("'", "''")
$block = @"

$beginMarker
function np {
    param([Parameter(ValueFromRemainingArguments = `$true)] [string[]] `$Args)
    & '$escapedPath' @Args
}
$endMarker
"@

foreach ($target in $targetAllHostsProfiles) {
    $profileInfo = Read-Profile -Path $target
    $existing = if ($profileInfo) { [string]$profileInfo.Content } else { '' }
    $encoding = if ($profileInfo) { $profileInfo.Encoding } else { New-ProfileEncoding }
    if ($existing -and $existing.Contains($beginMarker)) {
        $startIdx = $existing.IndexOf($beginMarker)
        $endIdx = $existing.IndexOf($endMarker, $startIdx + $beginMarker.Length)
        $before = $existing.Substring(0, $startIdx)
        $after = $existing.Substring($endIdx + $endMarker.Length)
        $updated = $before + $block.Trim() + $after
        if ($updated -eq $existing) {
            Write-Info "'np' shortcut already up to date in $target."
        } else {
            Write-Profile -Path $target -Content $updated -Encoding $encoding
            Write-Info "Updated 'np' shortcut in $target."
        }
        continue
    }

    $conflict = $scanProfilePaths | Where-Object {
        $candidate = Read-Profile -Path $_
        $c = if ($candidate) { [string]$candidate.Content } else { '' }
        $c -and ($c -notmatch [regex]::Escape($beginMarker)) -and
            (Test-ProfileCommandConflict -Content $c -CommandName 'np')
    } | Select-Object -First 1
    if ($conflict) {
        Write-Info "An 'np' function or alias already exists in $conflict - leaving $target untouched."
        continue
    }

    $targetDir = Split-Path -Parent $target
    if (-not (Test-Path -LiteralPath $targetDir)) {
        New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
    }
    Write-Profile -Path $target -Content ($existing + $block) -Encoding $encoding
    Write-Info "Installed 'np' shortcut into $target."
}

Write-Info 'Open a new PowerShell session to start using it.'
