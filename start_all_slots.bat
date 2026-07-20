@echo off
chcp 65001 >nul
setlocal
set "SLOTS=%~1"
if "%SLOTS%"=="" set "SLOTS=14"
rem SLOTS must be validated before it reaches the PowerShell command below.
rem %SLOTS% is quoted here (not delayed-expansion !SLOTS!) so the value stays
rem inert to cmd.exe's own metacharacter parsing regardless of its content;
rem this also avoids a real-machine bug where a Japanese comment elsewhere in
rem this file, combined with delayed expansion, gets misread under the
rem Shift-JIS (932) codepage and corrupts parsing of the lines below it.
echo "%SLOTS%"| findstr /r "^\"[1-9][0-9]*\"$" >nul
if errorlevel 1 (
  echo error: SLOTS must be a positive integer, got "%SLOTS%"
  exit /b 1
)
rem second arg "adapt" makes each launcher start its supervisor with --adapt
set "ADAPT=$false"
if /I "%~2"=="adapt" set "ADAPT=$true"
pushd "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$obj = @{ slots = %SLOTS%; adapt = %ADAPT%; requested_at = (Get-Date).ToString('o') }; $obj | ConvertTo-Json | Set-Content -Encoding UTF8 control\start_slots_all.json"
echo Requested all launcher agents to start %SLOTS% slots (adapt=%ADAPT%).
popd
