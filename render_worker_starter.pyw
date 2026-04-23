"""Inicia o Render Worker em background (sem janela de terminal)."""
import subprocess
import sys
import os

pasta = os.path.dirname(os.path.abspath(__file__))
os.chdir(pasta)

# Adicionar DLLs NVIDIA ao PATH
nvidia_path = os.path.join(os.path.dirname(sys.executable), "Lib", "site-packages", "nvidia")
if os.path.exists(nvidia_path):
    for root, dirs, files in os.walk(nvidia_path):
        if root.endswith("bin"):
            os.environ["PATH"] = root + os.pathsep + os.environ.get("PATH", "")

subprocess.Popen(
    [sys.executable.replace("pythonw.exe", "python.exe"), os.path.join(pasta, "render_worker.py")],
    cwd=pasta,
    stdout=open(os.path.join(pasta, "logs", "render_worker.log"), "a"),
    stderr=subprocess.STDOUT,
    creationflags=0x08000000,  # CREATE_NO_WINDOW
)
