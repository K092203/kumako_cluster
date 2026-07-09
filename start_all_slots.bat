@echo off
chcp 65001 >nul
setlocal
set "SLOTS=%~1"
if "%SLOTS%"=="" set "SLOTS=14"
rem 第2引数に adapt を渡すと、各launcherが supervisor を --adapt 付きで起動する
set "ADAPT=$false"
if /I "%~2"=="adapt" set "ADAPT=$true"
pushd "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$obj = @{ slots = %SLOTS%; adapt = %ADAPT%; requested_at = (Get-Date).ToString('o') }; $obj | ConvertTo-Json | Set-Content -Encoding UTF8 control\start_slots_all.json"
echo Requested all launcher agents to start %SLOTS% slots (adapt=%ADAPT%).
popd
