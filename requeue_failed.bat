@echo off
setlocal
pushd "%~dp0"
python scripts\requeue_failed.py %*
popd
