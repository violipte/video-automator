"""Watchdog para o Render Worker — reinicia se morrer."""
import subprocess
import sys
import os
import time

PASTA = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable.replace("pythonw.exe", "python.exe")
WORKER = os.path.join(PASTA, "render_worker.py")
LOG = os.path.join(PASTA, "logs", "render_worker.log")

while True:
    log_f = open(LOG, "a")
    proc = subprocess.Popen(
        [PYTHON, WORKER],
        cwd=PASTA,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        creationflags=0x08000000,
    )
    proc.wait()  # Espera o worker morrer
    log_f.close()
    time.sleep(5)  # Pausa antes de reiniciar
