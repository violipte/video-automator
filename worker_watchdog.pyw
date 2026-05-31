"""Watchdog para os Render Workers — mantem N workers vivos (reinicia se morrer).

5070 Ti (16GB) aguenta multiplos renders em paralelo. NUM_WORKERS controla
quantos workers paralelos sobem. Cada worker isola temp_w{ID}/ e log
render_worker_w{ID}.log via env WORKER_ID.

Roda no boot (atalho na pasta Startup do Windows). Se um worker cair, re-sobe
em ate ~10s. Sem janelas de console (CREATE_NO_WINDOW).
"""
import subprocess
import sys
import os
import time

PASTA = os.path.dirname(os.path.abspath(__file__))
PYTHON = sys.executable.replace("pythonw.exe", "python.exe")
WORKER = os.path.join(PASTA, "render_worker.py")

# Quantos workers paralelos manter. 5070 Ti com 16GB aguenta 2-3 com folga
# (NVENC usa pouca VRAM; Whisper ~3GB e libera apos transcrever).
NUM_WORKERS = 2

# Adicionar DLLs NVIDIA ao PATH (igual aos starters)
_nvidia = os.path.join(os.path.dirname(sys.executable), "Lib", "site-packages", "nvidia")
if os.path.exists(_nvidia):
    for _root, _dirs, _files in os.walk(_nvidia):
        if _root.endswith("bin"):
            os.environ["PATH"] = _root + os.pathsep + os.environ.get("PATH", "")

_CREATE_NO_WINDOW = 0x08000000


def _spawn(wid):
    """Sobe 1 worker com WORKER_ID=wid. Retorna (proc, log_file)."""
    env = dict(os.environ)
    env["WORKER_ID"] = str(wid)
    log_path = os.path.join(PASTA, "logs", f"render_worker_w{wid}.log")
    log_f = open(log_path, "a")
    proc = subprocess.Popen(
        [PYTHON, WORKER],
        cwd=PASTA,
        env=env,
        stdout=log_f,
        stderr=subprocess.STDOUT,
        creationflags=_CREATE_NO_WINDOW,
    )
    return proc, log_f


def main():
    procs = {}  # wid -> (proc, log_f)
    for wid in range(1, NUM_WORKERS + 1):
        procs[wid] = _spawn(wid)
        time.sleep(2)  # escalona o boot pra nao carregar tudo no mesmo instante

    while True:
        time.sleep(10)
        for wid in range(1, NUM_WORKERS + 1):
            proc, log_f = procs[wid]
            if proc.poll() is not None:  # worker morreu
                try:
                    log_f.close()
                except Exception:
                    pass
                procs[wid] = _spawn(wid)


if __name__ == "__main__":
    main()
