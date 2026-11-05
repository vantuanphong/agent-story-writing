@echo off
REM Mo mot cua so terminal da kich hoat san venv_autogen.
REM Nhay dup file nay la dung duoc ngay, khong can doi ExecutionPolicy.
chcp 65001 >nul
set "ROOT=%~dp0"
if not exist "%ROOT%venv_autogen\Scripts\activate.bat" (
    echo Khong thay venv_autogen\Scripts\activate.bat
    exit /b 1
)
cd /d "%ROOT%"
call "%ROOT%venv_autogen\Scripts\activate.bat"
echo.
echo === Da kich hoat venv_autogen. Thu cac lenh: ===
echo    python -c "import sys; print(sys.prefix)"
echo    viet_truyen.bat --chapters 12 --words 900
echo    deactivate   ^(thoat venv^)
echo.
cmd /k
