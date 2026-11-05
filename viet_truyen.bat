@echo off
REM Viet truyen dai nhieu chuong. Vi du:
REM   viet_truyen.bat --plan-only --chapters 12
REM   viet_truyen.bat --chapters 12 --words 900
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
"%PY%" "%ROOT%script_viet_truyen.py" %*
exit /b %ERRORLEVEL%
