@echo off
cd /d "%~dp0"
if not exist ".venv\Scripts\python.exe" (
    echo 未找到虚拟环境，请先双击 setup.bat 配置环境
    pause
    exit /b 1
)
".venv\Scripts\python.exe" app.py
pause
