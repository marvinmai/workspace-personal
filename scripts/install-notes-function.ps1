# Installs a `notes` function for PowerShell 7 that changes to the user's
# Obsidian notes directory.
#
# - Idempotent: re-running updates the managed block in place.
# - Non-fatal: an unavailable notes directory skips installation successfully.
# - Never overwrites an existing, unmanaged `notes` function or alias.

[CmdletBinding()]
param(
    [string] $NotesPath = '',
    [switch] $Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

# Default location comes from the per-machine ai-workspace config, never from code.
function Get-AiWorkspaceConfigValue {
    param([Parameter(Mandatory = $true)][string]$Key)
    $base = if ($env:APPDATA) { $env:APPDATA }
            elseif ($env:XDG_CONFIG_HOME) { $env:XDG_CONFIG_HOME }
            else { Join-Path $HOME '.config' }
    $file = Join-Path (Join-Path $base 'ai-workspace') 'config.json'
    if (-not (Test-Path -LiteralPath $file -PathType Leaf)) { return '' }
    $value = (Get-Content -LiteralPath $file -Raw | ConvertFrom-Json).$Key
    if ($value) { return [string]$value } else { return '' }
}

if ([string]::IsNullOrWhiteSpace($NotesPath)) { $NotesPath = Get-AiWorkspaceConfigValue 'notes_dir' }

function Write-Info {
    param([Parameter(Mandatory = $true)][string]$Message)
    if (-not $Quiet) {
        Write-Host $Message
    }
}

if ($PSEdition -ne 'Core' -or $PSVersionTable.PSVersion.Major -lt 7) {
    Write-Info "'notes' shortcut requires PowerShell 7 (pwsh) -- skipping installation."
    return
}

if ([string]::IsNullOrWhiteSpace($NotesPath)) {
    Write-Info "No notes_dir in the ai-workspace config -- skipping 'notes' shortcut installation."
    return
}

if (-not (Test-Path -LiteralPath $NotesPath -PathType Container)) {
    Write-Info "Notes directory not found -- skipping 'notes' shortcut installation: $NotesPath"
    return
}

$resolvedNotesPath = (Resolve-Path -LiteralPath $NotesPath -ErrorAction Stop).Path
$beginMarker = '# >>> workspace-personal: notes function >>>'
$endMarker   = '# <<< workspace-personal: notes function <<<'

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

function Get-CurrentProfilePaths {
    return @(
        $PROFILE.CurrentUserAllHosts,
        $PROFILE.CurrentUserCurrentHost,
        $PROFILE.AllUsersAllHosts,
        $PROFILE.AllUsersCurrentHost
    )
}

$targetProfile = [string]$PROFILE.CurrentUserAllHosts
if ([string]::IsNullOrWhiteSpace($targetProfile)) {
    Write-Info 'No PowerShell 7 profile could be resolved -- skipping installation.'
    return
}

$scanProfilePaths = @(Get-CurrentProfilePaths)
$profileDir = Split-Path -Parent $targetProfile
$scanProfilePaths += @(
    (Join-Path $profileDir 'Microsoft.PowerShell_profile.ps1'),
    (Join-Path $profileDir 'Microsoft.PowerShellISE_profile.ps1'),
    (Join-Path $profileDir 'Microsoft.VSCode_profile.ps1')
)

Assert-ProfileMarkerPreflight -Paths (
    @($targetProfile) + @($scanProfilePaths)
)

$scanProfilePaths = @(
    @($targetProfile) + @($scanProfilePaths) |
        Where-Object {
            $_ -and (Test-Path -LiteralPath $_ -PathType Leaf)
        } |
        Select-Object -Unique
)

$escapedPath = $resolvedNotesPath.Replace("'", "''")
$block = @"

$beginMarker
function notes {
    Set-Location -LiteralPath '$escapedPath'
}
$endMarker
"@

$profileInfo = Read-Profile -Path $targetProfile
$existing = if ($null -ne $profileInfo) { [string]$profileInfo.Content } else { '' }
$encoding = if ($null -ne $profileInfo) { $profileInfo.Encoding } else { New-ProfileEncoding }

if ($existing.Contains($beginMarker)) {
    $startIdx = $existing.IndexOf($beginMarker)
    $endIdx = $existing.IndexOf($endMarker, $startIdx + $beginMarker.Length)
    if ($endIdx -lt $startIdx) {
        throw "Malformed 'notes' block in $targetProfile (markers out of order)."
    }
    $before = $existing.Substring(0, $startIdx)
    $after = $existing.Substring($endIdx + $endMarker.Length)
    $updated = $before + $block.Trim() + $after
    if ($updated -eq $existing) {
        Write-Info "'notes' shortcut already up to date in $targetProfile."
    } else {
        Write-Profile -Path $targetProfile -Content $updated -Encoding $encoding
        Write-Info "Updated 'notes' shortcut in $targetProfile."
    }
    Write-Info 'Open a new PowerShell 7 session to start using it.'
    return
}

$conflict = $scanProfilePaths | Where-Object {
    $candidate = Read-Profile -Path $_
    $content = if ($null -ne $candidate) { [string]$candidate.Content } else { '' }
    $content -and ($content -notmatch [regex]::Escape($beginMarker)) -and
        (Test-ProfileCommandConflict -Content $content -CommandName 'notes')
} | Select-Object -First 1
if ($null -ne $conflict) {
    Write-Info "A 'notes' function or alias already exists in $conflict - leaving $targetProfile untouched."
    return
}

$targetDir = Split-Path -Parent $targetProfile
if (-not (Test-Path -LiteralPath $targetDir)) {
    New-Item -ItemType Directory -Path $targetDir -Force | Out-Null
}
Write-Profile -Path $targetProfile -Content ($existing + $block) -Encoding $encoding
Write-Info "Installed 'notes' shortcut into $targetProfile."
Write-Info 'Open a new PowerShell 7 session to start using it.'
