@echo off
setlocal
pushd "%~dp0"
python scripts\launcher_agent.py --max-workers 21
popd
