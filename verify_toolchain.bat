@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
if not exist "tools\w64devkit\bin\g++.exe" (
  echo tools\w64devkit\bin\g++.exe not found. See tools\README.md.
  popd
  exit /b 1
)
if not exist repo_snapshot mkdir repo_snapshot
copy /y templates\hello.cpp repo_snapshot\ >nul
copy /y templates\cluster_setup.hello.json repo_snapshot\cluster_setup.json >nul
python scripts\make_job.py --prefix hello --count 1 --timeout-sec 60 -- hello.exe
echo.
echo Job queued. Start a worker (start_worker_auto.bat) and check:
echo   results\^<worker^>\hello001\stdout.txt  -^> "hello from kumako cluster"
popd
