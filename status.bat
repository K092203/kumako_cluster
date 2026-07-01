@echo off
setlocal
pushd "%~dp0"
python scripts\status.py %*
popd
