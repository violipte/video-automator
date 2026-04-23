@echo off
title Instalar Video Automator - Render Worker
echo ============================================
echo  Video Automator - Render Worker (Local GPU)
echo ============================================
echo.
echo  O servidor principal roda no VPS.
echo  Este PC roda apenas o render worker (GPU).
echo.

set "PASTA=%~dp0"

:: Remover tarefa antiga se existir
schtasks /delete /tn "VideoAutomatorRenderWorker" /f >nul 2>&1
schtasks /delete /tn "VideoAutomator" /f >nul 2>&1

:: Criar tarefa agendada para o Render Worker
schtasks /create /tn "VideoAutomatorRenderWorker" /tr "pythonw.exe \"%PASTA%render_worker_starter.pyw\"" /sc onlogon /rl highest /f

if %errorlevel% equ 0 (
    echo.
    echo [OK] Render Worker instalado com sucesso!
    echo [OK] Inicia automaticamente com o Windows.
    echo [OK] Busca jobs de render do VPS e renderiza com GPU local.
    echo.
    echo Iniciando agora...
    start "" pythonw.exe "%PASTA%render_worker_starter.pyw"
    timeout /t 5 >nul
    echo Render Worker rodando em background.
    echo Log: %PASTA%logs\render_worker.log
    echo.
) else (
    echo.
    echo [ERRO] Execute como Administrador.
    echo.
)

pause
