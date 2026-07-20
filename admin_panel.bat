@echo off
chcp 65001 >nul
setlocal
pushd "%~dp0"
start http://127.0.0.1:8765/
python scripts\admin_panel.py %*
popd
