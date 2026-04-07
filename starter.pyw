"""Inicia o Video Automator em background (sem janela de terminal)."""
import subprocess
import sys
import os

pasta = os.path.dirname(os.path.abspath(__file__))
os.chdir(pasta)
subprocess.Popen(
    [sys.executable, os.path.join(pasta, "app.py")],
    cwd=pasta,
    creationflags=0x08000000
)
