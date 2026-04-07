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
    """Transcrição usando faster-whisper (CTranslate2)."""
    from faster_whisper import WhisperModel

    kwargs = {}
    if idioma:
        kwargs["language"] = idioma

    # Tentar GPU primeiro, se falhar (falta CUDA libs) usar CPU
    for device, compute in [("cuda", "float16"), ("cpu", "int8")]:
        try:
            model = WhisperModel(modelo, device=device, compute_type=compute)
            segments, info = model.transcribe(mp3_path, **kwargs)
            resultado = []
            for seg in segments:
                resultado.append({
                    "start": seg.start,
                    "end": seg.end,
                    "text": seg.text
                })
                if callback_progresso and duracao_total > 0:
                    pct = min(95.0, (seg.end / duracao_total) * 100)
                    callback_progresso(pct)
            # Liberar modelo da VRAM para o FFmpeg NVENC poder usar
            del model
            import gc, ctypes
            gc.collect()
            try:
                import torch
                torch.cuda.empty_cache()
            except ImportError:
                pass
            return resultado
        except Exception as e:
            if device == "cpu":
                raise
            # GPU falhou, tentar CPU
            continue

    raise RuntimeError("Falha na transcrição com GPU e CPU")


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
