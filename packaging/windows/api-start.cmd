@echo off
setlocal
cd /d "%~dp0"
python\python.exe bootstrap.py
if errorlevel 1 exit /b %errorlevel%
python\python.exe -m api
