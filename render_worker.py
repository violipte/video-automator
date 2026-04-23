"""
Render Worker — roda no PC local com GPU (RTX 3060).
Busca jobs de render do servidor VPS via HTTP, renderiza localmente,
e reporta conclusao de volta ao VPS.

Uso: python render_worker.py
Config: worker_config.json (na mesma pasta)
"""

import json
import os
import sys
import time
import traceback
import urllib.request
import urllib.error
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "worker_config.json"
LOG_FILE = BASE_DIR / "logs" / "render_worker.log"

# Defaults
DEFAULT_CONFIG = {
    "vps_url": "http://127.0.0.1:8500",
    "worker_token": "",
    "poll_interval": 5,
    "temp_dir": str(BASE_DIR / "temp"),
    "cache_dir": str(BASE_DIR / "cache"),
    "export_base": "F:/Canal Dark/Automator Exports",
}


def load_config() -> dict:
    """Carrega worker_config.json."""
    if CONFIG_FILE.exists():
        with open(CONFIG_FILE, "r", encoding="utf-8") as f:
            cfg = json.load(f)
        # Merge com defaults
        for k, v in DEFAULT_CONFIG.items():
            if k not in cfg:
                cfg[k] = v
        return cfg
    return dict(DEFAULT_CONFIG)


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


def api_request(config: dict, method: str, path: str, data: dict = None) -> dict | None:
    """Faz request autenticado ao VPS."""
    url = config["vps_url"].rstrip("/") + path
    headers = {
        "Authorization": f"Bearer {config['worker_token']}",
        "Content-Type": "application/json",
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


def report_progress(config: dict, canal_idx: int, etapa_detalhe: str, progresso: int = 0):
    """Reporta progresso intermediario ao VPS."""
    try:
        api_request(config, "POST", "/api/render-worker/progress", {
            "canal_idx": canal_idx,
            "etapa_detalhe": etapa_detalhe,
            "progresso": progresso,
        })
    except Exception:
        pass  # Nao falhar por causa de report de progresso


def report_complete(config: dict, job_id: str, sucesso: bool, erro: str = "", video_path: str = ""):
    """Reporta conclusao do job ao VPS."""
    return api_request(config, "POST", "/api/render-worker/complete", {
        "job_id": job_id,
        "sucesso": sucesso,
        "erro": erro,
        "video_path": video_path,
    })


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
        report_progress(config, canal_idx, f"MP3 local ({tag}.mp3)", 5)
    else:
        report_progress(config, canal_idx, "Baixando MP3 do VPS...", 5)
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

    # Verificar se video ja existe
    if Path(video_path).exists() and Path(video_path).stat().st_size > 1000:
        log(f"  Video ja existe: {video_nome}")
        report_progress(config, canal_idx, f"Video existe ({video_nome})", 100)
        report_complete(config, job_id, True, video_path=video_path)
        return True

    max_retries = 2
    for attempt in range(max_retries + 1):
        try:
            # 3. Transcrever (Whisper GPU)
            report_progress(config, canal_idx, "Transcrevendo...", 10)
            log(f"  Transcrevendo...")
            srt_path = transcriber.transcrever(narr_local, idioma)

            # 4. Corrigir legendas
            report_progress(config, canal_idx, "Corrigindo legendas...", 20)
            log(f"  Corrigindo legendas...")
            lc = tmpl.get("legenda_config", {})
            maiuscula = lc.get("maiuscula", tmpl.get("estilo_legenda") == 2)
            srt_corrigido = subtitle_fixer.corrigir_srt(
                srt_path, idioma, job.get("template_id", ""), maiuscula,
                max_linhas=lc.get("max_linhas", 2),
                max_chars=lc.get("max_chars", 30),
                regras_template=tmpl.get("regras")
            )

            # 5. Renderizar (FFmpeg NVENC GPU)
            retry_msg = f" (retry {attempt})" if attempt > 0 else ""
            report_progress(config, canal_idx, f"Renderizando{retry_msg}...", 30)
            log(f"  Renderizando{retry_msg}...")
            engine = VideoEngine(tmpl, narr_local, video_path)
            engine.montar(srt_path=srt_corrigido)

            if Path(video_path).exists() and Path(video_path).stat().st_size > 1000:
                size_mb = Path(video_path).stat().st_size / (1024 * 1024)
                log(f"  OK: {video_nome} ({size_mb:.1f} MB)")
                report_complete(config, job_id, True, video_path=video_path)
                return True
            else:
                raise RuntimeError("Video nao gerado ou vazio")

        except Exception as e:
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

    consecutive_errors = 0

    while True:
        try:
            job = api_request(config, "GET", "/api/render-worker/next-job")

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
