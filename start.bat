@echo off
chcp 65001 >nul
title AI-IMS 一键启动

cd /d "%~dp0"

echo ========================================
echo   AI-IMS 工业多智能体系统 - 一键启动
echo   后端: http://127.0.0.1:8000
echo   前端: http://localhost:5173
echo ========================================
echo.

REM ---- 检查虚拟环境 ----
if not exist ".venv\Scripts\python.exe" (
    echo [错误] 未找到 .venv 虚拟环境，请先在项目根目录执行: python -m venv .venv
    pause
    exit /b 1
)

REM ---- 启动后端（新窗口） ----
echo [1/2] 启动后端服务...
start "AI-IMS 后端 (8000)" cmd /k ""%~dp0start-backend.bat""

REM ---- 等待后端就绪 ----
echo       等待后端启动...
timeout /t 4 /nobreak >nul

REM ---- 启动前端（新窗口） ----
echo [2/2] 启动前端服务...
start "AI-IMS 前端 (5173)" cmd /k ""%~dp0start-frontend.bat""

REM ---- 等待前端就绪并打开浏览器 ----
echo       等待前端启动...
timeout /t 5 /nobreak >nul

echo.
echo 启动完成！正在打开浏览器 http://localhost:5173
start "" "http://localhost:5173"

echo.
echo 提示：关闭此窗口不会停止服务，请在各自窗口中按 Ctrl+C 或关闭窗口来停止。
timeout /t 3 /nobreak >nul
exit