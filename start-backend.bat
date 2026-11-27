@echo off
chcp 65001 >nul
title AI-IMS 后端服务

cd /d "%~dp0"

if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到虚拟环境 .venv，请先创建：python -m venv .venv
    pause
    exit /b 1
)

echo ========================================
echo  启动 AI-IMS 后端服务
echo  地址: http://127.0.0.1:8000
echo  文档: http://127.0.0.1:8000/docs
echo ========================================

set PYTHONPATH=src
set APP_ENVIRONMENT=shadow
set PORT=8000

echo [检查] 释放端口 %PORT% ...
set FOUND_PID=
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    echo       终止占用进程 PID %%a
    taskkill /F /PID %%a >nul 2>&1
    set FOUND_PID=1
)
if defined FOUND_PID timeout /t 2 /nobreak >nul

echo.
echo [启动] uvicorn 监听 127.0.0.1:%PORT% ...
".venv\Scripts\python.exe" -m uvicorn industrial_agent.api.app:app --host 127.0.0.1 --port %PORT%

pause