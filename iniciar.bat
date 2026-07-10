@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
title Monitor de Atos - Menu
cd /d "%~dp0"

REM Tesseract / Poppler no PATH (Windows)
set "PATH=C:\Program Files\Tesseract-OCR;C:\Poppler\poppler-24.02.0\Library\bin;C:\poppler\Library\bin;%PATH%"
set PYTHONUNBUFFERED=1
set DEV_RELOAD=0

:MENU
cls
echo.
echo  ============================================================
echo           MONITOR DE ATOS - Inaja / O Regional
echo  ============================================================
echo.
echo    [1] Iniciar TUDO  (Web + BOT no mesmo terminal)
echo        Web: http://localhost:8001
echo.
echo    [2] So a interface WEB
echo    [3] So o BOT  (ciclo continuo / agendado)
echo    [4] Um ciclo do BOT e encerra  (--once)
echo.
echo    [5] Processar JULHO/2026  (cache OCR + IA, validacao)
echo    [6] Status JULHO/2026
echo    [7] Listar mencoes de julho
echo.
echo    [8] Reprocessar subdetectados  (lote com IA)
echo    [9] Status rapido da fila  (pendentes / pubs)
echo.
echo    [A] Teste de notificacao  (Telegram / arquivo)
echo    [B] Rodar testes pytest
echo    [C] Limpar dados processados  (ATENCAO: zera pubs/mencoes)
echo.
echo    [0] Sair
echo.
echo  ============================================================
set "OP="
set /p OP="  Escolha uma opcao: "
if not defined OP goto MENU

if /I "%OP%"=="0" goto SAIR
if /I "%OP%"=="1" goto INICIAR_TUDO
if /I "%OP%"=="2" goto SO_WEB
if /I "%OP%"=="3" goto SO_BOT
if /I "%OP%"=="4" goto BOT_ONCE
if /I "%OP%"=="5" goto PROC_JULHO
if /I "%OP%"=="6" goto STATUS_JULHO
if /I "%OP%"=="7" goto MENCOES_JULHO
if /I "%OP%"=="8" goto REPROC_SUB
if /I "%OP%"=="9" goto STATUS_FILA
if /I "%OP%"=="A" goto NOTIFY_TEST
if /I "%OP%"=="B" goto PYTEST
if /I "%OP%"=="C" goto LIMPAR
if /I "%OP%"=="a" goto NOTIFY_TEST
if /I "%OP%"=="b" goto PYTEST
if /I "%OP%"=="c" goto LIMPAR

echo.
echo  Opcao invalida.
timeout /t 2 >nul
goto MENU

:INICIAR_TUDO
cls
echo.
echo  Iniciando Web + BOT...
echo  Ctrl+C encerra tudo.
echo.
python iniciar_tudo.py
set ERR=!ERRORLEVEL!
if !ERR! NEQ 0 (
  echo.
  echo  Falha ^(codigo !ERR!^). Verifique o Python no PATH.
  pause
)
goto MENU

:SO_WEB
cls
echo.
echo  Interface WEB em http://localhost:8001
echo  Ctrl+C encerra.
echo.
python run_interface.py
if errorlevel 1 pause
goto MENU

:SO_BOT
cls
echo.
echo  BOT em modo continuo...
echo  Ctrl+C encerra.
echo.
python main.py
if errorlevel 1 pause
goto MENU

:BOT_ONCE
cls
echo.
echo  Executando um ciclo do BOT (--once)...
echo.
python main.py --once
echo.
pause
goto MENU

:PROC_JULHO
cls
echo.
echo  Processando edicoes de JULHO/2026 (cache + IA)...
echo.
python scripts\_processar_julho_2026.py
echo.
pause
goto MENU

:STATUS_JULHO
cls
echo.
python scripts\_status_julho.py
echo.
pause
goto MENU

:MENCOES_JULHO
cls
echo.
python scripts\_listar_mencoes_julho.py
echo.
pause
goto MENU

:REPROC_SUB
cls
echo.
echo  Reprocessar edicoes subdetectadas (IA).
echo  Padrao: desde 2026-01-01, limite 20.
echo.
set "LIMITE=20"
set /p LIMITE="  Limite de edicoes [%LIMITE%]: "
if not defined LIMITE set LIMITE=20
echo.
python scripts\reprocessar_subdetectados.py --desde 2026-01-01 --limit %LIMITE%
echo.
pause
goto MENU

:STATUS_FILA
cls
echo.
echo  Status da fila / banco...
echo.
python scripts\_status_fila.py
echo.
pause
goto MENU

:NOTIFY_TEST
cls
echo.
echo  Enviando notificacao de teste...
echo.
python main.py --notify-test
echo.
pause
goto MENU

:PYTEST
cls
echo.
echo  Rodando pytest...
echo.
python -m pytest tests/ -q --tb=line
echo.
pause
goto MENU

:LIMPAR
cls
echo.
echo  ATENCAO: isso apaga publicacoes, mencoes, jobs e zera flags OCR.
echo  Mantem: edicoes, PDFs e caches .ocr.json
echo.
set /p CONF="  Digite SIM para confirmar: "
if /I not "%CONF%"=="SIM" (
  echo  Cancelado.
  timeout /t 2 >nul
  goto MENU
)
echo.
python scripts\_limpar_processados.py
echo.
pause
goto MENU

:SAIR
echo.
echo  Ate logo.
endlocal
exit /b 0
