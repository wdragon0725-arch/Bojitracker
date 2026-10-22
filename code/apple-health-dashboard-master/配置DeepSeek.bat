@echo off
setlocal
chcp 65001 >nul
cd /d "%~dp0"
python configure_deepseek.py
pause
