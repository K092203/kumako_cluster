@echo off
chcp 65001 >nul
setlocal
if "%~1"=="" (
  echo Usage: worker_loop_id.bat worker01
  exit /b 2
)
set "WORKER_ID=%~1"
shift
pushd "%~dp0"
python scripts\worker.py --worker-id "%WORKER_ID%" --local-dir "C:\supercon-worker\%WORKER_ID%" %*
popd
