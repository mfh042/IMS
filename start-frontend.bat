@echo off
chcp 65001 >nul
title AI-IMS 前端服务

cd /d "%~dp0frontend"

if not exist "node_modules" (
    echo [提示] 未检测到 node_modules，正在安装依赖...
    call npm install --no-audit --no-fund
    if errorlevel 1 (
        echo [错误] 依赖安装失败
        pause
        exit /b 1
    )
)

echo ========================================
echo  启动 AI-IMS 前端服务 (Vite)
echo  地址: http://localhost:5173
echo  代理: /api -^> http://127.0.0.1:8000
echo ========================================

set PORT=5173

echo [检查] 释放端口 %PORT% ...
set FOUND_PID=
for /f "tokens=5" %%a in ('netstat -ano ^| findstr ":%PORT% " ^| findstr "LISTENING"') do (
    echo       终止占用进程 PID %%a
    taskkill /F /PID %%a >nul 2>&1
    set FOUND_PID=1
)
if defined FOUND_PID timeout /t 2 /nobreak >nul

echo.
echo [启动] Vite 监听 localhost:%PORT% ...
call npm run dev

pause