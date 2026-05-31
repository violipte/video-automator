"""
Fila de narracao via Chatterbox local (GPU). Mesmo modelo do render_queue:
- LOCAL: worker interno chama narrate_fn() direto
- REMOTO: job fica na fila esperando worker externo buscar via HTTP

Diferenca pra render: o worker UPLOAD o MP3 final pra VPS apos gerar (multipart),
enquanto render_worker baixa MP3 ja existente. Aqui o fluxo eh inverso.

Tipico fluxo remoto (VPS + worker local):
1. Orchestrator chama enfileirar() com texto + voice_ref + nome_saida
2. Worker local polla /api/narration-worker/next-job -> recebe job_data
3. Worker executa narrator_chatterbox.narrar_chatterbox(...) localmente
4. Worker faz POST /api/narration-worker/upload-mp3 (multipart) com o MP3
5. Worker chama /api/narration-worker/complete com sucesso=true + destino_remoto
6. VPS dispara on_done() do callback registrado, libera o pipeline (-> render)
"""

import threading
import time
import traceback
from queue import Queue, Empty

# === CONFIGURACAO ===
# True = modo VPS (worker externo busca jobs via API)
# False = modo local (worker interno executa narrate_fn diretamente)
# Por padrao casa com render_queue.REMOTE_MODE (mesma maquina manda os jobs)
try:
    import render_queue as _rq
    REMOTE_MODE = _rq.REMOTE_MODE
except Exception:
    REMOTE_MODE = True

# Fila local (so usada quando REMOTE_MODE=False)
_queue = Queue()
_worker_thread = None
_running = False

# Jobs remotos
_remote_jobs = []  # lista ordenada
_remote_jobs_lock = threading.Lock()
_remote_current = None

# Callbacks por job_id
_callbacks = {}
_callbacks_lock = threading.Lock()

# Trackear workers de narracao
_workers_seen = {}
_workers_seen_lock = threading.Lock()

# Estado publico
estado = {
    "ativo": False,
    "job_atual": None,
    "fila_tamanho": 0,
}

# Stale recovery: re-enfileira job orfao (worker morreu/reboot mid-narracao).
# 15min (era 1h). O worker MANDA heartbeat por chunk via /api/narration-worker/
# progress -> tocar_job_remoto() reseta started_at. Job VIVO nunca passa de
# ~12min sem heartbeat (STALL_TIMEOUT_SEG interno do narrator_chatterbox mata
# travamento em 12min). Logo 15min > 12min: nao re-enfileira job vivo, mas
# recupera orfao em 15min em vez de 1h.
# Historico: reboot do PC em 29/05 matou narracao EN3 mid-job -> orfao preso
# 90min (orchestrator _evt.wait) porque timeout era 1h. Reduzido pra 15min.
WORKER_JOB_TIMEOUT = 15 * 60  # 15min


def marcar_worker_visto(worker_id: str = "default"):
    with _workers_seen_lock:
        _workers_seen[worker_id or "default"] = time.time()


# === WORKER LOCAL (REMOTE_MODE=False) ===

def _worker():
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

        if job is None:
            break

        job_id = job.get("id", "?")
        estado["ativo"] = True
        estado["job_atual"] = job_id
        estado["fila_tamanho"] = _queue.qsize()

        try:
            narrate_fn = job.get("narrate_fn")
            if narrate_fn:
                resultado = narrate_fn()  # deve retornar dict {ok, audio_local, ...}
            else:
                resultado = {"ok": False, "erro": "sem narrate_fn"}

            with _callbacks_lock:
                cb = _callbacks.pop(job_id, {})
            if resultado.get("ok"):
                if cb.get("on_done"):
                    cb["on_done"](resultado)
            else:
                if cb.get("on_error"):
                    cb["on_error"](resultado.get("erro", "erro desconhecido"))

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
    if REMOTE_MODE:
        return
    global _worker_thread
    if _worker_thread and _worker_thread.is_alive():
        return
    _worker_thread = threading.Thread(target=_worker, daemon=True, name="narration-worker")
    _worker_thread.start()


# === ENFILEIRAR ===

def enfileirar(job_id: str, texto: str = "", voice_ref: str = "",
               nome_saida: str = "", destino_remoto: str = "",
               exaggeration: float = 0.5, cfg_weight: float = 0.5,
               chunk_max_chars: int = 300, model_variant: str = "base",
               canal_idx: int | None = None,
               on_done=None, on_error=None, narrate_fn=None):
    """Enfileira um job de narracao Chatterbox.

    Modo remoto: orchestrator passa texto/voice_ref/etc serializaveis.
    Modo local: orchestrator pode passar narrate_fn direto (callable).

    Args:
        job_id: identificador unico (ex: "CON_20260514_narr")
        texto: roteiro completo
        voice_ref: path da voz de referencia (existe no worker local)
        nome_saida: nome base do MP3 (ex: "Channel 14-05")
        destino_remoto: path onde a VPS espera receber o MP3 (apos upload)
        exaggeration, cfg_weight: params Chatterbox
        chunk_max_chars: max chars por chunk (300 default)
        canal_idx: indice do canal em production_log (pra worker reportar progresso N/total no UI)
        on_done(resultado): callback ao sucesso. resultado contem audio_local na VPS
        on_error(erro_str): callback de erro
        narrate_fn: callable para modo local (override do dict serializavel)
    """
    with _callbacks_lock:
        _callbacks[job_id] = {"on_done": on_done, "on_error": on_error}

    if REMOTE_MODE:
        job_entry = {
            "id": job_id,
            "ts": time.time(),
            "status": "pending",
            "texto": texto,
            "voice_ref": voice_ref,
            "nome_saida": nome_saida,
            "destino_remoto": destino_remoto,
            "exaggeration": exaggeration,
            "cfg_weight": cfg_weight,
            "chunk_max_chars": chunk_max_chars,
            "model_variant": (model_variant or "base").lower(),
            "canal_idx": canal_idx,
        }
        with _remote_jobs_lock:
            existing = next((j for j in _remote_jobs if j["id"] == job_id), None)
            if existing is not None:
                print(f"[NARRATION_QUEUE] enfileirar({job_id}): ja existe (status={existing.get('status')}), atualizando callbacks")
                estado["fila_tamanho"] = sum(1 for j in _remote_jobs if j["status"] == "pending")
                return
            _remote_jobs.append(job_entry)
        estado["fila_tamanho"] = sum(1 for j in _remote_jobs if j["status"] == "pending")
        estado["ativo"] = True
    else:
        _queue.put({
            "id": job_id,
            "narrate_fn": narrate_fn,
            "ts": time.time(),
        })
        estado["fila_tamanho"] = _queue.qsize()
        iniciar_worker()


# === API REMOTA (worker polla) ===

def _recuperar_jobs_travados():
    now = time.time()
    with _remote_jobs_lock:
        for job in _remote_jobs:
            if job["status"] == "processing":
                started = job.get("started_at", now)
                if now - started > WORKER_JOB_TIMEOUT:
                    print(f"[NARRATION_QUEUE] Job {job['id']} travado ha {int(now - started)}s. Re-enfileirando.")
                    job["status"] = "pending"
                    job.pop("started_at", None)


def tocar_job_remoto(job_id: str) -> bool:
    """Heartbeat: reseta started_at. Util se worker quiser reportar progresso."""
    with _remote_jobs_lock:
        for job in _remote_jobs:
            if job["id"] == job_id and job.get("status") == "processing":
                job["started_at"] = time.time()
                return True
    return False


def proximo_job_remoto(worker_id: str = "default") -> dict | None:
    global _remote_current
    marcar_worker_visto(worker_id)
    _recuperar_jobs_travados()
    with _remote_jobs_lock:
        for job in _remote_jobs:
            if job["status"] == "pending":
                job["status"] = "processing"
                job["started_at"] = time.time()
                _remote_current = job
                estado["ativo"] = True
                estado["job_atual"] = job["id"]
                # Copia serializavel (sem callables)
                return {k: v for k, v in job.items() if k != "narrate_fn"}
    return None


def completar_job_remoto(job_id: str, sucesso: bool, erro: str = "",
                          audio_local: str = "", duracao_seg: float = 0,
                          chunks: int = 0, tempo_geracao_seg: float = 0):
    """Worker chama ao terminar. Idempotente (se chamar 2x, segunda eh no-op)."""
    global _remote_current
    job_existia = False
    with _remote_jobs_lock:
        for j, job in enumerate(_remote_jobs):
            if job["id"] == job_id:
                _remote_jobs.pop(j)
                job_existia = True
                break
        if job_existia:
            _remote_current = None
            estado["fila_tamanho"] = sum(1 for j in _remote_jobs if j["status"] == "pending")
            estado["ativo"] = estado["fila_tamanho"] > 0 or any(j["status"] == "processing" for j in _remote_jobs)
            estado["job_atual"] = None

    if not job_existia:
        print(f"[NARRATION_QUEUE] completar_job_remoto({job_id}): job ja completou (idempotente), ignorando duplicata")
        return

    with _callbacks_lock:
        cb = _callbacks.pop(job_id, {})
    if sucesso:
        if cb.get("on_done"):
            resultado = {
                "ok": True,
                "audio_local": audio_local,
                "duracao_seg": duracao_seg,
                "chunks": chunks,
                "tempo_geracao_seg": tempo_geracao_seg,
            }
            cb["on_done"](resultado)
    else:
        if cb.get("on_error"):
            cb["on_error"](erro)


def jobs_remotos_pendentes() -> list:
    with _remote_jobs_lock:
        return [
            {k: v for k, v in j.items() if k != "narrate_fn"}
            for j in _remote_jobs
        ]


# === LIMPAR ===

def limpar():
    while not _queue.empty():
        try:
            _queue.get_nowait()
            _queue.task_done()
        except Empty:
            break
    global _remote_current
    with _remote_jobs_lock:
        _remote_jobs.clear()
        _remote_current = None
    with _callbacks_lock:
        _callbacks.clear()
    estado["ativo"] = False
    estado["job_atual"] = None
    estado["fila_tamanho"] = 0


def tamanho_fila() -> int:
    if REMOTE_MODE:
        with _remote_jobs_lock:
            return len(_remote_jobs)
    return _queue.qsize()


def obter_estado() -> dict:
    return dict(estado)
