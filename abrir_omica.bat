@echo off
:: omica - auto-start e abertura do browser (herdado de abrir_genomics.bat).
:: Atalho opcional em:
::   C:\Users\luizd\AppData\Roaming\Microsoft\Windows\Start Menu\Programs\Startup

title omica

echo ============================================
echo   omica - iniciando...
echo ============================================

:: Aguarda Docker Desktop inicializar (60 tentativas x 2s)
set TIMEOUT=60
set COUNT=0
:waitdocker
docker info >nul 2>&1
if %errorlevel% == 0 goto dockerok
set /a COUNT+=1
if %COUNT% geq %TIMEOUT% goto dockerfail
echo Aguardando Docker... (%COUNT%/%TIMEOUT%)
timeout /t 2 /nobreak >nul
goto waitdocker

:dockerfail
echo ERRO: Docker nao respondeu. Inicie o Docker Desktop e rode novamente.
pause
exit /b 1

:dockerok
echo Docker OK. Subindo containers...
cd /d "C:\github\omica"
docker compose up -d

echo Aguardando a UI ficar disponivel na porta 8710...
set COUNT=0
:waitui
powershell -command "try { (New-Object Net.Sockets.TcpClient('localhost',8710)).Close(); exit 0 } catch { exit 1 }" >nul 2>&1
if %errorlevel% == 0 goto uiok
set /a COUNT+=1
if %COUNT% geq 30 goto uifail
timeout /t 2 /nobreak >nul
goto waitui

:uifail
echo UI nao respondeu - abrindo mesmo assim...

:uiok
echo Abrindo a omica no navegador...
start "" "http://localhost:8710"
echo.
echo omica disponivel em: http://localhost:8710
echo API docs em:         http://localhost:8710/api/docs
