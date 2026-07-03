@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
python scripts\register_worker.py --local-base "C:\supercon-worker" --max-workers 21 %*
popd
