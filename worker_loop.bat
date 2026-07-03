@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
python scripts\worker.py --worker-id worker01 %*
popd
