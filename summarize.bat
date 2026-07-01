@echo off
setlocal
pushd "%~dp0"
python scripts\summarize_results.py --update-incumbent %*
popd
