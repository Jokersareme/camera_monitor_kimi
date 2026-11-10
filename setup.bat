@echo off
setlocal EnableDelayedExpansion
cd /d "%~dp0"

echo ==========================================
echo   camera_monitor 环境一键配置
echo ==========================================
echo.

REM 1. 查找可用的 Python（优先 py -3.12，其次 python）
set "PYCMD="
py -3.12 -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
if !errorlevel! equ 0 set "PYCMD=py -3.12"
if not defined PYCMD (
    python -c "import sys; raise SystemExit(0 if sys.version_info >= (3, 10) else 1)" >nul 2>nul
    if !errorlevel! equ 0 set "PYCMD=python"
)
if not defined PYCMD (
    echo [错误] 未找到 Python 3.10+，请先到 https://www.python.org/downloads/ 安装
    echo        安装时务必勾选 "Add python.exe to PATH"
    pause
    exit /b 1
)
echo [1/4] 使用 Python: %PYCMD%
%PYCMD% --version

REM 2. 删除旧虚拟环境
if exist ".venv" (
    echo [2/4] 删除旧的 .venv ...
    rmdir /s /q .venv
    if exist ".venv" (
        echo [错误] 删除失败，请手动删除 .venv 文件夹后重试
        pause
        exit /b 1
    )
) else (
    echo [2/4] 没有旧的 .venv，跳过删除
)

REM 3. 重建虚拟环境
echo [3/4] 创建新的虚拟环境 ...
%PYCMD% -m venv .venv
if not exist ".venv\Scripts\python.exe" (
    echo [错误] 虚拟环境创建失败
    pause
    exit /b 1
)

REM 4. 安装依赖（优先清华镜像，失败则回退官方源）
echo [4/4] 安装依赖 ...
".venv\Scripts\python.exe" -m pip install -i https://pypi.tuna.tsinghua.edu.cn/simple -r requirements.txt
if errorlevel 1 (
    echo 清华镜像安装失败，改用官方源重试 ...
    ".venv\Scripts\python.exe" -m pip install -r requirements.txt
)
if errorlevel 1 (
    echo [错误] 依赖安装失败，请检查网络后重试
    pause
    exit /b 1
)

echo.
echo ==========================================
echo   配置完成！启动方式：
echo     双击 run.bat
echo   或执行： .venv\Scripts\python.exe app.py
echo ==========================================
pause
