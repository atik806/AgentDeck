@echo off
rem ===========================================================================
rem  Windows Multi-Terminal Panel -- double-click this file to run.
rem
rem  First run: creates .venv and installs requirements.txt, reporting progress
rem  in this window. Every run after that goes straight to the panel.
rem
rem  The app is started with pythonw.exe, so nothing is left behind: this window
rem  closes the moment the panel is on its way up.
rem ===========================================================================

setlocal EnableExtensions
cd /d "%~dp0"

set "VENV_PY=.venv\Scripts\python.exe"
set "VENV_PYW=.venv\Scripts\pythonw.exe"
set "PYSIDE_DIR=.venv\Lib\site-packages\PySide6"

if exist "%VENV_PY%" goto :check_deps

echo Multi-Terminal Panel -- first-run setup.
echo.
call :find_python
if errorlevel 1 goto :err_no_python
echo Creating the virtual environment in .venv ...
%PY% -m venv .venv
if not exist "%VENV_PY%" goto :err_venv

:check_deps
rem A directory check, not an import: starting Python to ask would add most of a
rem second to every single launch. The case this catches is a first install that
rem was interrupted, which is the only way a venv here ends up without PySide6.
if exist "%PYSIDE_DIR%" goto :launch

echo Installing dependencies. This takes a minute the first time ...
echo.
"%VENV_PY%" -m pip install --disable-pip-version-check -r requirements.txt
if errorlevel 1 goto :err_deps
if not exist "%PYSIDE_DIR%" goto :err_deps
echo.
echo Setup done. Starting the panel ...

:launch
rem pythonw.exe leaves no console window behind. main.py knows it may be running
rem without one and reports a crash in a dialog box instead of into the void.
set "RUNNER=%VENV_PYW%"
if not exist "%RUNNER%" set "RUNNER=%VENV_PY%"
start "" "%RUNNER%" "%~dp0main.py"
exit /b 0


rem --- finding an interpreter to build the venv with --------------------------

:find_python
rem The py launcher first: it knows about installs that were never added to PATH.
set "PY="
for %%I in ("py -3" "python" "python3") do if not defined PY call :try_python %%~I
if defined PY exit /b 0
exit /b 1

:try_python
rem Doubles as the version gate -- an interpreter that is too old is as useless
rem here as one that is not installed, and this way both give the same message.
%* -c "import sys; sys.exit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if errorlevel 1 exit /b 1
set "PY=%*"
exit /b 0


rem --- things that can go wrong ----------------------------------------------

:err_no_python
echo.
echo   Python 3.10 or newer was not found.
echo.
echo   Install it from https://www.python.org/downloads/ -- tick
echo   "Add python.exe to PATH" in the installer -- then run this file again.
goto :fail

:err_venv
echo.
echo   Could not create the virtual environment in:
echo     %CD%\.venv
echo.
echo   Check that this folder is writable, then run this file again.
goto :fail

:err_deps
echo.
echo   Installing the dependencies failed. The messages above say why; the
echo   usual cause is no network access to PyPI.
echo.
echo   Delete the .venv folder and run this file again to start over.
goto :fail

:fail
echo.
pause
exit /b 1
