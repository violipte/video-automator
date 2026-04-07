@echo off
title Instalar Video Automator
echo ============================================
echo  Video Automator - Instalacao Automatica
echo ============================================
echo.

:: Usa %~dp0 para pegar o caminho real da pasta (evita problemas com acentos)
set "PASTA=%~dp0"

:: Criar tarefa agendada que inicia com o Windows usando .pyw (sem janela)
schtasks /create /tn "VideoAutomator" /tr "pythonw.exe \"%PASTA%starter.pyw\"" /sc onlogon /rl highest /f

if %errorlevel% equ 0 (
    echo.
    echo [OK] Servico instalado com sucesso!
    echo [OK] O Video Automator vai iniciar automaticamente com o Windows.
    echo.
    echo Iniciando agora...
    start "" pythonw.exe "%PASTA%starter.pyw"
    timeout /t 4 >nul
    echo Acesse: http://127.0.0.1:8500
    echo.
) else (
    echo.
    echo [ERRO] Execute como Administrador.
    echo.
)

pause
