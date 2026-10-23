@echo off
setlocal
where.exe py.exe >nul 2>nul
if errorlevel 1 goto try_python
py -3 -I -B -X utf8 -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto try_python
py -3 -I -B -X utf8 "%~dp0scripts\run.py" %*
exit /b %errorlevel%
:try_python
where.exe python.exe >nul 2>nul
if errorlevel 1 goto try_python3
python -I -B -X utf8 -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto try_python3
python -I -B -X utf8 "%~dp0scripts\run.py" %*
exit /b %errorlevel%
:try_python3
where.exe python3.exe >nul 2>nul
if errorlevel 1 goto unavailable
python3 -I -B -X utf8 -c "import sys; raise SystemExit(0 if sys.version_info[:2] >= (3,10) else 1)" >nul 2>nul
if errorlevel 1 goto unavailable
python3 -I -B -X utf8 "%~dp0scripts\run.py" %*
exit /b %errorlevel%
:unavailable
echo {"error":{"code":"PYTHON_NOT_AVAILABLE","message":"Python 3.10+ is required. Install Python or use its full executable path to scripts/run.py."}} 1>&2
exit /b 3
