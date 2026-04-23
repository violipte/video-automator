"""
Narrador TTS via Inworld (fallback da ai33.pro/Minimax).

Inworld aceita 2000 chars/request (vs 8000 do Minimax) e retorna audio MP3
sincronamente em base64 no campo "audioContent". Sem fila, sem polling.

Uso tipico: chamado pelo orchestrator quando a narracao via Minimax falha
todos os retries.
"""

import base64
import json
import subprocess
import time
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).parent
NARRACOES_DIR = BASE_DIR / "narracoes"
NARRACOES_DIR.mkdir(exist_ok=True)

API_BASE = "https://api.inworld.ai"
INWORLD_CHUNK_LIMIT = 1900  # margem de 100 chars no limite de 2000 da API
INWORLD_MAX_RETRIES = 2
INWORLD_TIMEOUT = 120.0

_http_client = httpx.Client(
    timeout=httpx.Timeout(INWORLD_TIMEOUT, connect=15.0),
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
)


def _dividir_em_chunks(texto: str, max_chars: int) -> list:
    """Divide texto em chunks respeitando paragrafos, depois frases."""
    paragrafos = texto.split("\n\n")
    chunks = []
    chunk_atual = ""

    for p in paragrafos:
        if len(chunk_atual) + len(p) + 2 > max_chars and chunk_atual:
            chunks.append(chunk_atual.strip())
            chunk_atual = p
        else:
            chunk_atual += ("\n\n" if chunk_atual else "") + p

    if chunk_atual.strip():
        chunks.append(chunk_atual.strip())

    # Se algum chunk ainda for grande demais, quebra por frase
    final = []
    for c in chunks:
        if len(c) <= max_chars:
            final.append(c)
            continue
        import re
        frases = re.split(r"(?<=[.!?])\s+", c)
        sub = ""
        for f in frases:
            if len(sub) + len(f) + 1 > max_chars and sub:
                final.append(sub.strip())
                sub = f
            else:
                sub += (" " if sub else "") + f
        if sub.strip():
            final.append(sub.strip())

    return final if final else [texto]


def _concatenar_audios(paths: list, output: str):
    """Concatena MP3s via FFmpeg concat demuxer."""
    if not paths:
        raise RuntimeError("Nenhum audio para concatenar")
    if len(paths) == 1:
        Path(paths[0]).replace(output)
        return

    list_file = Path(output).parent / f"_inworld_concat_{int(time.time())}.txt"
    abs_paths = [str(Path(p).resolve()).replace("\\", "/") for p in paths]
    list_file.write_text(
        "\n".join(f"file '{p}'" for p in abs_paths),
        encoding="utf-8",
    )

    try:
        subprocess.run(
            [
                "ffmpeg", "-y", "-hide_banner", "-loglevel", "error",
                "-f", "concat", "-safe", "0", "-i", str(list_file),
                "-c:a", "libmp3lame", "-b:a", "128k",
                output,
            ],
            check=True,
            timeout=300,
        )
    finally:
        list_file.unlink(missing_ok=True)


def gerar_tts_inworld(
    api_key: str, voice_id: str, texto: str,
    model: str = "inworld-tts-1.5-max",
) -> dict:
    """Chama Inworld TTS uma vez. Retorna {"ok": bool, "audio_bytes": bytes, "erro": str}."""
    body = {
        "text": texto,
        "voiceId": voice_id,
        "modelId": model,
        "audioConfig": {"audioEncoding": "MP3", "sampleRateHertz": 48000},
    }
    headers = {
        "Authorization": f"Basic {api_key}",
        "Content-Type": "application/json",
    }
    try:
        resp = _http_client.post(
            f"{API_BASE}/tts/v1/voice",
            json=body,
            headers=headers,
            timeout=INWORLD_TIMEOUT,
        )
        resp.raise_for_status()
        data = resp.json()
        audio_b64 = data.get("audioContent", "")
        if not audio_b64:
            return {"ok": False, "audio_bytes": b"", "erro": "audioContent vazio"}
        return {"ok": True, "audio_bytes": base64.b64decode(audio_b64), "erro": ""}
    except httpx.HTTPStatusError as e:
        body_text = e.response.text[:300] if e.response is not None else ""
        return {"ok": False, "audio_bytes": b"", "erro": f"HTTP {e.response.status_code}: {body_text}"}
    except Exception as e:
        return {"ok": False, "audio_bytes": b"", "erro": str(e)}


def narrar_inworld_chunked(
    api_key: str, voice_id: str, texto: str,
    nome_saida: str, pasta: str = "",
    model: str = "inworld-tts-1.5-max",
    destino_final: str = "",
) -> dict:
    """Gera narracao completa via Inworld com chunking + concat.

    Args:
        api_key: Inworld API key (Basic Auth, já base64-encoded).
        voice_id: voice_id do Inworld (ex: 'default-xxx__bill').
        texto: roteiro completo.
        nome_saida: nome base do arquivo final (ex: 'ENS').
        pasta: diretorio de saida.
        model: 'inworld-tts-1.5-max' ou 'inworld-tts-1.5-mini'.
        destino_final: caminho completo do MP3 final (override do calculado).

    Returns:
        {"ok": bool, "audio_local": str, "erro": str, "chunks": int}
    """
    output_dir = Path(pasta) if pasta else NARRACOES_DIR
    if not output_dir.is_absolute():
        output_dir = BASE_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    destino = destino_final or str(output_dir / f"{nome_saida}.mp3")
    destino_dir = Path(destino).parent
    destino_dir.mkdir(parents=True, exist_ok=True)

    chunks = _dividir_em_chunks(texto, INWORLD_CHUNK_LIMIT)
    total = len(chunks)
    print(f"[INWORLD-SEQ] {nome_saida}: {total} chunks (limit={INWORLD_CHUNK_LIMIT} chars)")

    chunk_paths = []
    try:
        for i, chunk_text in enumerate(chunks):
            chunk_path = str(destino_dir / f"_inworld_chunk_{nome_saida}_{i}.mp3")
            # Resume: se chunk ja existe no disco, pula
            if Path(chunk_path).exists() and Path(chunk_path).stat().st_size > 1000:
                print(f"[INWORLD-SEQ] {nome_saida}: Chunk {i+1}/{total} - JA EXISTE, pulando")
                chunk_paths.append(chunk_path)
                continue

            print(f"[INWORLD-SEQ] {nome_saida}: Chunk {i+1}/{total} - gerando ({len(chunk_text)} chars)...")

            last_err = ""
            for attempt in range(INWORLD_MAX_RETRIES + 1):
                r = gerar_tts_inworld(api_key, voice_id, chunk_text, model=model)
                if r["ok"]:
                    Path(chunk_path).write_bytes(r["audio_bytes"])
                    print(f"[INWORLD-SEQ] {nome_saida}: Chunk {i+1}/{total} - OK ({len(r['audio_bytes'])//1024}KB)")
                    chunk_paths.append(chunk_path)
                    break
                last_err = r["erro"]
                if attempt < INWORLD_MAX_RETRIES:
                    wait = 5 * (attempt + 1)
                    print(f"[INWORLD-SEQ] {nome_saida}: Chunk {i+1}/{total} - falhou ({last_err[:100]}). Retry em {wait}s...")
                    time.sleep(wait)
            else:
                raise RuntimeError(f"Chunk {i+1}/{total}: {last_err}")

        if len(chunk_paths) != total:
            raise RuntimeError(f"Apenas {len(chunk_paths)}/{total} chunks baixados")

        print(f"[INWORLD-SEQ] {nome_saida}: Concatenando {total} chunks...")
        _concatenar_audios(chunk_paths, destino)

        # Limpa chunks temporarios
        for cp in chunk_paths:
            Path(cp).unlink(missing_ok=True)

        print(f"[INWORLD-SEQ] {nome_saida}: Concluido -> {destino}")
        return {"ok": True, "audio_local": destino, "erro": "", "chunks": total}

    except Exception as e:
        print(f"[INWORLD-SEQ] {nome_saida}: Erro ({e}). {len(chunk_paths)} chunks preservados.")
        return {"ok": False, "audio_local": "", "erro": str(e), "chunks": total}
