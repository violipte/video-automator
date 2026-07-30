@echo off
REM Rede de seguranca diaria do upload (ver upload_daily_check.py).
REM Registrado como tarefa agendada "AutomatorUploadDailyCheck" (1x/dia).
cd /d "F:\Canal Dark\Aplicativo de Edição\video-automator"
set PYTHONUTF8=1
"C:\Users\Piter Piter\AppData\Local\Programs\Python\Python314\python.exe" -u upload_daily_check.py --apply >> "logs\upload_daily_check.log" 2>&1
