@echo off
REM Chay phong hop bien kich 4 agent (script_phong_hop.py)
setlocal
chcp 65001 >nul
set "ROOT=%~dp0"
set "PY=%ROOT%venv_autogen\Scripts\python.exe"

if not exist "%PY%" (
    echo Khong thay: %PY%
    echo Tao venv truoc:  python -m venv venv_autogen
    exit /b 1
)

set PYTHONIOENCODING=utf-8
"%PY%" "%ROOT%script_phong_hop.py" %*
exit /b %ERRORLEVEL%

