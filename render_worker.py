"""
Render Worker — roda no PC local com GPU (RTX 3060).
Busca jobs de render do servidor VPS via HTTP, renderiza localmente,
e reporta conclusao de volta ao VPS.

Uso: python render_worker.py
Config: worker_config.json (na mesma pasta)
"""

import json
import os
import subprocess
import sys
import threading
import time
import traceback
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

# === ANTI-FLICKER (Windows) ===========================================
# Monkeypatch subprocess.Popen pra SEMPRE usar CREATE_NO_WINDOW quando o
# worker roda sob pythonw (sem console proprio). Sem isso, cada FFmpeg do
# engine (~139 clips/video), ffprobe, whisper e chatterbox abre uma janela
# de console que "pisca" e rouba o foco de teclado/mouse do operador.
# Cobre todos os subprocess.Popen/run/call deste processo (engine, transcriber,
# narrator_chatterbox). Subprocessos em OUTROS processos (ex: chatterbox_runner
# que roda no venv 3.12) sao tratados separadamente la dentro.
if os.name == "nt":
    _CNW = getattr(subprocess, "CREATE_NO_WINDOW", 0x08000000)
    _orig_popen_init = subprocess.Popen.__init__
    def _popen_no_window(self, *args, **kwargs):
        kwargs["creationflags"] = kwargs.get("creationflags", 0) | _CNW
        return _orig_popen_init(self, *args, **kwargs)
    subprocess.Popen.__init__ = _popen_no_window
# ======================================================================

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "worker_config.json"
# Log separado por worker (mesmo padrao temp_w{WID})
_WID_LOG = os.environ.get("WORKER_ID", "").strip()
LOG_FILE = BASE_DIR / "logs" / (f"render_worker_w{_WID_LOG}.log" if _WID_LOG else "render_worker.log")

# temp_dir isolado por WORKER_ID pra suportar 2+ workers paralelos
# (mesmo padrao usado em engine.py e transcriber.py).
_WID_DEF = os.environ.get("WORKER_ID", "").strip()
_TEMP_NAME_DEF = f"temp_w{_WID_DEF}" if _WID_DEF else "temp"

# Defaults
DEFAULT_CONFIG = {
    "vps_url": "http://127.0.0.1:8500",
    "worker_token": "",
    "poll_interval": 5,
    "temp_dir": str(BASE_DIR / _TEMP_NAME_DEF),
    "cache_dir": str(BASE_DIR / "cache"),
    "export_base": "F:/Canal Dark/Automator Exports",
}


def load_config() -> dict:
    """Carrega config: env vars > worker_config.json > defaults.

    Env vars (uso em pod cloud sem worker_config.json):
      VPS_URL, WORKER_TOKEN, POLL_INTERVAL, TEMP_DIR, CACHE_DIR, EXPORT_BASE
    """
    cfg = dict(DEFAULT_CONFIG)
    # 1. Tenta carregar JSON file (uso local)
    if CONFIG_FILE.exists():
        try:
            with open(CONFIG_FILE, "r", encoding="utf-8") as f:
                file_cfg = json.load(f)
            cfg.update(file_cfg)
        except Exception as e:
            print(f"[worker] erro lendo {CONFIG_FILE}: {e}")
    # 2. Override por env vars (uso cloud/Docker)
    env_map = {
        "VPS_URL": "vps_url",
        "WORKER_TOKEN": "worker_token",
        "POLL_INTERVAL": "poll_interval",
        "TEMP_DIR": "temp_dir",
        "CACHE_DIR": "cache_dir",
        "EXPORT_BASE": "export_base",
    }
    for env_key, cfg_key in env_map.items():
        v = os.environ.get(env_key)
        if v:
            cfg[cfg_key] = int(v) if cfg_key == "poll_interval" else v
    return cfg


def log(msg: str):
    ts = datetime.now().strftime("%Y-%m-%d %H:%M:%S")
    line = f"[{ts}] {msg}"
    print(line)
    try:
        LOG_FILE.parent.mkdir(parents=True, exist_ok=True)
        with open(LOG_FILE, "a", encoding="utf-8") as f:
            f.write(line + "\n")
        # Truncar log
        with open(LOG_FILE, "r", encoding="utf-8") as f:
            lines = f.readlines()
        if len(lines) > 1000:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                f.writelines(lines[-500:])
    except Exception:
        pass


def _worker_id_header() -> str:
    """Worker id consistente entre polls + heartbeats (X-Worker-Id)."""
    try:
        hostname = subprocess.check_output(["hostname"], timeout=2).decode().strip()
    except Exception:
        hostname = "unknown"
    wid = os.environ.get("WORKER_ID", "default")
    return f"{hostname}-w{wid}"


def _coletar_telemetria() -> dict:
    """Coleta GPU temp/util/mem (via nvidia-smi) + CPU util (psutil).

    CPU temp NAO disponivel no Windows por padrao (precisa LibreHardwareMonitor).
    Retorna dict com chaves: gpu_temp_c, gpu_util_pct, gpu_mem_used_mb, gpu_mem_total_mb,
    cpu_util_pct, gpu_name.
    """
    t = {}
    try:
        out = subprocess.check_output(
            ["nvidia-smi",
             "--query-gpu=temperature.gpu,utilization.gpu,memory.used,memory.total,name",
             "--format=csv,noheader,nounits"],
            timeout=5,
        ).decode().strip()
        # Ex: "31, 0, 301, 16303, NVIDIA GeForce RTX 5070 Ti"
        parts = [p.strip() for p in out.split(",")]
        if len(parts) >= 5:
            t["gpu_temp_c"] = int(parts[0])
            t["gpu_util_pct"] = int(parts[1])
            t["gpu_mem_used_mb"] = int(parts[2])
            t["gpu_mem_total_mb"] = int(parts[3])
            t["gpu_name"] = parts[4]
    except Exception:
        pass
    try:
        import psutil
        t["cpu_util_pct"] = round(psutil.cpu_percent(interval=None), 1)
        t["ram_used_gb"] = round(psutil.virtual_memory().used / 1e9, 1)
        t["ram_total_gb"] = round(psutil.virtual_memory().total / 1e9, 1)
    except Exception:
        pass
    # CPU temp via LibreHardwareMonitor (HTTP server localhost:8085)
    try:
        import json as _json
        with urllib.request.urlopen("http://localhost:8085/data.json", timeout=2) as r:
            lhm = _json.loads(r.read())

        def _find_temp(node, predicates):
            """Busca primeiro sensor Temperature que match qualquer predicate."""
            if node.get("Type") == "Temperature":
                sid = (node.get("SensorId") or "").lower()
                name = (node.get("Text") or "").lower()
                for pred in predicates:
                    if pred in sid or pred in name:
                        v = (node.get("Value") or "").replace(",", ".").replace("°C", "").strip()
                        try: return float(v)
                        except: pass
            for c in node.get("Children", []):
                r = _find_temp(c, predicates)
                if r is not None:
                    return r
            return None

        # CPU principal: Tctl/Tdie (AMD) ou Package (Intel)
        cpu_t = _find_temp(lhm, ["tctl", "tdie", "package", "core (t"])
        if cpu_t is not None:
            t["cpu_temp_c"] = round(cpu_t, 1)
        # Motherboard socket
        socket_t = _find_temp(lhm, ["cpu socket", "vrm mos"])
        if socket_t is not None:
            t["motherboard_temp_c"] = round(socket_t, 1)
        # NVMe principal (primeiro)
        nvme_t = _find_temp(lhm, ["/nvme/0/temperature/0", "/nvme/0/temperature/1"])
        if nvme_t is not None:
            t["nvme_temp_c"] = round(nvme_t, 1)
    except Exception:
        pass  # LHM nao instalado/rodando — ignora silencioso
    return t


def _telemetria_loop(config: dict):
    """Background thread que reporta telemetria pra VPS a cada 10s."""
    wid = _worker_id_header()
    url = config["vps_url"].rstrip("/") + "/api/system-telemetry"
    headers = {
        "Authorization": f"Bearer {config['worker_token']}",
        "Content-Type": "application/json",
        "X-Worker-Id": wid,
    }
    while True:
        try:
            data = _coletar_telemetria()
            data["worker_id"] = wid
            data["ts"] = time.time()
            body = json.dumps(data).encode()
            req = urllib.request.Request(url, data=body, headers=headers, method="POST")
            try:
                with urllib.request.urlopen(req, timeout=10) as resp:
                    resp.read()
            except Exception:
                pass  # nao falhar se VPS indisponivel
        except Exception:
            pass
        time.sleep(10)


def api_request(config: dict, method: str, path: str, data: dict = None) -> dict | None:
    """Faz request autenticado ao VPS."""
    url = config["vps_url"].rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {config['worker_token']}",
        "Content-Type": "application/json",
        "X-Worker-Id": _worker_id_header(),
    }
    body = json.dumps(data).encode() if data else None

    try:
        req = urllib.request.Request(url, data=body, headers=headers, method=method)
        with urllib.request.urlopen(req, timeout=30) as resp:
            if resp.status == 204:
                return None
            return json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 204:
            return None
        log(f"HTTP Error {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
        return None
    except Exception as e:
        log(f"Request error: {e}")
        return None


def download_file(config: dict, vps_path: str, local_dest: str) -> bool:
    """Baixa arquivo do VPS."""
    url = config["vps_url"].rstrip("/") + f"/api/render-worker/download?path={urllib.request.quote(vps_path)}"
    headers = {"Authorization": f"Bearer {config['worker_token']}"}

    try:
        req = urllib.request.Request(url, headers=headers, method="GET")
        Path(local_dest).parent.mkdir(parents=True, exist_ok=True)
        with urllib.request.urlopen(req, timeout=300) as resp:
            with open(local_dest, "wb") as f:
                while True:
                    chunk = resp.read(65536)
                    if not chunk:
                        break
                    f.write(chunk)
        size = Path(local_dest).stat().st_size
        log(f"  Baixado: {Path(local_dest).name} ({size} bytes)")
        return True
    except Exception as e:
        log(f"  Erro download: {e}")
        return False


def report_progress(config: dict, canal_idx: int, etapa_detalhe: str, progresso: int = 0,
                    job_id: str = ""):
    """Reporta progresso intermediario ao VPS.

    job_id (opcional): se fornecido, VPS reseta started_at do job (heartbeat)
    para evitar re-enqueue por timeout. Recomendado em qualquer chamada
    durante operacao longa (transcricao, render).
    """
    try:
        payload = {
            "canal_idx": canal_idx,
            "etapa_detalhe": etapa_detalhe,
            "progresso": progresso,
        }
        if job_id:
            payload["job_id"] = job_id
        api_request(config, "POST", "/api/render-worker/progress", payload)
    except Exception:
        pass  # Nao falhar por causa de report de progresso


class _HeartbeatThread(threading.Thread):
    """Thread daemon que envia report_progress a cada 30s durante operacao
    longa (engine.montar). Garante que o VPS sabe que o worker esta vivo
    e nao re-enfileira o job. Para de rodar quando stop_event e setado.
    """
    def __init__(self, config, job_id, canal_idx, mensagem, progresso, interval=30):
        super().__init__(daemon=True)
        self.config = config
        self.job_id = job_id
        self.canal_idx = canal_idx
        self.mensagem = mensagem
        self.progresso = progresso
        self.interval = interval
        self.stop_event = threading.Event()

    def run(self):
        while not self.stop_event.wait(self.interval):
            report_progress(self.config, self.canal_idx, self.mensagem,
                            self.progresso, job_id=self.job_id)

    def stop(self):
        self.stop_event.set()


def report_complete(config: dict, job_id: str, sucesso: bool, erro: str = "", video_path: str = "",
                    local_storage: str = "local", tamanho_mb: float = 0):
    """Reporta conclusao do job ao VPS.

    local_storage: 'local' ou 'google_drive' (registro de onde o MP4 final ficou)
    tamanho_mb: tamanho do MP4 final em MB
    """
    return api_request(config, "POST", "/api/render-worker/complete", {
        "job_id": job_id,
        "sucesso": sucesso,
        "erro": erro,
        "video_path": video_path,
        "local_storage": local_storage,
        "tamanho_mb": tamanho_mb,
    })


# === NARRATION (Chatterbox local) ===

def report_narration_progress(config: dict, job_id: str, canal_idx: int = None,
                              etapa_detalhe: str = "", progresso: int = 0):
    """Heartbeat opcional pra narration jobs."""
    try:
        payload = {"job_id": job_id, "etapa_detalhe": etapa_detalhe, "progresso": progresso}
        if canal_idx is not None:
            payload["canal_idx"] = canal_idx
        api_request(config, "POST", "/api/narration-worker/progress", payload)
    except Exception:
        pass


def report_narration_complete(config: dict, job_id: str, sucesso: bool, erro: str = "",
                              audio_local: str = "", duracao_seg: float = 0,
                              chunks: int = 0, tempo_geracao_seg: float = 0):
    """Reporta conclusao de narration job."""
    return api_request(config, "POST", "/api/narration-worker/complete", {
        "job_id": job_id,
        "sucesso": sucesso,
        "erro": erro,
        "audio_local": audio_local,
        "duracao_seg": duracao_seg,
        "chunks": chunks,
        "tempo_geracao_seg": tempo_geracao_seg,
    })


def upload_mp3_multipart(config: dict, job_id: str, local_mp3: str) -> bool:
    """Faz POST multipart/form-data pra /api/narration-worker/upload-mp3.

    Sem usar libs externas (urllib stdlib + boundary manual).
    """
    import uuid as _uuid
    boundary = f"----worker-{_uuid.uuid4().hex}"
    url = config["vps_url"].rstrip("/") + "/api/narration-worker/upload-mp3"

    try:
        with open(local_mp3, "rb") as f:
            mp3_bytes = f.read()
    except Exception as e:
        log(f"  Erro lendo MP3 pra upload: {e}")
        return False

    # Monta body multipart manualmente
    lines = []
    lines.append(f"--{boundary}".encode())
    lines.append(b'Content-Disposition: form-data; name="job_id"')
    lines.append(b"")
    lines.append(job_id.encode())
    lines.append(f"--{boundary}".encode())
    fname = Path(local_mp3).name
    lines.append(f'Content-Disposition: form-data; name="file"; filename="{fname}"'.encode())
    lines.append(b"Content-Type: audio/mpeg")
    lines.append(b"")
    lines.append(mp3_bytes)
    lines.append(f"--{boundary}--".encode())
    lines.append(b"")
    body = b"\r\n".join(lines)

    headers = {
        "Authorization": f"Bearer {config['worker_token']}",
        "Content-Type": f"multipart/form-data; boundary={boundary}",
        "X-Worker-Id": _worker_id_header(),
    }
    req = urllib.request.Request(url, data=body, headers=headers, method="POST")
    try:
        # Timeout generoso pra MP3 grandes (50MB+ em link lento)
        with urllib.request.urlopen(req, timeout=600) as resp:
            payload = json.loads(resp.read())
            return bool(payload.get("ok"))
    except urllib.error.HTTPError as e:
        log(f"  Upload HTTP {e.code}: {e.read().decode('utf-8', errors='replace')[:200]}")
        return False
    except Exception as e:
        log(f"  Upload error: {e}")
        return False


def process_narration_job(config: dict, job: dict) -> bool:
    """Processa um job de narracao Chatterbox local. Upload MP3 -> complete.

    job traz: id, texto, voice_ref, nome_saida, destino_remoto, exaggeration,
    cfg_weight, chunk_max_chars.
    """
    import narrator_chatterbox

    job_id = job.get("id", "?")
    texto = job.get("texto", "")
    voice_ref = job.get("voice_ref", "")
    nome_saida = job.get("nome_saida", job_id)
    exaggeration = float(job.get("exaggeration", 0.5))
    cfg_weight = float(job.get("cfg_weight", 0.5))
    chunk_max_chars = int(job.get("chunk_max_chars", 300))
    model_variant = (job.get("model_variant") or "base").lower()

    log(f"=== Narracao Chatterbox: {job_id} ===")
    log(f"  texto={len(texto)} chars, voice_ref={Path(voice_ref).name if voice_ref else '(vazio)'}, variant={model_variant}")

    if not narrator_chatterbox.disponivel():
        msg = f"venv Chatterbox indisponivel em {narrator_chatterbox.CHATTERBOX_DIR}"
        log(f"  ERRO: {msg}")
        report_narration_complete(config, job_id, False, erro=msg)
        return False

    if not Path(voice_ref).exists():
        msg = f"voice_ref nao existe no worker local: {voice_ref}"
        log(f"  ERRO: {msg}")
        report_narration_complete(config, job_id, False, erro=msg)
        return False

    # MP3 gerado em pasta temp local antes do upload
    local_tmp = Path(config.get("temp_dir", "temp")) / "chatterbox_out"
    local_tmp.mkdir(parents=True, exist_ok=True)
    local_mp3 = str(local_tmp / f"{nome_saida}.mp3")

    canal_idx_narr = job.get("canal_idx")
    report_narration_progress(config, job_id, canal_idx=canal_idx_narr,
                              etapa_detalhe="Chatterbox iniciando...", progresso=1)

    # Callback de progresso REAL por chunk. Mapeia chunks 0-N pra progresso 5-95%
    # (reserva 0-5 pra init/upload e 95-100 pra concat/conv/upload).
    def _on_chatterbox_progress(chunk_n, chunk_total):
        if chunk_total <= 0:
            return
        # 5% inicial + 90% durante chunks + 5% final (concat+upload)
        pct = 5 + int(90 * chunk_n / chunk_total)
        if pct < 5: pct = 5
        if pct > 95: pct = 95
        detalhe = f"Chatterbox {chunk_n}/{chunk_total}"
        report_narration_progress(config, job_id, canal_idx=canal_idx_narr,
                                  etapa_detalhe=detalhe, progresso=pct)

    # Heartbeat de seguranca (se nenhum chunk reportar progresso por muito tempo,
    # ainda mantemos VPS sabendo que o worker esta vivo)
    hb_stop = threading.Event()
    def _hb_loop():
        while not hb_stop.wait(120):  # 2min
            report_narration_progress(config, job_id, canal_idx=canal_idx_narr,
                                      etapa_detalhe="Chatterbox em andamento...", progresso=-1)
    hb_thread = threading.Thread(target=_hb_loop, daemon=True)
    hb_thread.start()

    try:
        result = narrator_chatterbox.narrar_chatterbox(
            texto=texto,
            voice_ref=voice_ref,
            nome_saida=nome_saida,
            destino_final=local_mp3,
            exaggeration=exaggeration,
            cfg_weight=cfg_weight,
            chunk_max_chars=chunk_max_chars,
            model_variant=model_variant,
            on_progress=_on_chatterbox_progress,
        )
    finally:
        hb_stop.set()

    if not result.get("ok"):
        erro = result.get("erro", "Chatterbox falhou")
        log(f"  ERRO Chatterbox: {erro[:200]}")
        report_narration_complete(config, job_id, False, erro=erro)
        return False

    duracao = result.get("duracao_seg", 0)
    chunks = result.get("chunks", 0)
    tempo_gen = result.get("tempo_geracao_seg", 0)
    log(f"  Chatterbox OK: {duracao:.0f}s audio, {chunks} chunks, {tempo_gen/60:.1f}min")

    # Upload do MP3 pro VPS
    report_narration_progress(config, job_id, etapa_detalhe="Upload MP3...", progresso=95)
    log(f"  Uploading {local_mp3} ({Path(local_mp3).stat().st_size/1024/1024:.1f}MB)...")
    if not upload_mp3_multipart(config, job_id, local_mp3):
        log(f"  ERRO upload MP3 falhou")
        report_narration_complete(config, job_id, False, erro="upload MP3 falhou")
        return False

    # Marca complete (audio_local = destino_remoto do job; VPS ja sabe)
    audio_final_path = job.get("destino_remoto", "")
    report_narration_complete(
        config, job_id, True,
        audio_local=audio_final_path,
        duracao_seg=duracao, chunks=chunks, tempo_geracao_seg=tempo_gen,
    )
    log(f"  Concluido: {job_id}")
    # Limpa MP3 local apos upload OK (libera espaco)
    try:
        Path(local_mp3).unlink(missing_ok=True)
    except OSError:
        pass
    return True


def _validar_mp4_integro(path: str) -> tuple[bool, float]:
    """Valida se MP4 tem moov atom (ffprobe duration > 0).

    Retorna (ok, duracao_segundos). ok=False se arquivo corrompido,
    sem moov atom, ou ffprobe falhar.

    Usado pra evitar skip-existing de MP4 corrompido (size > 1KB mas
    sem moov atom = lixo). Bug historico do RunPod: workers reportavam
    complete em MP4 sem moov por kill prematuro de pod no faststart final.
    """
    try:
        out = subprocess.check_output(
            ["ffprobe", "-v", "error", "-show_entries", "format=duration",
             "-of", "csv=p=0", str(path)],
            timeout=15, stderr=subprocess.DEVNULL
        ).decode().strip()
        if not out:
            return False, 0.0
        dur = float(out)
        return dur > 0, dur
    except (subprocess.TimeoutExpired, subprocess.CalledProcessError,
            ValueError, FileNotFoundError, OSError):
        return False, 0.0


def process_job(config: dict, job: dict) -> bool:
    """Processa um job de render completo: transcricao + legendas + render."""
    import transcriber
    import subtitle_fixer
    from engine import VideoEngine

    job_id = job["id"]
    tag = job["tag"]
    canal_idx = job.get("canal_idx", 0)
    tmpl = job["template"]
    idioma = job.get("idioma", "en")
    data_pasta = job.get("data_pasta", "")
    video_pasta = job.get("video_pasta", "")
    video_nome = job.get("video_nome", f"{tag}.mp4")

    log(f"=== Processando: {job_id} ===")
    log(f"  Template: {tmpl.get('nome', '?')}, idioma: {idioma}")

    export_base = Path(config.get("export_base", "F:/Canal Dark/Automator Exports"))

    # 1. Buscar MP3 — local é fonte da verdade, VPS é fallback
    narr_vps_path = job.get("narr_path_vps", "")
    narr_dir_local = export_base / data_pasta / "Narracoes"
    narr_dir_local.mkdir(parents=True, exist_ok=True)
    narr_local = str(narr_dir_local / f"{tag}.mp3")

    if Path(narr_local).exists() and Path(narr_local).stat().st_size > 1000:
        log(f"  MP3 local existe: {tag}.mp3 ({Path(narr_local).stat().st_size} bytes)")
        report_progress(config, canal_idx, f"MP3 local ({tag}.mp3)", 5, job_id=job_id)
    else:
        report_progress(config, canal_idx, "Baixando MP3 do VPS...", 5, job_id=job_id)
        log(f"  Baixando narracao: {job.get('narr_filename', '?')}")
        if not download_file(config, narr_vps_path, narr_local):
            report_complete(config, job_id, False, "Falha ao baixar MP3")
            return False
        log(f"  MP3 salvo em: {narr_local}")

    # 2. Sincronizar roteiro do VPS pro local (se não existe local)
    roteiro_dir_local = export_base / data_pasta / "Roteiros"
    roteiro_dir_local.mkdir(parents=True, exist_ok=True)
    roteiro_local = roteiro_dir_local / f"{tag}.txt"
    if not roteiro_local.exists():
        roteiro_vps_path = str(Path(job.get("narr_path_vps", "")).parent.parent / "Roteiros" / f"{tag}.txt")
        if download_file(config, roteiro_vps_path, str(roteiro_local)):
            log(f"  Roteiro salvo: {roteiro_local.name}")
        # Nao falhar se roteiro nao baixar — nao é necessario pro render

    # 3. Criar pasta de saida de video (sempre usar export_base local, ignorar path do VPS)
    output_dir = export_base / data_pasta / "Videos"
    output_dir.mkdir(parents=True, exist_ok=True)
    video_path = str(output_dir / video_nome)

    # Skip-existing: video ja gerado E integro (moov atom presente).
    # Antes: so checava size > 1KB - falso-positivo com MP4 sem moov atom
    # (lixo do RunPod por kill prematuro de pod no faststart final).
    if Path(video_path).exists() and Path(video_path).stat().st_size > 1000:
        ok, dur = _validar_mp4_integro(video_path)
        if ok:
            log(f"  Video ja existe (integro, {dur:.0f}s): {video_nome}")
            report_progress(config, canal_idx, f"Video existe ({video_nome})", 100, job_id=job_id)
            report_complete(config, job_id, True, video_path=video_path)
            return True
        else:
            log(f"  Video existe mas CORROMPIDO (sem moov atom). Deletando e re-renderizando.")
            try:
                Path(video_path).unlink(missing_ok=True)
            except OSError:
                pass

    max_retries = 2
    # Template pode desabilitar legenda (ex: DE com transcricao ruim).
    # Pulamos Whisper + subtitle_fixer + ASS burn quando legenda_ativa=False.
    _legenda_ativa = tmpl.get("legenda_ativa", True)
    for attempt in range(max_retries + 1):
        heartbeat = None
        try:
            srt_corrigido = None
            if _legenda_ativa:
                # 3. Transcrever (Whisper GPU)
                report_progress(config, canal_idx, "Transcrevendo...", 10, job_id=job_id)
                log(f"  Transcrevendo...")
                # Heartbeat durante transcricao (pode demorar 1-3min)
                heartbeat = _HeartbeatThread(config, job_id, canal_idx, "Transcrevendo...", 10)
                heartbeat.start()
                try:
                    # Permite override do modelo Whisper via template (default medium).
                    # Ex: "large-v3", "large-v3-turbo", "small", etc.
                    whisper_modelo = (tmpl.get("whisper_model") or "medium").strip()
                    srt_path = transcriber.transcrever(narr_local, idioma, modelo=whisper_modelo)
                finally:
                    heartbeat.stop()
                    heartbeat.join(timeout=5)
                    heartbeat = None

                # 4. Corrigir legendas
                report_progress(config, canal_idx, "Corrigindo legendas...", 20, job_id=job_id)
                log(f"  Corrigindo legendas...")
                lc = tmpl.get("legenda_config", {})
                maiuscula = lc.get("maiuscula", tmpl.get("estilo_legenda") == 2)
                srt_corrigido = subtitle_fixer.corrigir_srt(
                    srt_path, idioma, job.get("template_id", ""), maiuscula,
                    max_linhas=lc.get("max_linhas", 2),
                    max_chars=lc.get("max_chars", 30),
                    regras_template=tmpl.get("regras")
                )
            else:
                report_progress(config, canal_idx, "Legenda desativada (skip Whisper)", 20, job_id=job_id)
                log(f"  Legenda desativada no template - skip Whisper + fix")

            # 5. Renderizar (FFmpeg NVENC GPU) — passa callback pra reportar
            # progresso real (Ken Burns 0-60%, montagem final 60-100% mapeado pra 30-100)
            retry_msg = f" (retry {attempt})" if attempt > 0 else ""
            # Combina callback de progresso REAL (do master) + job_id pro heartbeat
            # do tocar_job_remoto (do DEV). Cada call do report_progress reseta
            # started_at no VPS, entao o callback (chamado a cada 3% ou 8s)
            # ja serve como heartbeat efetivo - dispensa _HeartbeatThread separada
            # durante render. Throttle do callback garante minimo 1 call a cada
            # 8s, MUITO mais frequente que o WORKER_JOB_TIMEOUT=7200s.
            report_progress(config, canal_idx, f"Renderizando{retry_msg}... 30%", 30, job_id=job_id)
            log(f"  Renderizando{retry_msg}...")
            engine = VideoEngine(tmpl, narr_local, video_path)

            _last_pct = [30]
            _last_report_ts = [time.time()]
            def _progress_cb(pct):
                # Mapeia 0-100 do engine pra 30-100 do worker (deixa 30% pra Whisper+legendas)
                new_pct = int(30 + (pct or 0) * 0.70)
                if new_pct < 30: new_pct = 30
                if new_pct > 100: new_pct = 100
                # Throttle: report a cada 5% OU a cada 15s OU sempre quando >=99.
                # Reduzido de 3%/8s -> 5%/15s em 2026-05-11 pois saturava VPS
                # com progress requests (curl externo timeoutava por concorrencia).
                # WORKER_JOB_TIMEOUT=300s comporta heartbeat a cada 15s com 20x folga.
                now = time.time()
                if (new_pct - _last_pct[0] >= 5) or (now - _last_report_ts[0] >= 15) or new_pct >= 99:
                    _last_pct[0] = new_pct
                    _last_report_ts[0] = now
                    try:
                        report_progress(config, canal_idx, f"Renderizando{retry_msg}... {new_pct}%", new_pct, job_id=job_id)
                    except Exception:
                        pass

            engine.montar(srt_path=srt_corrigido, callback_progresso=_progress_cb)

            if Path(video_path).exists() and Path(video_path).stat().st_size > 1000:
                # Valida moov atom ANTES de reportar complete. Sem isso,
                # MP4 sem moov passava como sucesso e contaminava o volume
                # RunPod / pasta Exports com lixo. (Bug historico de corrupcao)
                ok, dur = _validar_mp4_integro(video_path)
                if not ok:
                    log(f"  ERRO: video gerado mas SEM moov atom (corrompido). Deletando.")
                    try:
                        Path(video_path).unlink(missing_ok=True)
                    except OSError:
                        pass
                    raise RuntimeError("Video gerado sem moov atom (corrompido)")

                size_mb = Path(video_path).stat().st_size / (1024 * 1024)
                log(f"  OK: {video_nome} ({size_mb:.1f} MB, {dur:.0f}s)")

                # === UPLOAD GOOGLE DRIVE (opcional, por template) ===
                # Template define output_destination = "local" (default) ou "google_drive".
                # Se Drive: faz upload, cria subpasta YYYY-MM-DD automaticamente,
                # opcionalmente deleta local apos upload OK (drive_config.delete_local_after_upload).
                drive_storage = "local"  # default
                dest = tmpl.get("output_destination", "local")
                if dest == "google_drive":
                    drive_cfg = tmpl.get("drive_config", {}) or {}
                    folder_id_raiz = (drive_cfg.get("folder_id") or "").strip()
                    delete_local = bool(drive_cfg.get("delete_local_after_upload", False))
                    if not folder_id_raiz:
                        log(f"  AVISO Drive: output_destination=google_drive mas drive_config.folder_id vazio. Mantendo local.")
                    else:
                        try:
                            import drive_uploader
                            report_progress(config, canal_idx, "Upload Drive...", 99, job_id=job_id)
                            log(f"  Upload Drive: pasta_raiz={folder_id_raiz[:20]}..., data={data_pasta}")
                            res = drive_uploader.upload_video(
                                local_path=video_path,
                                folder_id_raiz=folder_id_raiz,
                                data_pasta=data_pasta,
                            )
                            if res.get("ok"):
                                drive_storage = "google_drive"
                                file_id = res.get("file_id")
                                log(f"  Drive OK: file_id={file_id} ({res.get('tamanho_mb',0):.1f}MB)"
                                    f"{' SKIP (ja existia)' if res.get('skip') else ''}")
                                # Deleta local se configurado E upload foi sucesso real (nao skip-existing)
                                if delete_local and not res.get("skip"):
                                    try:
                                        Path(video_path).unlink(missing_ok=True)
                                        log(f"  Local deletado (delete_local_after_upload=True)")
                                    except OSError as _de:
                                        log(f"  AVISO: falha ao deletar local: {_de}")
                            else:
                                log(f"  AVISO Drive upload falhou: {res.get('erro')}. Mantendo local.")
                        except Exception as _eup:
                            log(f"  AVISO Drive upload exception: {_eup}. Mantendo local.")

                report_complete(config, job_id, True, video_path=video_path,
                                local_storage=drive_storage,
                                tamanho_mb=size_mb)
                return True
            else:
                raise RuntimeError("Video nao gerado ou vazio")

        except Exception as e:
            # Garantir que qualquer heartbeat orfao seja parado
            if heartbeat is not None:
                try:
                    heartbeat.stop()
                    heartbeat.join(timeout=5)
                except Exception:
                    pass
                heartbeat = None
            if Path(video_path).exists():
                Path(video_path).unlink(missing_ok=True)
            if attempt < max_retries:
                log(f"  ERRO (tentativa {attempt + 1}): {e}. Retentando em 10s...")
                time.sleep(10)
            else:
                log(f"  ERRO FATAL apos {max_retries + 1} tentativas: {e}")
                traceback.print_exc()
                report_complete(config, job_id, False, str(e))
                return False

    return False


def main():
    config = load_config()
    log("=== RENDER WORKER INICIADO ===")
    log(f"  VPS: {config['vps_url']}")
    log(f"  Poll interval: {config['poll_interval']}s")
    log(f"  Token: ...{config['worker_token'][-8:]}" if config['worker_token'] else "  Token: NAO CONFIGURADO")

    if not config["worker_token"]:
        log("ERRO: worker_token nao configurado em worker_config.json")
        sys.exit(1)

    # Adicionar DLLs NVIDIA ao PATH (mesmo codigo do app.py)
    try:
        import ctypes
        _nvidia_path = Path(sys.executable).parent / "Lib" / "site-packages" / "nvidia"
        if _nvidia_path.exists():
            for dll_dir in _nvidia_path.glob("*/bin"):
                os.add_dll_directory(str(dll_dir))
                os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
            _cublas = _nvidia_path / "cublas" / "bin" / "cublas64_12.dll"
            if _cublas.exists():
                ctypes.CDLL(str(_cublas))
    except Exception:
        pass

    # Inicia thread de telemetria (GPU temp/util/mem + CPU util)
    _telem_thread = threading.Thread(target=_telemetria_loop, args=(config,),
                                      daemon=True, name="telemetria")
    _telem_thread.start()
    log("Telemetria thread iniciada (GPU/CPU a cada 10s)")

    consecutive_errors = 0

    while True:
        try:
            # worker_id identifica unicamente esse worker pro watchdog do VPS
            wid = os.environ.get("WORKER_ID", "default")
            try:
                hostname = subprocess.check_output(["hostname"], timeout=2).decode().strip()
            except Exception:
                hostname = "unknown"
            worker_full_id = f"{hostname}-w{wid}"
            worker_qs = urllib.request.quote(worker_full_id)

            # Prioridade: narracao Chatterbox > render. Narracao deve ir primeiro
            # pra liberar o pipeline (proximo passo eh render desse canal). Se
            # narracao 15-25min e render 25-30min, fazer narracao antes destrava
            # render mais cedo do que se a gente renderasse primeiro outro canal.
            narr_job = api_request(config, "GET",
                f"/api/narration-worker/next-job?worker_id={worker_qs}")
            if narr_job:
                consecutive_errors = 0
                process_narration_job(config, narr_job)
                continue  # imediato proximo poll (pode ter mais narracao)

            job = api_request(config, "GET",
                f"/api/render-worker/next-job?worker_id={worker_qs}")
            if job:
                consecutive_errors = 0
                process_job(config, job)
            else:
                time.sleep(config["poll_interval"])

        except KeyboardInterrupt:
            log("Worker encerrado pelo usuario.")
            break
        except Exception as e:
            consecutive_errors += 1
            log(f"Erro no loop principal: {e}")
            if consecutive_errors > 10:
                log("Muitos erros consecutivos, pausando 60s...")
                time.sleep(60)
                consecutive_errors = 0
            else:
                time.sleep(config["poll_interval"])


if __name__ == "__main__":
    main()
