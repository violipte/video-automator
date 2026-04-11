"""
Fila unica de render. Tanto o modo automatico (orchestrator) quanto o
modo manual (batch/producao) enfileiram jobs aqui. Um unico worker
consome a fila, garantindo que so 1 render roda por vez (GPU/NVENC).
"""

import threading
import time
import traceback
from queue import Queue, Empty
from pathlib import Path

# Fila global de render
_queue = Queue()
_worker_thread = None
_running = False

# Callbacks registrados por quem enfileirou
# job_id -> {"on_progress": fn, "on_done": fn, "on_error": fn}
_callbacks = {}
_callbacks_lock = threading.Lock()

# Estado publico (para UI consultar)
estado = {
    "ativo": False,
    "job_atual": None,
    "fila_tamanho": 0,
    "fonte": "",  # "auto" ou "manual"
}


def _worker():
    """Worker que consome a fila de render. Roda em thread daemon."""
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
                cb["on_done"]()

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
    """Inicia o worker de render (chamar no startup do app)."""
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_thread = threading.Thread(target=_worker, daemon=True, name="render-worker")
    _worker_thread.start()


def enfileirar(job_id: str, render_fn, fonte: str = "manual", on_done=None, on_error=None):
    """Enfileira um job de render.

    Args:
        job_id: identificador unico (ex: "CON_20260411")
        render_fn: funcao que executa o render (sem args)
        fonte: "auto" (orchestrator) ou "manual" (batch)
        on_done: callback quando render concluir
        on_error: callback(erro_str) quando render falhar
    """
    with _callbacks_lock:
        _callbacks[job_id] = {"on_done": on_done, "on_error": on_error}

    _queue.put({
        "id": job_id,
        "render_fn": render_fn,
        "fonte": fonte,
        "ts": time.time(),
    })

    estado["fila_tamanho"] = _queue.qsize()

    # Garantir worker rodando
    iniciar_worker()


def tamanho_fila() -> int:
    return _queue.qsize()


def obter_estado() -> dict:
    return dict(estado)
