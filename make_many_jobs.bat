@echo off
setlocal
set "COUNT=%~1"
if "%COUNT%"=="" set "COUNT=10000"
pushd "%~dp0"
python scripts\make_job.py --count %COUNT%
popd
