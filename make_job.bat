@echo off
setlocal
pushd "%~dp0"
python scripts\make_job.py %*
popd
