@echo off
setlocal EnableExtensions
chcp 65001 >nul 2>&1
title Monitor de Atos - Interface Web
cd /d "%~dp0"

REM Tesseract / Poppler no PATH (Windows)
set "PATH=C:\Program Files\Tesseract-OCR;C:\Poppler\poppler-24.02.0\Library\bin;C:\poppler\Library\bin;%PATH%"
set PYTHONUNBUFFERED=1
set DEV_RELOAD=0

echo.
echo  ============================================================
echo    MONITOR DE ATOS - Interface Web
echo  ============================================================
echo.
echo  Iniciando servico...
echo  O navegador sera aberto automaticamente.
echo  Pressione Ctrl+C para encerrar.
echo.

python run_interface.py
set ERR=%ERRORLEVEL%
if %ERR% NEQ 0 (
  echo.
  echo  Falha ao iniciar. Verifique se o Python esta no PATH.
  echo  Codigo: %ERR%
  pause
)
endlocal
exit /b %ERR%
