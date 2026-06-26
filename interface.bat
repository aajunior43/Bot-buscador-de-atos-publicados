@echo off
title Monitor de Atos - Interface Web
cd /d "%~dp0"
set PATH=C:\Program Files\Tesseract-OCR;C:\Poppler\poppler-24.02.0\Library\bin;%PATH%
python run_interface.py
pause
