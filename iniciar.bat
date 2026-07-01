@echo off
title Monitor de Atos
cd /d "%~dp0"
set PATH=C:\Program Files\Tesseract-OCR;C:\Poppler\poppler-24.02.0\Library\bin;%PATH%

echo ============================================================
echo   Monitor de Atos - Iniciando todos os servicos...
echo ============================================================
echo.

REM --- Interface Web ---
start "Monitor - Interface Web" cmd /k "cd /d "%~dp0" && set PATH=C:\Program Files\Tesseract-OCR;C:\Poppler\poppler-24.02.0\Library\bin;%%PATH%% && title Monitor - Interface Web && python run_interface.py"

REM --- Bot monitor (ciclo continuo) ---
start "Monitor - Bot Rastreador" cmd /k "cd /d "%~dp0" && set PATH=C:\Program Files\Tesseract-OCR;C:\Poppler\poppler-24.02.0\Library\bin;%%PATH%% && title Monitor - Bot Rastreador && python main.py"

REM --- Bot Telegram (polling interativo) ---
start "Monitor - Bot Telegram" cmd /k "cd /d "%~dp0" && set PATH=C:\Program Files\Tesseract-OCR;C:\Poppler\poppler-24.02.0\Library\bin;%%PATH%% && title Monitor - Bot Telegram && python telegram_bot.py"

echo.
echo  3 janelas abertas:
echo   [1] Interface Web    -> http://localhost:8001
echo   [2] Bot Rastreador   -> ciclo de monitoramento
echo   [3] Bot Telegram     -> polling interativo
echo.
echo  Feche esta janela quando quiser. Os servicos continuam rodando.
echo.
pause
