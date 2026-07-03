@echo off
chcp 65001 >nul
setlocal
set "SLOTS=%~1"
if "%SLOTS%"=="" set "SLOTS=14"
pushd "%~dp0"
powershell -NoProfile -ExecutionPolicy Bypass -Command "$obj = @{ slots = %SLOTS%; requested_at = (Get-Date).ToString('o') }; $obj | ConvertTo-Json | Set-Content -Encoding UTF8 control\start_slots_all.json"
echo Requested all launcher agents to start %SLOTS% slots.
popd
