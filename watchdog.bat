@echo off
title Video Automator - Watchdog
echo [WATCHDOG] Monitorando Video Automator...
echo [WATCHDOG] Reinicia automaticamente se o servidor morrer.
echo.

:loop
echo [%date% %time%] Iniciando servidor...
cd /d "f:\Canal Dark\Aplicativo de Edição\video-automator"
python app.py
echo.
echo [%date% %time%] Servidor morreu! Reiniciando em 5 segundos...
timeout /t 5 /nobreak >nul
goto loop
