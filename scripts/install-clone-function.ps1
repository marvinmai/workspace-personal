#Requires -Version 5.1
<#
.SYNOPSIS
    Installs or removes the personal clone command in a PowerShell profile.

.DESCRIPTION
    The installed function keeps the interactive Python clone command
    (python -m bootstrap clone) available as "clone" from any directory. It
    runs in a child process so exit paths cannot close the caller's shell.

.PARAMETER ProfilePath
    PowerShell profile to update (default: the current host's $PROFILE).

.PARAMETER RepositoryRoot
    Root of the workspace-personal checkout (default: the parent of this
    script's scripts directory).

.PARAMETER Uninstall
    Remove the managed clone function from the profile without deleting the
    profile itself.

.PARAMETER Force
    Install even when another command named "clone" already exists.

.PARAMETER Quiet
    Suppress informational output.
#>
[CmdletBinding()]
param(
    [string] $ProfilePath = $PROFILE,
    [string] $RepositoryRoot = (Split-Path -Parent $PSScriptRoot),
    [switch] $Uninstall,
    [switch] $Force,
    [switch] $Quiet
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

function Write-Info {
    param(
        [Parameter(Mandatory = $true)][string]$Message,
        [ConsoleColor]$Color = [ConsoleColor]::Gray
    )
    if (-not $Quiet) {
        Write-Host $Message -ForegroundColor $Color
    }
}

function Get-BlockPattern {
    param([string]$Start, [string]$End)
    '(?ms)^' + [regex]::Escape($Start) + '\r?\n.*?^' + [regex]::Escape($End) + '\r?\n?'
}

$startMarker = '# >>> workspace-personal: clone function >>>'
$endMarker = '# <<< workspace-personal: clone function <<<'
$managedMarkerPattern = '(?m)^' + [regex]::Escape($startMarker) + '\r?$'
$blockPattern = Get-BlockPattern $startMarker $endMarker

if ([string]::IsNullOrWhiteSpace($ProfilePath)) {
    throw 'PowerShell profile path is empty.'
}

$ProfilePath = [Environment]::ExpandEnvironmentVariables($ProfilePath)
if (-not [IO.Path]::IsPathRooted($ProfilePath)) {
    $ProfilePath = Join-Path (Get-Location) $ProfilePath
}
$ProfilePath = [IO.Path]::GetFullPath($ProfilePath)

$profileContent = if (Test-Path -LiteralPath $ProfilePath -PathType Leaf) {
    Get-Content -LiteralPath $ProfilePath -Raw
} else {
    ''
}
if ($null -eq $profileContent) {
    $profileContent = ''
}

if ($Uninstall) {
    if ($profileContent -notmatch $managedMarkerPattern) {
        Write-Info "No managed clone command found in $ProfilePath." Yellow
        return
    }

    $updatedProfile = [regex]::Replace($profileContent, $blockPattern, '')
    Set-Content -LiteralPath $ProfilePath -Value $updatedProfile -Encoding UTF8
    Write-Info "Removed the clone command from $ProfilePath." Green
    return
}

$RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$cloneModulePath = Join-Path $RepositoryRoot 'bootstrap\__main__.py'
if (-not (Test-Path -LiteralPath $cloneModulePath -PathType Leaf)) {
    throw "Bootstrap package not found at $cloneModulePath."
}

$hasManagedBlock = $profileContent -match $managedMarkerPattern
$existingCommand = Get-Command clone -ErrorAction SilentlyContinue
if ($existingCommand -and -not $hasManagedBlock -and -not $Force) {
    throw "A command named 'clone' already exists ($($existingCommand.CommandType): $($existingCommand.Source)). Use -Force to install the profile function anyway."
}
if ($existingCommand -and -not $hasManagedBlock -and $Force) {
    Write-Warning "Installing the profile function will take precedence over the existing 'clone' command."
}

$escapedRepositoryRoot = $RepositoryRoot.Replace("'", "''")
$profileBlock = @"
$startMarker
function clone {
    `$previous = `$env:PYTHONPATH
    `$env:PYTHONPATH = '$escapedRepositoryRoot'
    try {
        if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m bootstrap clone @args }
        else { & python3 -m bootstrap clone @args }
    } finally { `$env:PYTHONPATH = `$previous }
}
$endMarker
"@.Trim()

$profileDirectory = Split-Path -Parent $ProfilePath
if ($profileDirectory -and -not (Test-Path -LiteralPath $profileDirectory -PathType Container)) {
    New-Item -ItemType Directory -Path $profileDirectory -Force | Out-Null
}

if ($hasManagedBlock) {
    $updatedProfile = [regex]::Replace(
        $profileContent,
        $blockPattern,
        $profileBlock + [Environment]::NewLine
    )
} elseif ([string]::IsNullOrWhiteSpace($profileContent)) {
    $updatedProfile = $profileBlock + [Environment]::NewLine
} else {
    $updatedProfile = $profileContent.TrimEnd() +
        [Environment]::NewLine + [Environment]::NewLine +
        $profileBlock + [Environment]::NewLine
}

Set-Content -LiteralPath $ProfilePath -Value $updatedProfile -Encoding UTF8
Write-Info "Installed the clone command in $ProfilePath." Green
Write-Info "Run this in the current session: . `"$ProfilePath`"" Cyan
