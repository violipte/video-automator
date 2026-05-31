"""Render Worker #2 (paralelo) — WORKER_ID=2, temp_w2/, log render_worker_w2.log."""
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

# Worker 2: isola temp/ + log
env = dict(os.environ)
env["WORKER_ID"] = "2"

subprocess.Popen(
    [sys.executable.replace("pythonw.exe", "python.exe"), os.path.join(pasta, "render_worker.py")],
    cwd=pasta,
    env=env,
    stdout=open(os.path.join(pasta, "logs", "render_worker_w2.log"), "a"),
    stderr=subprocess.STDOUT,
    creationflags=0x08000000,  # CREATE_NO_WINDOW
)
