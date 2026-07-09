@echo off
title Monitor de Atos
cd /d "%~dp0"

REM Tesseract / Poppler no PATH (Windows)
set PATH=C:\Program Files\Tesseract-OCR;C:\Poppler\poppler-24.02.0\Library\bin;C:\poppler\Library\bin;%PATH%
set PYTHONUNBUFFERED=1
set DEV_RELOAD=0

echo ============================================================
echo   Monitor de Atos - um unico terminal
echo   Web: http://localhost:8001
echo   Servicos: interface web + rastreador
echo   Ctrl+C encerra tudo
echo ============================================================
echo.

python iniciar_tudo.py
set ERR=%ERRORLEVEL%
if %ERR% NEQ 0 (
  echo.
  echo Falha ao iniciar ^(codigo %ERR%^). Verifique se o Python esta no PATH.
  pause
)

