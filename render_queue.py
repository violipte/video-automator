"""
Fila unica de render. Suporta dois modos:
- LOCAL: worker interno consome a fila e chama render_fn() diretamente (GPU local)
- REMOTO: jobs ficam na fila esperando um render_worker externo buscar via HTTP

O modo e definido por REMOTE_MODE (True = VPS, False = local com GPU).
"""

import json
import threading
import time
import traceback
from queue import Queue, Empty
from pathlib import Path

# === CONFIGURACAO ===
# True = modo VPS (render worker externo busca jobs via API)
# False = modo local (worker interno executa render_fn diretamente)
REMOTE_MODE = True

# Fila global de render
_queue = Queue()
_worker_thread = None
_running = False

# Jobs remotos: job_id -> job_data (para o render worker buscar)
_remote_jobs = []  # lista ordenada de jobs pendentes
_remote_jobs_lock = threading.Lock()
_remote_current = None  # job sendo processado pelo worker remoto

# Callbacks registrados por quem enfileirou
# job_id -> {"on_done": fn, "on_error": fn, "event": Event}
_callbacks = {}
_callbacks_lock = threading.Lock()

# Estado publico (para UI consultar)
estado = {
    "ativo": False,
    "job_atual": None,
    "fila_tamanho": 0,
    "fonte": "",  # "auto" ou "manual"
}


# === WORKER LOCAL (modo local) ===

def _worker():
    """Worker que consome a fila de render localmente. So roda se REMOTE_MODE=False."""
    global _running
    _running = True

    while _running:
        try:
            job = _queue.get(timeout=2)
        except Empty:
            estado["ativo"] = False
            estado["job_atual"] = None
            estado["fila_tamanho"] = _queue.qsize()
            continue

        if job is None:  # Sinal de shutdown
            break

        job_id = job.get("id", "?")
        estado["ativo"] = True
        estado["job_atual"] = job_id
        estado["fila_tamanho"] = _queue.qsize()
        estado["fonte"] = job.get("fonte", "?")

        try:
            render_fn = job.get("render_fn")
            if render_fn:
                render_fn()

            # Callback de sucesso
            with _callbacks_lock:
                cb = _callbacks.pop(job_id, {})
            if cb.get("on_done"):
                cb["on_done"]("")

        except Exception as e:
            traceback.print_exc()
            with _callbacks_lock:
                cb = _callbacks.pop(job_id, {})
            if cb.get("on_error"):
                cb["on_error"](str(e))

        finally:
            estado["ativo"] = _queue.qsize() > 0
            estado["job_atual"] = None
            estado["fila_tamanho"] = _queue.qsize()
            _queue.task_done()

    _running = False


def iniciar_worker():
    """Inicia o worker de render (chamar no startup do app). So faz algo em modo local."""
    if REMOTE_MODE:
        return  # Em modo remoto, nao tem worker local
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_thread = threading.Thread(target=_worker, daemon=True, name="render-worker")
    _worker_thread.start()


# === ENFILEIRAR (ambos os modos) ===

def enfileirar(job_id: str, render_fn=None, fonte: str = "manual",
               on_done=None, on_error=None, job_data: dict = None):
    """Enfileira um job de render.

    Args:
        job_id: identificador unico (ex: "CON_20260411")
        render_fn: funcao que executa o render (modo local apenas)
        fonte: "auto" (orchestrator) ou "manual" (batch)
        on_done: callback quando render concluir
        on_error: callback(erro_str) quando render falhar
        job_data: dict serializavel com dados do job (modo remoto)
    """
    with _callbacks_lock:
        _callbacks[job_id] = {"on_done": on_done, "on_error": on_error}

    if REMOTE_MODE:
        # Modo remoto: guardar job serializavel para o worker externo buscar
        job_entry = {
            "id": job_id,
            "fonte": fonte,
            "ts": time.time(),
            "status": "pending",  # pending -> processing -> done/error
            **(job_data or {}),
        }
        with _remote_jobs_lock:
            _remote_jobs.append(job_entry)
        estado["fila_tamanho"] = len(_remote_jobs)
        estado["ativo"] = True
    else:
        # Modo local: enfileirar com callable
        _queue.put({
            "id": job_id,
            "render_fn": render_fn,
            "fonte": fonte,
            "ts": time.time(),
        })
        estado["fila_tamanho"] = _queue.qsize()
        iniciar_worker()


# === API REMOTA (para render_worker.py buscar jobs) ===

WORKER_JOB_TIMEOUT = 7200  # 2 horas — se job fica "processing" mais que isso, considerar worker morto


def _recuperar_jobs_travados():
    """Verifica se algum job ficou travado em 'processing' (worker morreu).
    Re-enfileira como 'pending' para o proximo worker pegar."""
    now = time.time()
    with _remote_jobs_lock:
        for job in _remote_jobs:
            if job["status"] == "processing":
                started = job.get("started_at", now)
                if now - started > WORKER_JOB_TIMEOUT:
                    print(f"[RENDER_QUEUE] Job {job['id']} travado ha {int(now - started)}s. Re-enfileirando.")
                    job["status"] = "pending"
                    job.pop("started_at", None)


def proximo_job_remoto() -> dict | None:
    """Retorna o proximo job pendente para o render worker. Marca como 'processing'."""
    global _remote_current
    # Primeiro, recuperar jobs que ficaram travados (worker morreu)
    _recuperar_jobs_travados()
    with _remote_jobs_lock:
        for job in _remote_jobs:
            if job["status"] == "pending":
                job["status"] = "processing"
                job["started_at"] = time.time()
                _remote_current = job
                estado["ativo"] = True
                estado["job_atual"] = job["id"]
                estado["fonte"] = job.get("fonte", "?")
                # Retornar copia serializavel (sem callables)
                return {k: v for k, v in job.items() if k != "render_fn"}
    return None


def completar_job_remoto(job_id: str, sucesso: bool, erro: str = "", video_path: str = "",
                          local_storage: str = "local", tamanho_mb: float = 0):
    """Chamado pelo render worker quando termina um job."""
    global _remote_current
    with _remote_jobs_lock:
        for j, job in enumerate(_remote_jobs):
            if job["id"] == job_id:
                _remote_jobs.pop(j)
                break
        _remote_current = None
        estado["fila_tamanho"] = sum(1 for j in _remote_jobs if j["status"] == "pending")
        estado["ativo"] = estado["fila_tamanho"] > 0 or any(j["status"] == "processing" for j in _remote_jobs)
        estado["job_atual"] = None

    # Chamar callbacks
    with _callbacks_lock:
        cb = _callbacks.pop(job_id, {})
    if sucesso:
        if cb.get("on_done"):
            # on_done aceita video_path + kwargs opcionais para metadados de storage
            try:
                cb["on_done"](video_path, local_storage=local_storage, tamanho_mb=tamanho_mb)
            except TypeError:
                # Fallback pra assinatura antiga
                cb["on_done"](video_path)
    else:
        if cb.get("on_error"):
            cb["on_error"](erro)


def jobs_remotos_pendentes() -> list:
    """Lista todos os jobs remotos (para debug/UI)."""
    with _remote_jobs_lock:
        return [
            {k: v for k, v in j.items() if k != "render_fn"}
            for j in _remote_jobs
        ]


# === LIMPAR (ambos os modos) ===

def limpar():
    """Esvazia a fila de render e reseta o estado."""
    # Modo local
    while not _queue.empty():
        try:
            _queue.get_nowait()
            _queue.task_done()
        except Empty:
            break
    # Modo remoto
    global _remote_current
    with _remote_jobs_lock:
        _remote_jobs.clear()
        _remote_current = None
    with _callbacks_lock:
        _callbacks.clear()
    estado["ativo"] = False
    estado["job_atual"] = None
    estado["fila_tamanho"] = 0
    estado["fonte"] = ""


def tamanho_fila() -> int:
    if REMOTE_MODE:
        with _remote_jobs_lock:
            return len(_remote_jobs)
    return _queue.qsize()


def obter_estado() -> dict:
    return dict(estado)
