@echo off
chcp 65001 >nul
setlocal
set "COUNT=%~1"
if "%COUNT%"=="" set "COUNT=10000"
set "SECONDS=%~2"
if "%SECONDS%"=="" set "SECONDS=60"
set /a TIMEOUT=%SECONDS%+30
pushd "%~dp0"
if not exist repo_snapshot mkdir repo_snapshot
copy /y templates\stress_solver.py repo_snapshot\ >nul
python scripts\make_job.py --count %COUNT% --timeout-sec %TIMEOUT% -- python stress_solver.py --seconds %SECONDS% --workers 1 --seed __seed__
popd
