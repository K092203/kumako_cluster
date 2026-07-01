@echo off
setlocal
pushd "%~dp0"
python scripts\worker.py --worker-id worker01 --once %*
popd
