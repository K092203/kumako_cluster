@echo off
chcp 65001 >nul
setlocal EnableDelayedExpansion
set "SLOTS=%~1"
if "%SLOTS%"=="" set "SLOTS=8"
pushd "%~dp0"
for /f "usebackq delims=" %%W in (`python scripts\register_worker.py --local-base "C:\supercon-worker" --max-workers 21 --print-id`) do set "BASE_WORKER_ID=%%W"
if "%BASE_WORKER_ID%"=="" (
  echo Failed to assign worker id.
  popd
  exit /b 1
)
echo Starting %SLOTS% slots for %BASE_WORKER_ID%
for /l %%S in (1,1,%SLOTS%) do (
  set "NUM=0%%S"
  set "SLOT_ID=%BASE_WORKER_ID%-s!NUM:~-2!"
  start "%BASE_WORKER_ID% slot %%S" cmd /c python scripts\worker.py --worker-id "!SLOT_ID!" --local-dir "C:\supercon-worker\!SLOT_ID!"
)
popd
