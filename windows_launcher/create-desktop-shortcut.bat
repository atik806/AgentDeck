@echo off
rem ===========================================================================
rem  Puts an "AgentDeck" shortcut on the desktop, pointing at run.bat
rem  in this folder. Double-click this file once; after that the desktop icon is
rem  all you need.
rem
rem  The shortcut is minimised (WindowStyle 7) so run.bat's console does its work
rem  out of the way instead of flashing over whatever you were looking at.
rem ===========================================================================

setlocal EnableExtensions
cd /d "%~dp0"

if not exist "%~dp0run.bat" goto :err_missing

rem The app mark ships beside this script; fall back to pythonw, then cmd, if it
rem is somehow missing.
set "ICON=%~dp0assets\icon.ico"
if not exist "%ICON%" set "ICON=%~dp0.venv\Scripts\pythonw.exe"
if not exist "%ICON%" set "ICON=%SystemRoot%\System32\cmd.exe"

powershell -NoProfile -ExecutionPolicy Bypass -Command ^
  "$link = Join-Path ([Environment]::GetFolderPath('Desktop')) 'AgentDeck.lnk';" ^
  "$s = (New-Object -ComObject WScript.Shell).CreateShortcut($link);" ^
  "$s.TargetPath = '%~dp0run.bat';" ^
  "$s.WorkingDirectory = '%~dp0';" ^
  "$s.IconLocation = '%ICON%,0';" ^
  "$s.Description = 'Every terminal, every agent, one deck';" ^
  "$s.WindowStyle = 7;" ^
  "$s.Save();" ^
  "Write-Host ('Created ' + $link)"

if errorlevel 1 goto :err_failed
echo.
echo Done. The icon is on your desktop.
echo.
pause
exit /b 0

:err_missing
echo.
echo   run.bat is not next to this file. Keep the two together -- the shortcut
echo   points at run.bat in whatever folder this script lives in.
goto :fail

:err_failed
echo.
echo   Could not create the shortcut. The messages above say why.
goto :fail

:fail
echo.
pause
exit /b 1
