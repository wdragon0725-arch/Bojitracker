@echo off
setlocal
chcp 65001 >nul
title Boji Health 启动器
cd /d "%~dp0"

set "BOJI_URL=http://127.0.0.1:4173/boji-health.html"
set "BOJI_HEALTH_URL=http://127.0.0.1:4173/api/health"

where python >nul 2>nul
if errorlevel 1 (
  echo [错误] 未找到 Python。请先安装 Python 3，并勾选“Add Python to PATH”。
  pause
  exit /b 1
)

call :check_server
if not errorlevel 1 goto open_frontend

echo [1/2] 正在启动后端、SQLite 数据库和前端文件服务...
start "Boji Health 后端" /min cmd /k "python server.py"

for /l %%I in (1,1,20) do (
  timeout /t 1 /nobreak >nul
  call :check_server
  if not errorlevel 1 goto open_frontend
)

echo [错误] 服务未能在 20 秒内启动。
echo 请查看任务栏中“Boji Health 后端”窗口显示的错误信息。
echo 如果提示 4173 端口被占用，请先关闭原来的预览服务后重试。
pause
exit /b 1

:open_frontend
echo [2/2] 服务已就绪，正在打开前端页面...
start "" "%BOJI_URL%"
exit /b 0

:check_server
powershell -NoProfile -Command "try { $r = Invoke-RestMethod -Uri '%BOJI_HEALTH_URL%' -TimeoutSec 1; if ($r.service -eq 'boji-health') { exit 0 } } catch {}; exit 1" >nul 2>nul
exit /b %errorlevel%
