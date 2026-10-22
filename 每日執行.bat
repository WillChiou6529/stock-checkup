@echo off
chcp 65001 >nul
cd /d "%~dp0"
python analyze.py %*
if "%1"=="" pause
