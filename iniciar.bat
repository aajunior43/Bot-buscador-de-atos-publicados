@echo off
title Monitor de Atos - Bot
cd /d "%~dp0"
set PATH=C:\Program Files\Tesseract-OCR;C:\Poppler\poppler-24.02.0\Library\bin;%PATH%
python main.py --once
pause
