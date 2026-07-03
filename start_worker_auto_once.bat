@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
for /f "usebackq delims=" %%W in (`python scripts\register_worker.py --local-base "C:\supercon-worker" --max-workers 21 --print-id`) do set "WORKER_ID=%%W"
if "%WORKER_ID%"=="" (
  echo Failed to assign worker id.
  popd
  exit /b 1
)
echo Starting %WORKER_ID% once
python scripts\worker.py --worker-id "%WORKER_ID%" --local-dir "C:\supercon-worker\%WORKER_ID%" --once %*
popd
