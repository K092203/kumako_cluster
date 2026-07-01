@echo off
setlocal
set "SLOTS=%~1"
if "%SLOTS%"=="" set "SLOTS=14"
pushd "%~dp0"
python scripts\supervise_slots.py --slots %SLOTS% --max-workers 21
popd
