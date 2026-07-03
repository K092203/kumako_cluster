@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
python scripts\summarize_results.py --update-incumbent %*
popd
