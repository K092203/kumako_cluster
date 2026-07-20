@echo off
chcp 65001 >nul
setlocal
set "SLOTS=%~1"
if "%SLOTS%"=="" set "SLOTS=14"
rem second arg "adapt" enables goodput-feedback auto-scaling of slot count
set "ADAPT="
if /I "%~2"=="adapt" set "ADAPT=--adapt"
pushd "%~dp0"
python scripts\supervise_slots.py --slots %SLOTS% --max-workers 21 %ADAPT%
popd
