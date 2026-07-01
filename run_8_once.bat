@echo off
setlocal
pushd "%~dp0"
for %%W in (worker01 worker02 worker03 worker04 worker05 worker06 worker07 worker08) do (
  python scripts\worker.py --worker-id %%W --local-dir "C:\supercon-worker\%%W" --once
)
popd
