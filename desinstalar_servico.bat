@echo off
title Desinstalar Video Automator
echo Removendo tarefa agendada...
schtasks /delete /tn "VideoAutomator" /f
echo.

echo Parando servidor...
for /f "tokens=5" %%a in ('netstat -ano ^| findstr :8500 ^| findstr LISTENING') do taskkill /F /PID %%a 2>nul
echo.

echo [OK] Video Automator removido.
pause
