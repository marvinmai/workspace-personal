# Optional personal PowerShell helpers.
#
# Run this script directly or through `ai-workspace link` (python -m bootstrap link). The operation is
# opt-in, and each helper preserves an existing unmanaged command.

[CmdletBinding()]
param(
    [switch]$Quiet,
    [switch]$List
)

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'
$scriptRoot = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent (Resolve-Path $MyInvocation.MyCommand.Path)
}
$manifestPath = Join-Path $scriptRoot 'shell-extras.psd1.ps1'

if (-not (Test-Path -LiteralPath $manifestPath -PathType Leaf)) {
    throw "Shell-extra manifest not found at '$manifestPath'."
}

$helpers = @(& $manifestPath)
if (-not $helpers) {
    throw "Shell-extra manifest '$manifestPath' did not define any helpers."
}

if ($List) {
    $nameWidth = ($helpers | ForEach-Object { $_.Name.Length } |
        Measure-Object -Maximum).Maximum
    foreach ($helper in $helpers) {
        Write-Host ("  {0,-$nameWidth}  {1}" -f $helper.Name, $helper.Description)
    }
    exit 0
}

function Write-Header {
    param([string]$Message)
    if (-not $Quiet) {
        Write-Host ''
        Write-Host "=== $Message ===" -ForegroundColor Cyan
    }
}

foreach ($helper in $helpers) {
    $helperScript = Join-Path $scriptRoot $helper.Script
    if (-not (Test-Path -LiteralPath $helperScript -PathType Leaf)) {
        throw "Shell-extra installer '$helperScript' for '$($helper.Name)' was not found."
    }

    Write-Header "$($helper.Name) -- $($helper.Description)"
    & $helperScript -Quiet:$Quiet
    if (-not $?) {
        throw "Shell-extra installer '$helperScript' failed."
    }
}

if (-not $Quiet) {
    Write-Host ''
    Write-Host 'Personal shell extras setup finished.' -ForegroundColor Green
}
