@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title Monitor de Atos - Menu
cd /d "%~dp0"

REM Tesseract / Poppler no PATH (Windows)
set "PATH=C:\Program Files\Tesseract-OCR;C:\Poppler\poppler-24.02.0\Library\bin;C:\poppler\Library\bin;%PATH%"
set PYTHONUNBUFFERED=1
set DEV_RELOAD=0

python scripts\_menu_cli.py
set ERR=%ERRORLEVEL%
if %ERR% NEQ 0 (
  echo.
  echo  Falha ao abrir o menu. Verifique se o Python esta no PATH.
  echo  Codigo: %ERR%
  pause
)
endlocal
exit /b %ERR%