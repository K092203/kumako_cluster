@echo off
setlocal
set "COUNT=%~1"
if "%COUNT%"=="" set "COUNT=10000"
set "SECONDS=%~2"
if "%SECONDS%"=="" set "SECONDS=60"
set /a TIMEOUT=%SECONDS%+30
pushd "%~dp0"
python scripts\make_job.py --count %COUNT% --timeout-sec %TIMEOUT% -- python stress_solver.py --seconds %SECONDS% --workers 1 --seed __seed__
popd
