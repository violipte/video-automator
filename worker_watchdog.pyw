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
if PASTA not in sys.path:
    sys.path.insert(0, PASTA)
try:
    import limpar_disco  # limpeza automatica de cache/temp quando F: enche
except Exception:
    limpar_disco = None
PYTHON = sys.executable.replace("pythonw.exe", "python.exe")
WORKER = os.path.join(PASTA, "render_worker.py")
# Consumidor da esteira pos-render (thumb fila -> upload paralelo -> pin fila).
# 1 instancia so; morre -> re-sobe igual aos render workers.
ESTEIRA = os.path.join(PASTA, "esteira_worker.py")

# Quantos workers paralelos manter. STT agora e via Grok API (nao usa GPU),
# entao o Whisper nao disputa mais VRAM -> mais workers cabem.
# Ryzen 9 9950X (16C/32T) + RTX 5070 Ti 16GB aguentam 4 com folga.
NUM_WORKERS = 4

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


def _spawn_esteira():
    """Sobe o esteira_worker (unico). Retorna (proc, log_file)."""
    log_f = open(os.path.join(PASTA, "logs", "esteira_worker.log"), "a")
    proc = subprocess.Popen(
        [PYTHON, ESTEIRA],
        cwd=PASTA,
        env=dict(os.environ, PYTHONUTF8="1"),
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
    est_proc, est_log = _spawn_esteira()

    ciclo = 0
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
        if est_proc.poll() is not None:  # esteira_worker morreu
            try:
                est_log.close()
            except Exception:
                pass
            est_proc, est_log = _spawn_esteira()

        # Limpeza de disco a cada ~5min (30 ciclos de 10s). So age se F: > 85%.
        ciclo += 1
        if limpar_disco is not None and ciclo % 30 == 0:
            try:
                limpar_disco.limpar_se_necessario()
            except Exception:
                pass


if __name__ == "__main__":
    main()
