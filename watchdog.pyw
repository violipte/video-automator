"""
Watchdog silencioso — monitora o servidor a cada 15s.
Se detectar que caiu, reinicia automaticamente.
Roda sem janela (.pyw), completamente em background.
"""
import subprocess
import sys
import os
import time
import urllib.request
import json
from datetime import datetime

PASTA = os.path.dirname(os.path.abspath(__file__))
APP_PY = os.path.join(PASTA, "app.py")
LOG_FILE = os.path.join(PASTA, "logs", "watchdog.log")
URL_HEALTH = "http://127.0.0.1:8500/api/health"
CHECK_INTERVAL = 15  # segundos entre checks
RESTART_DELAY = 5    # segundos antes de reiniciar
MAX_LOG_LINES = 500  # manter log compacto

_server_proc = None


def log(msg):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        os.makedirs(os.path.dirname(LOG_FILE), exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        # Truncar log se muito grande
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > MAX_LOG_LINES:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-MAX_LOG_LINES:])
    except Exception:
        pass


def is_server_alive():
    """Checa se o servidor responde em /api/health."""
    try:
        req = urllib.request.Request(URL_HEALTH, method="GET")
        with urllib.request.urlopen(req, timeout=5) as resp:
            data = json.loads(resp.read())
            return data.get("ok", False)
    except Exception:
        return False


def start_server():
    """Inicia o servidor como processo desatrelado."""
    global _server_proc
    log("Iniciando servidor...")
    try:
        # Usar python.exe (nao pythonw.exe) — RADAR do Windows mata pythonw por "memory leak"
        python_exe = sys.executable.replace("pythonw.exe", "python.exe")
        env = dict(os.environ)
        env["PYTHONUNBUFFERED"] = "1"
        # Desabilitar RADAR leak detection para este processo
        env["__COMPAT_LAYER"] = "RunAsInvoker"
        env["PYTHONMALLOC"] = "malloc"  # Usar malloc do sistema (mais estavel que pymalloc)
        _server_proc = subprocess.Popen(
            [python_exe, APP_PY],
            cwd=PASTA,
            stdout=open(os.path.join(PASTA, "logs", "server.log"), "a"),
            stderr=subprocess.STDOUT,
            creationflags=0x08000000,  # CREATE_NO_WINDOW
            env=env,
        )
        log(f"Servidor iniciado (PID {_server_proc.pid})")
    except Exception as e:
        log(f"ERRO ao iniciar: {e}")


def kill_orphans():
    """Mata FFmpeg orfao se existir."""
    try:
        subprocess.run(
            ["taskkill", "/F", "/IM", "ffmpeg.exe"],
            capture_output=True, timeout=5,
            creationflags=0x08000000,
        )
    except Exception:
        pass


def read_production_state():
    """Le production_state.json e retorna (data_idx, ordem_colunas) se havia producao ativa."""
    try:
        state_file = os.path.join(PASTA, "production_state.json")
        with open(state_file, "r", encoding="utf-8") as f:
            state = json.load(f)
        if state.get("ativo") and state.get("data_idx") is not None:
            return state["data_idx"], state.get("ordem_colunas")
    except Exception:
        pass
    return None, None


def clean_production_state():
    """Limpa production_state.json para evitar retomar producao orfã."""
    try:
        state_file = os.path.join(PASTA, "production_state.json")
        with open(state_file, "w", encoding="utf-8") as f:
            json.dump({
                "ativo": False, "data_ref": "", "data_idx": None,
                "ordem_colunas": None, "inicio": None, "total_canais": 0,
                "canal_atual": 0, "canais": [], "log": [],
                "concluidos": 0, "erros": 0, "pulados": 0, "cancelado": False,
            }, f)
    except Exception:
        pass


def restart_production(data_idx, ordem_colunas=None):
    """Reinicia producao via API apos o servidor subir."""
    try:
        body = json.dumps({"data_idx": data_idx, "ordem": ordem_colunas}).encode()
        req = urllib.request.Request(
            "http://127.0.0.1:8500/api/producao-completa/iniciar",
            data=body,
            headers={"Content-Type": "application/json"},
            method="POST",
        )
        with urllib.request.urlopen(req, timeout=10) as resp:
            result = json.loads(resp.read())
            return result.get("ok", False)
    except Exception as e:
        log(f"Erro ao reiniciar producao: {e}")
        return False


def main():
    log("=== WATCHDOG INICIADO ===")

    # Se servidor ja estiver rodando, apenas monitorar
    if is_server_alive():
        log("Servidor ja esta rodando, monitorando...")
    else:
        start_server()
        time.sleep(8)  # dar tempo pro uvicorn subir

    consecutive_failures = 0

    while True:
        time.sleep(CHECK_INTERVAL)

        if is_server_alive():
            consecutive_failures = 0
            continue

        consecutive_failures += 1

        # Esperar 2 checks consecutivos antes de reiniciar (evitar falso positivo)
        if consecutive_failures < 2:
            continue

        log(f"Servidor NAO responde ({consecutive_failures} checks). Reiniciando...")
        consecutive_failures = 0

        # Salvar info de producao ativa ANTES de limpar
        active_data_idx, active_ordem = read_production_state()

        # Matar processo antigo se existir
        if _server_proc and _server_proc.poll() is None:
            try:
                _server_proc.kill()
                _server_proc.wait(timeout=5)
            except Exception:
                pass

        kill_orphans()
        clean_production_state()
        time.sleep(RESTART_DELAY)
        start_server()
        time.sleep(10)  # dar tempo pro uvicorn subir

        # Se havia producao ativa, reiniciar automaticamente
        if active_data_idx is not None and is_server_alive():
            log(f"Producao ativa detectada (data_idx={active_data_idx}). Reiniciando producao...")
            if restart_production(active_data_idx, active_ordem):
                log("Producao reiniciada com sucesso!")
            else:
                log("Falha ao reiniciar producao.")


if __name__ == "__main__":
    main()
