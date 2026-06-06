@echo off
chcp 65001 >nul
echo =============================================
echo   ZZP自动输入2.0
echo   按 F9 输出  |  按 ESC 中断
echo =============================================
echo.
python "%~dp0auto_type.py"
if %ERRORLEVEL% NEQ 0 (
    echo.
    echo 出错！请确保已安装依赖:
    echo pip install pynput pillow
    pause
)
