@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
python scripts\launcher_agent.py --max-workers 21
popd
