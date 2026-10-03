#Requires -Version 5.1
<#
.SYNOPSIS
    Installs or removes the ai-link, ai-doctor, ai-setup and config commands
    in a PowerShell profile.

.DESCRIPTION
    Each installed function runs the matching bootstrap command
    (python -m bootstrap <command>) from any directory, passing its arguments
    on. It runs in a child process so exit paths cannot close the caller's
    shell.

.PARAMETER ProfilePath
    PowerShell profile to update (default: the current host's $PROFILE).

.PARAMETER RepositoryRoot
    Root of the workspace-personal checkout (default: the parent of this
    script's scripts directory).

.PARAMETER Uninstall
    Remove the managed bootstrap command functions from the profile without deleting the
    profile itself.

.PARAMETER Force
    Install even when another command with one of these names already exists.

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

# function name -> bootstrap command
$commands = [ordered]@{ 'ai-link' = 'link'; 'ai-doctor' = 'doctor'; 'ai-setup' = 'setup'; 'config' = 'config' }
$startMarker = '# >>> workspace-personal: bootstrap command functions >>>'
$endMarker = '# <<< workspace-personal: bootstrap command functions <<<'
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
        Write-Info "No managed bootstrap command functions found in $ProfilePath." Yellow
        return
    }

    $updatedProfile = [regex]::Replace($profileContent, $blockPattern, '')
    Set-Content -LiteralPath $ProfilePath -Value $updatedProfile -Encoding UTF8
    Write-Info "Removed the bootstrap command functions from $ProfilePath." Green
    return
}

$RepositoryRoot = (Resolve-Path -LiteralPath $RepositoryRoot).Path
$bootstrapModulePath = Join-Path $RepositoryRoot 'bootstrap\__main__.py'
if (-not (Test-Path -LiteralPath $bootstrapModulePath -PathType Leaf)) {
    throw "Bootstrap package not found at $bootstrapModulePath."
}

$hasManagedBlock = $profileContent -match $managedMarkerPattern
foreach ($name in $commands.Keys) {
    $existingCommand = Get-Command $name -ErrorAction SilentlyContinue
    if ($existingCommand -and -not $hasManagedBlock -and -not $Force) {
        throw "A command named '$name' already exists ($($existingCommand.CommandType): $($existingCommand.Source)). Use -Force to install the profile functions anyway."
    }
    if ($existingCommand -and -not $hasManagedBlock -and $Force) {
        Write-Warning "Installing the profile function will take precedence over the existing '$name' command."
    }
}

$escapedRepositoryRoot = $RepositoryRoot.Replace("'", "''")
$functions = foreach ($name in $commands.Keys) {
    $command = $commands[$name]
@"
function $name {
    `$previous = `$env:PYTHONPATH
    `$env:PYTHONPATH = '$escapedRepositoryRoot'
    try {
        if (Get-Command py -ErrorAction SilentlyContinue) { & py -3 -m bootstrap $command @args }
        else { & python3 -m bootstrap $command @args }
    } finally { `$env:PYTHONPATH = `$previous }
}
"@
}
$profileBlock = (@($startMarker) + $functions + @($endMarker)) -join [Environment]::NewLine

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
Write-Info "Installed ai-link, ai-doctor, ai-setup and config in $ProfilePath." Green
Write-Info "Run this in the current session: . `"$ProfilePath`"" Cyan
