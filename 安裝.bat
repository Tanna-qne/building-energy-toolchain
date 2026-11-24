@echo off
rem One-click setup: needs DesignBuilder and uv (https://docs.astral.sh/uv/).
cd /d "%~dp0"
where uv >nul 2>nul
if errorlevel 1 (
  echo uv not found. Install it first: https://docs.astral.sh/uv/getting-started/installation/
  pause
  exit /b 1
)
uv run --no-project --python 3.12 python tools\setup\install.py %*
pause
