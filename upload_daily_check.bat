@echo off
REM Rede de seguranca diaria do upload (ver upload_daily_check.py).
REM Registrado como "AutomatorUploadDailyCheck" (09:00) e "...2" (21:00), com --jitter (0-20min).
cd /d "F:\Canal Dark\Aplicativo de Edição\video-automator"
set PYTHONUTF8=1
"C:\Users\Piter Piter\AppData\Local\Programs\Python\Python314\python.exe" -u upload_daily_check.py --apply --jitter >> "logs\upload_daily_check.log" 2>&1
