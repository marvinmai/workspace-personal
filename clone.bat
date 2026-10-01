@echo off
setlocal
rem Launcher: runs the Python bootstrap (clone). Needs Python 3 (py launcher or python).

set "PYTHONPATH=%~dp0;%PYTHONPATH%"

py -3 --version >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    py -3 -m bootstrap clone %*
    goto :done
)
python --version >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    python -m bootstrap clone %*
    goto :done
)

echo Python 3 was not found.
where winget >nul 2>nul
if %ERRORLEVEL% EQU 0 (
    echo Install it with:  winget install Python.Python.3.12
) else (
    echo Install Python 3 from https://www.python.org/downloads/ and re-run.
)
exit /b 1

:done
if not %ERRORLEVEL% EQU 0 (
    echo clone failed.
    exit /b %ERRORLEVEL%
)
