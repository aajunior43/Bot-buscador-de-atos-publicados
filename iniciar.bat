@echo off
setlocal EnableExtensions EnableDelayedExpansion
chcp 65001 >nul 2>&1
title Monitor de Atos - Menu
cd /d "%~dp0"

REM Tesseract / Poppler no PATH (Windows)
set "PATH=C:\Program Files\Tesseract-OCR;C:\Poppler\poppler-24.02.0\Library\bin;C:\poppler\Library\bin;%PATH%"
set PYTHONUNBUFFERED=1
set DEV_RELOAD=0

REM Cores ANSI (Windows 10+)
for /F %%a in ('echo prompt $E ^| cmd') do set "ESC=%%a"
set "C0=%ESC%[0m"
set "C1=%ESC%[96m"
set "C2=%ESC%[92m"
set "C3=%ESC%[93m"
set "C4=%ESC%[91m"
set "C5=%ESC%[95m"
set "CD=%ESC%[90m"
set "CB=%ESC%[1m"

:MENU
cls
echo.
echo  %C1%%CB%============================================================%C0%
echo  %C1%%CB%        MONITOR DE ATOS - Inaja / O Regional%C0%
echo  %C1%%CB%============================================================%C0%
echo.
call :HEADER_STATUS
echo.
echo  %C5%%CB%  SERVICOS%C0%
echo  %C2%  [1]%C0% Iniciar TUDO            %CD%Web + BOT  http://localhost:8001%C0%
echo  %C2%  [2]%C0% So interface WEB
echo  %C2%  [3]%C0% So BOT continuo
echo  %C2%  [4]%C0% Um ciclo BOT e encerra  %CD%--once%C0%
echo  %C2%  [5]%C0% Abrir navegador         %CD%http://localhost:8001%C0%
echo.
echo  %C5%%CB%  PROCESSAMENTO%C0%
echo  %C2%  [6]%C0% Processar um MES         %CD%AAAA-MM, cache+IA%C0%
echo  %C2%  [7]%C0% Processar JULHO/2026     %CD%atalho%C0%
echo  %C2%  [8]%C0% Processar N pendentes    %CD%OCR fila%C0%
echo  %C2%  [9]%C0% Reprocessar subdetectados
echo  %C2%  [F]%C0% Ciclo com force-rescan   %CD%cuidado%C0%
echo  %C2%  [O]%C0% Um ciclo com force-OCR   %CD%Tesseract todas as paginas%C0%
echo.
echo  %C5%%CB%  CONSULTA%C0%
echo  %C2%  [S]%C0% Status da fila           %CD%completo%C0%
echo  %C2%  [U]%C0% Ultimas publicacoes
echo  %C2%  [P]%C0% Buscar publicacao        %CD%termo%C0%
echo  %C2%  [M]%C0% Mencoes de um mes
echo  %C2%  [Y]%C0% Resumo mensal            %CD%edicoes/pubs%C0%
echo  %C2%  [J]%C0% Status julho/2026
echo  %C2%  [I]%C0% Status da IA / chaves
echo.
echo  %C5%%CB%  FERRAMENTAS%C0%
echo  %C2%  [A]%C0% Teste de notificacao
echo  %C2%  [B]%C0% Backup do banco
echo  %C2%  [R]%C0% Reconstruir pasta atos/
echo  %C2%  [E]%C0% Exportar CSV do mes
echo  %C2%  [T]%C0% Rodar pytest
echo  %C2%  [L]%C0% Remover lock travado
echo  %C2%  [Q]%C0% Limpar jobs travados
echo  %C2%  [G]%C0% Ver final do log
echo  %C2%  [D]%C0% Espaco em disco
echo  %C2%  [K]%C0% Bot Telegram interativo
echo  %C2%  [W]%C0% Abrir pasta do projeto
echo  %C2%  [H]%C0% Ajuda rapida
echo.
echo  %C4%  [C]%C0% Limpar dados processados  %CD%pede SIM%C0%
echo  %C2%  [0]%C0% Sair
echo.
echo  %C1%============================================================%C0%
set "OP="
set /p OP="  %CB%Escolha:%C0% "
if not defined OP goto MENU

if /I "%OP%"=="0" goto SAIR
if /I "%OP%"=="1" goto INICIAR_TUDO
if /I "%OP%"=="2" goto SO_WEB
if /I "%OP%"=="3" goto SO_BOT
if /I "%OP%"=="4" goto BOT_ONCE
if /I "%OP%"=="5" goto ABRIR_WEB
if /I "%OP%"=="6" goto PROC_MES
if /I "%OP%"=="7" goto PROC_JULHO
if /I "%OP%"=="8" goto PROC_PEND
if /I "%OP%"=="9" goto REPROC_SUB
if /I "%OP%"=="F" goto FORCE_RESCAN
if /I "%OP%"=="O" goto FORCE_OCR
if /I "%OP%"=="S" goto STATUS_FILA
if /I "%OP%"=="U" goto ULTIMAS_PUBS
if /I "%OP%"=="P" goto BUSCAR_PUBS
if /I "%OP%"=="M" goto MENCOES_MES
if /I "%OP%"=="Y" goto RESUMO_MES
if /I "%OP%"=="J" goto STATUS_JULHO
if /I "%OP%"=="I" goto IA_STATUS
if /I "%OP%"=="A" goto NOTIFY_TEST
if /I "%OP%"=="B" goto BACKUP
if /I "%OP%"=="R" goto REBUILD_ATOS
if /I "%OP%"=="E" goto EXPORT_MES
if /I "%OP%"=="T" goto PYTEST
if /I "%OP%"=="L" goto REM_LOCK
if /I "%OP%"=="Q" goto LIMPAR_JOBS
if /I "%OP%"=="G" goto VER_LOG
if /I "%OP%"=="D" goto ESPACO
if /I "%OP%"=="K" goto TG_BOT
if /I "%OP%"=="W" goto ABRIR_PASTA
if /I "%OP%"=="H" goto AJUDA
if /I "%OP%"=="C" goto LIMPAR

echo.
echo  %C4%Opcao invalida. Digite H para ajuda.%C0%
timeout /t 1 >nul
goto MENU

:HEADER_STATUS
python scripts\_header_status.py 2>nul
if errorlevel 1 echo  %CD%(status indisponivel)%C0%
exit /b 0

:INICIAR_TUDO
cls
echo.
echo  %C2%Iniciando Web + BOT...%C0%
echo  %CD%Ctrl+C encerra tudo.%C0%
echo.
python iniciar_tudo.py
if errorlevel 1 (
  echo.
  echo  %C4%Falha. Verifique o Python no PATH e o .env%C0%
  pause
)
goto MENU

:SO_WEB
cls
echo.
echo  %C2%WEB%C0% em http://localhost:8001
echo  %CD%Ctrl+C encerra.%C0%
echo.
python run_interface.py
if errorlevel 1 pause
goto MENU

:SO_BOT
cls
echo.
echo  %C2%BOT%C0% continuo...
echo  %CD%Ctrl+C encerra.%C0%
echo.
python main.py
if errorlevel 1 pause
goto MENU

:BOT_ONCE
cls
echo.
echo  %C2%Um ciclo%C0% do BOT --once...
echo.
python main.py --once
echo.
pause
goto MENU

:ABRIR_WEB
start "" "http://localhost:8001"
echo.
echo  %C2%Navegador aberto.%C0% Se a web nao subiu, use a opcao [1] ou [2].
timeout /t 2 >nul
goto MENU

:PROC_MES
cls
echo.
echo  %C2%Processar um mes%C0% - cache OCR + IA
echo  Exemplo: 2026-07   2026-06   2025-12
echo.
set "MES="
set /p MES="  Mes AAAA-MM: "
if not defined MES (
  echo  Cancelado.
  timeout /t 1 >nul
  goto MENU
)
echo.
set "LIM="
set /p LIM="  Limite de edicoes [Enter=todas]: "
echo.
if defined LIM (
  python scripts\_processar_mes.py %MES% --limite %LIM%
) else (
  python scripts\_processar_mes.py %MES%
)
echo.
pause
goto MENU

:PROC_JULHO
cls
echo.
echo  %C2%Processando JULHO/2026...%C0%
echo.
python scripts\_processar_mes.py 2026-07
echo.
pause
goto MENU

:PROC_PEND
cls
echo.
echo  %C2%Processar pendentes%C0% - OCR + deteccao + IA
echo.
set "N=5"
set /p N="  Quantas edicoes [5]: "
if not defined N set N=5
echo.
python scripts\_processar_pendentes.py --limite %N%
echo.
pause
goto MENU

:REPROC_SUB
cls
echo.
echo  %C2%Reprocessar subdetectados%C0% - IA
echo.
set "DESDE=2026-01-01"
set "LIMITE=20"
set /p DESDE="  Desde [2026-01-01]: "
if not defined DESDE set DESDE=2026-01-01
set /p LIMITE="  Limite [20]: "
if not defined LIMITE set LIMITE=20
echo.
python scripts\reprocessar_subdetectados.py --desde %DESDE% --limit %LIMITE%
echo.
pause
goto MENU

:FORCE_RESCAN
cls
echo.
echo  %C3%Force-rescan: reprocessa conhecidas + um ciclo.%C0%
echo.
set /p CONF="  Digite SIM para continuar: "
if /I not "%CONF%"=="SIM" (
  echo  Cancelado.
  timeout /t 1 >nul
  goto MENU
)
echo.
python main.py --once --force-rescan
echo.
pause
goto MENU

:FORCE_OCR
cls
echo.
echo  %C3%Force-OCR: Tesseract em todas as paginas + um ciclo.%C0%
echo  %CD%Mais lento; use se o texto embutido estiver ruim.%C0%
echo.
set /p CONF="  Digite SIM para continuar: "
if /I not "%CONF%"=="SIM" (
  echo  Cancelado.
  timeout /t 1 >nul
  goto MENU
)
echo.
python main.py --once --force-ocr
echo.
pause
goto MENU

:STATUS_FILA
cls
python scripts\_status_fila.py
pause
goto MENU

:ULTIMAS_PUBS
cls
echo.
set "N=15"
set /p N="  Quantas publicacoes [15]: "
if not defined N set N=15
set "MESF="
set /p MESF="  Filtrar mes AAAA-MM [Enter=todos]: "
echo.
if defined MESF (
  python scripts\_ultimas_publicacoes.py -n %N% --mes %MESF%
) else (
  python scripts\_ultimas_publicacoes.py -n %N%
)
pause
goto MENU

:BUSCAR_PUBS
cls
echo.
echo  %C2%Buscar publicacoes%C0%
echo  Exemplos: aditivo  prefeitura  04/2026  contrato
echo.
set "TERMO="
set /p TERMO="  Termo: "
if not defined TERMO (
  echo  Cancelado.
  timeout /t 1 >nul
  goto MENU
)
echo.
python scripts\_buscar_publicacoes.py "%TERMO%"
pause
goto MENU

:MENCOES_MES
cls
echo.
echo  %C2%Mencoes de um mes%C0%
set "MES=2026-07"
set /p MES="  Mes [2026-07]: "
if not defined MES set MES=2026-07
echo.
python scripts\_listar_mencoes.py %MES%
pause
goto MENU

:RESUMO_MES
cls
python scripts\_resumo_mensal.py
pause
goto MENU

:STATUS_JULHO
cls
python scripts\_status_julho.py
pause
goto MENU

:IA_STATUS
cls
python scripts\_ia_status.py
pause
goto MENU

:NOTIFY_TEST
cls
echo.
echo  %C2%Teste de notificacao...%C0%
echo.
python main.py --notify-test
echo.
pause
goto MENU

:BACKUP
cls
echo.
echo  %C2%Backup do banco...%C0%
echo.
python scripts\backup_db.py
echo.
pause
goto MENU

:REBUILD_ATOS
cls
echo.
echo  %C2%Reconstruindo pasta atos/ a partir do banco...%C0%
echo.
python scripts\reconstruir_atos.py
echo.
pause
goto MENU

:EXPORT_MES
cls
echo.
echo  %C2%Exportar publicacoes CSV%C0%  %CD%pasta exportacoes/%C0%
set "MES=2026-07"
set /p MES="  Mes [2026-07]: "
if not defined MES set MES=2026-07
echo.
python scripts\_exportar_mes.py %MES% --json
echo.
pause
goto MENU

:PYTEST
cls
echo.
echo  %C2%pytest...%C0%
echo.
python -m pytest tests/ -q --tb=line
echo.
pause
goto MENU

:REM_LOCK
cls
echo.
echo  %C2%Removendo lock...%C0%
echo.
python scripts\_remover_lock.py
echo.
pause
goto MENU

:LIMPAR_JOBS
cls
echo.
echo  %C2%Limpar jobs travados status=rodando...%C0%
echo.
python scripts\_limpar_jobs.py
echo.
set /p APAGAR="  Apagar tambem jobs de erro? [s/N]: "
if /I "%APAGAR%"=="S" python scripts\_limpar_jobs.py --apagar-erros
echo.
pause
goto MENU

:VER_LOG
cls
echo.
echo  %C2%Ultimas 50 linhas de logs\monitor.log%C0%
echo.
if exist "logs\monitor.log" (
  powershell -NoProfile -Command "Get-Content -Path 'logs\monitor.log' -Tail 50 -Encoding UTF8"
) else (
  echo  Log nao encontrado.
)
echo.
echo  %CD%Outros logs em logs\%C0%
if exist "logs" dir /b /o-d "logs\*.log" 2>nul
echo.
pause
goto MENU

:ESPACO
cls
python scripts\_espaco_disco.py
pause
goto MENU

:TG_BOT
cls
echo.
echo  %C2%Bot Telegram interativo%C0%
echo  %CD%Ctrl+C encerra.%C0%
echo.
python telegram_bot.py
if errorlevel 1 pause
goto MENU

:ABRIR_PASTA
start "" explorer "%~dp0"
echo.
echo  %C2%Pasta do projeto aberta.%C0%
timeout /t 1 >nul
goto MENU

:AJUDA
cls
echo.
echo  %C1%%CB%  AJUDA RAPIDA%C0%
echo.
echo  %C5%Dia a dia%C0%
echo    [1] sobe Web + BOT e deixa rodando
echo    [S] ve fila, lock, jobs e ultimas edicoes
echo    [U] [P] consulta publicacoes
echo.
echo  %C5%Validar um mes%C0%
echo    [6] processa AAAA-MM via cache OCR + IA
echo    [7] atalho julho/2026
echo    [M] lista mencoes  -  [Y] resumo mensal  -  [E] exporta CSV
echo.
echo  %C5%Fila grande%C0%
echo    [8] processa N pendentes OCR real
echo    [3] BOT continuo esvazia a fila sozinho
echo    [L] [Q] se travar lock / jobs rodando
echo.
echo  %C5%Qualidade%C0%
echo    [9] reprocessa subdetectados com IA
echo    [I] checa chave/modelo da IA
echo    [A] teste Telegram/arquivo
echo    [O] force-OCR se texto embutido for ruim
echo.
echo  %C5%Cuidado%C0%
echo    [F] force-rescan  -  [C] apaga pubs/mencoes pede SIM
echo.
echo  %C5%Pastas%C0%
echo    edicoes\   PDFs + .ocr.json
echo    atos\      saidas por publicacao
echo    logs\      monitor.log + backups
echo    exportacoes\  CSV/JSON do menu [E]
echo    alertas\   fallback se Telegram falhar
echo.
pause
goto MENU

:LIMPAR
cls
echo.
echo  %C4%ATENCAO: apaga publicacoes, mencoes, jobs e zera flags OCR.%C0%
echo  %CD%Mantem: edicoes, PDFs e caches .ocr.json%C0%
echo.
set /p CONF="  Digite SIM para confirmar: "
if /I not "%CONF%"=="SIM" (
  echo  Cancelado.
  timeout /t 1 >nul
  goto MENU
)
echo.
python scripts\_limpar_processados.py
echo.
pause
goto MENU

:SAIR
echo.
echo  %C2%Ate logo.%C0%
endlocal
exit /b 0