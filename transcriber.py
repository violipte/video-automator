"""
Módulo de transcrição de áudio usando Whisper.
Converte MP3 de narração em arquivos SRT com timestamps.
"""

import os
import subprocess
import json
import re
from pathlib import Path

TEMP_DIR = Path(__file__).parent / "temp"
TEMP_DIR.mkdir(exist_ok=True)


def obter_duracao(caminho: str) -> float:
    """Retorna a duração em segundos de um arquivo de áudio/vídeo usando ffprobe."""
    cmd = [
        "ffprobe", "-v", "quiet",
        "-print_format", "json",
        "-show_format",
        caminho
    ]
    resultado = subprocess.run(cmd, capture_output=True, text=True)
    if resultado.returncode != 0:
        raise RuntimeError(f"ffprobe falhou: {resultado.stderr}")
    info = json.loads(resultado.stdout)
    return float(info["format"]["duration"])


def _formatar_timestamp_srt(segundos: float) -> str:
    """Converte segundos para formato SRT: HH:MM:SS,mmm"""
    h = int(segundos // 3600)
    m = int((segundos % 3600) // 60)
    s = int(segundos % 60)
    ms = int((segundos % 1) * 1000)
    return f"{h:02d}:{m:02d}:{s:02d},{ms:03d}"


def _segmentos_para_srt(segmentos: list) -> str:
    """Converte lista de segmentos Whisper para texto SRT."""
    linhas = []
    for i, seg in enumerate(segmentos, 1):
        inicio = _formatar_timestamp_srt(seg["start"])
        fim = _formatar_timestamp_srt(seg["end"])
        texto = seg["text"].strip()
        linhas.append(f"{i}")
        linhas.append(f"{inicio} --> {fim}")
        linhas.append(texto)
        linhas.append("")
    return "\n".join(linhas)


def transcrever(mp3_path: str, idioma: str = None, modelo: str = "medium", callback_progresso=None) -> str:
    """
    Transcreve um arquivo MP3 para SRT usando Whisper.

    Args:
        mp3_path: Caminho do arquivo MP3.
        idioma: Código do idioma (en, de, pt, es). None para auto-detectar.
        modelo: Modelo Whisper a usar (tiny, base, small, medium, large-v3).
        callback_progresso: Função callback(percentual: float) para progresso.

    Returns:
        Caminho do arquivo .srt gerado na pasta temp/.
    """
    mp3_path = Path(mp3_path)
    if not mp3_path.exists():
        raise FileNotFoundError(f"Arquivo não encontrado: {mp3_path}")

    nome_base = mp3_path.stem
    srt_path = TEMP_DIR / f"{nome_base}.srt"

    # Obter duração para cálculo de progresso
    duracao_total = obter_duracao(str(mp3_path))

    # Tentar faster-whisper primeiro (mais rápido com GPU)
    try:
        segmentos = _transcrever_faster_whisper(str(mp3_path), idioma, modelo, duracao_total, callback_progresso)
    except ImportError:
        # Fallback para openai-whisper
        try:
            segmentos = _transcrever_openai_whisper(str(mp3_path), idioma, modelo)
        except ImportError:
            raise RuntimeError(
                "Nenhum backend Whisper encontrado. "
                "Instale faster-whisper (pip install faster-whisper) "
                "ou openai-whisper (pip install openai-whisper)."
            )

    if callback_progresso:
        callback_progresso(100.0)

    conteudo_srt = _segmentos_para_srt(segmentos)
    srt_path.write_text(conteudo_srt, encoding="utf-8")
    return str(srt_path)


def _transcrever_faster_whisper(mp3_path: str, idioma: str, modelo: str, duracao_total: float = 0, callback_progresso=None) -> list:
    """Transcrição usando faster-whisper em subprocess isolado.

    Rodar em subprocess protege contra segfault nativo do CTranslate2/cuDNN:
    se o processo filho crashar (exit code != 0), caimos para CPU automaticamente.
    """
    import sys as _sys
    import subprocess as _sp

    worker = Path(__file__).parent / "_whisper_subprocess.py"
    if not worker.exists():
        raise RuntimeError(f"_whisper_subprocess.py nao encontrado: {worker}")

    last_err = ""
    for device in ("cuda", "cpu"):
        cmd = [_sys.executable, "-u", str(worker), mp3_path, modelo, device]
        if idioma:
            cmd.append(idioma)

        proc = _sp.Popen(
            cmd,
            stdout=_sp.PIPE,
            stderr=_sp.PIPE,
            text=True,
            encoding="utf-8",
            errors="replace",
            cwd=str(Path(__file__).parent),
        )

        resultado = []
        err_msg = ""

        for line in proc.stdout:
            line = line.strip()
            if not line:
                continue
            try:
                ev = json.loads(line)
            except Exception:
                continue

            t = ev.get("type")
            if t == "segment":
                resultado.append({
                    "start": ev["start"],
                    "end": ev["end"],
                    "text": ev["text"],
                })
                if callback_progresso and duracao_total > 0:
                    pct = min(95.0, (ev["end"] / duracao_total) * 100)
                    callback_progresso(pct)
            elif t == "done":
                pass
            elif t == "error":
                err_msg = ev.get("msg", "erro desconhecido")

        proc.wait()

        if proc.returncode == 0 and resultado:
            return resultado

        stderr = proc.stderr.read() if proc.stderr else ""
        last_err = err_msg or stderr[-500:] or f"exit code {proc.returncode} (possivel crash nativo)"

        if device == "cuda":
            # Fallback silencioso para CPU
            continue
        raise RuntimeError(f"Transcricao falhou (GPU e CPU): {last_err}")

    raise RuntimeError(f"Transcricao falhou: {last_err}")


def _transcrever_openai_whisper(mp3_path: str, idioma: str, modelo: str) -> list:
    """Transcrição usando openai-whisper (fallback)."""
    import whisper

    model = whisper.load_model(modelo)

    kwargs = {}
    if idioma:
        kwargs["language"] = idioma

    result = model.transcribe(mp3_path, **kwargs)

    resultado = []
    for seg in result["segments"]:
        resultado.append({
            "start": seg["start"],
            "end": seg["end"],
            "text": seg["text"]
        })
    return resultado
