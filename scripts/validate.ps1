<#
.SYNOPSIS
    Run lightweight syntax and command-help checks for this workspace.

.DESCRIPTION
    Python files are compiled in memory, so validation does not create
    __pycache__ files. PowerShell files are parsed with the built-in parser.
    Python installer --help commands are also checked; no installer is run.
#>
[CmdletBinding()]
param()

Set-StrictMode -Version Latest
$ErrorActionPreference = 'Stop'

$scriptRoot = if ($PSScriptRoot) {
    $PSScriptRoot
} else {
    Split-Path -Parent (Resolve-Path $MyInvocation.MyCommand.Path)
}
$workspaceRoot = (Resolve-Path (Join-Path $scriptRoot '..')).Path

$isWindowsPlatform = if (Test-Path Variable:\IsWindows) { [bool]$IsWindows } else { $true }

function Resolve-Python3 {
    $candidates = if ($isWindowsPlatform) {
        @(
            [pscustomobject]@{ Name = 'py'; Prefix = @('-3') },
            [pscustomobject]@{ Name = 'python'; Prefix = @() }
        )
    } else {
        @(
            [pscustomobject]@{ Name = 'python3'; Prefix = @() },
            [pscustomobject]@{ Name = 'python'; Prefix = @() },
            [pscustomobject]@{ Name = 'py'; Prefix = @('-3') }
        )
    }

    foreach ($candidate in $candidates) {
        $command = Get-Command $candidate.Name -ErrorAction SilentlyContinue
        if (-not $command) {
            continue
        }

        $commandPath = if ($command.Source) { $command.Source } else { $command.Path }
        if (-not $commandPath) {
            continue
        }
        $prefix = @($candidate.Prefix)
        & $commandPath @prefix -c 'import sys; raise SystemExit(0 if sys.version_info[0] == 3 else 1)' *> $null
        if ($LASTEXITCODE -eq 0) {
            return [pscustomobject]@{
                Path   = $commandPath
                Prefix = $prefix
            }
        }
    }

    return $null
}

$python = Resolve-Python3
if (-not $python) {
    throw 'Python 3 is required for validation but was not found on PATH.'
}
$pythonCommand = $python.Path
$pythonPrefix = @($python.Prefix)

$pythonFiles = @(Get-ChildItem -LiteralPath (Join-Path $workspaceRoot 'applications') `
    -Filter '*.py' -File -Recurse)
if ($pythonFiles.Count -eq 0) {
    throw 'No Python installers were found under applications\.'
}

$compileScript = @'
import pathlib
import sys

failed = 0
for filename in sys.argv[1:]:
    path = pathlib.Path(filename)
    try:
        source = path.read_text(encoding="utf-8")
        compile(source, str(path), "exec")
    except (OSError, SyntaxError) as exc:
        print(f"{path}: {exc}", file=sys.stderr)
        failed += 1
raise SystemExit(failed)
'@

Write-Host "Compiling $($pythonFiles.Count) Python installer(s) in memory..."
$compileScript | & $pythonCommand @pythonPrefix - @($pythonFiles | ForEach-Object FullName)
$pythonExit = $LASTEXITCODE
if ($pythonExit -ne 0) {
    throw "Python syntax validation failed with exit code $pythonExit."
}

foreach ($pythonFile in $pythonFiles) {
    & $pythonCommand @pythonPrefix $pythonFile.FullName --help *> $null
    $helpExit = $LASTEXITCODE
    if ($helpExit -ne 0) {
        throw "Python help validation failed for '$($pythonFile.FullName)' with exit code $helpExit."
    }
}

$powerShellFiles = @(Get-ChildItem -LiteralPath (Join-Path $workspaceRoot 'scripts') `
    -Filter '*.ps1' -File -Recurse)
if ($powerShellFiles.Count -eq 0) {
    throw 'No PowerShell scripts were found under scripts\.'
}

$parseFailures = 0
foreach ($powerShellFile in $powerShellFiles) {
    $tokens = $null
    $errors = $null
    [System.Management.Automation.Language.Parser]::ParseFile(
        $powerShellFile.FullName, [ref]$tokens, [ref]$errors
    ) | Out-Null
    if ($errors.Count -gt 0) {
        $parseFailures += $errors.Count
        foreach ($errorRecord in $errors) {
            Write-Host "$($powerShellFile.FullName): $($errorRecord.Message)" -ForegroundColor Red
        }
    }
}
if ($parseFailures -gt 0) {
    throw "PowerShell syntax validation found $parseFailures error(s)."
}

Write-Host (
    "Validated $($pythonFiles.Count) Python installer(s) and " +
    "$($powerShellFiles.Count) PowerShell script(s)."
) -ForegroundColor Green
