@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
python scripts\status.py %*
popd
