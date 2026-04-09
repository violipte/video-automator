"""
Motor de narração TTS via ai33.pro (ElevenLabs + Minimax).
Suporta vozes de banco, vozes clonadas, e polling de tarefas assíncronas.
"""

import json
import time
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).parent
NARRACOES_DIR = BASE_DIR / "narracoes"
NARRACOES_DIR.mkdir(exist_ok=True)


def _output_path_com_data(output_dir: Path, nome: str) -> str:
    """Cria subpasta por data e retorna path. Nome: 'TAG DD-MM' → '2026-MM-DD/TAG DD-MM.mp3'"""
    import re
    from datetime import datetime as dt
    match = re.search(r'(\d{2})-(\d{2})$', nome)
    if match:
        dd, mm = match.group(1), match.group(2)
        ano = dt.now().strftime("%Y")
        subpasta = output_dir / f"{ano}-{mm}-{dd}"
        subpasta.mkdir(parents=True, exist_ok=True)
        return str(subpasta / f"{nome}.mp3")
    return str(output_dir / f"{nome}.mp3")

API_BASE = "https://api.ai33.pro"


def _headers(api_key: str) -> dict:
    return {
        "xi-api-key": api_key,
        "Content-Type": "application/json",
    }


# === VOZES ===

def listar_vozes_elevenlabs_shared(api_key: str) -> list:
    """Lista vozes compartilhadas (Voice Library) do ElevenLabs."""
    try:
        resp = httpx.get(
            f"{API_BASE}/v1/shared-voices?page_size=100",
            headers={"xi-api-key": api_key},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        vozes = data.get("voices", [])
        return [
            {
                "voice_id": v.get("voice_id", ""),
                "name": v.get("name", ""),
                "labels": v.get("labels", {}),
                "preview_url": v.get("preview_url", ""),
                "category": "shared",
                "bookmarked": False,
                "provider": "elevenlabs_shared",
            }
            for v in vozes
        ]
    except Exception as e:
        return [{"error": str(e)}]


def listar_vozes_elevenlabs(api_key: str) -> list:
    """Lista vozes do ElevenLabs. Marca favoritos/bookmarked."""
    try:
        resp = httpx.get(
            f"{API_BASE}/v2/voices?page_size=100",
            headers={"xi-api-key": api_key},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        vozes = data.get("voices", data) if isinstance(data, dict) else data
        if isinstance(vozes, list):
            return [
                {
                    "voice_id": v.get("voice_id", ""),
                    "name": v.get("name", ""),
                    "labels": v.get("labels", {}),
                    "preview_url": v.get("preview_url", ""),
                    "category": v.get("category", ""),
                    "bookmarked": bool(v.get("is_bookmarked")),
                    "provider": "elevenlabs",
                }
                for v in vozes
            ]
        return []
    except Exception as e:
        return [{"error": str(e)}]


def listar_vozes_minimax(api_key: str, page: int = 1, page_size: int = 50) -> list:
    """Lista vozes disponíveis do Minimax."""
    try:
        resp = httpx.post(
            f"{API_BASE}/v1m/voice/list",
            headers=_headers(api_key),
            json={"page": page, "page_size": page_size, "tag_list": []},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        voice_list = data.get("data", {}).get("voice_list", [])
        return [
            {
                "voice_id": str(v.get("voice_id", "")),
                "name": v.get("voice_name", ""),
                "tags": v.get("tag_list", []),
                "sample_audio": v.get("sample_audio", ""),
                "provider": "minimax",
            }
            for v in voice_list
        ]
    except Exception as e:
        return [{"error": str(e)}]


def listar_vozes_clonadas(api_key: str) -> list:
    """Lista vozes clonadas do Minimax."""
    try:
        resp = httpx.get(
            f"{API_BASE}/v1m/voice/clone",
            headers={"xi-api-key": api_key},
            timeout=15.0,
        )
        resp.raise_for_status()
        data = resp.json()
        clones = data.get("data", [])
        return [
            {
                "voice_id": str(v.get("voice_id", "")),
                "name": v.get("voice_name", ""),
                "tags": v.get("tag_list", []),
                "sample_audio": v.get("sample_audio", ""),
                "provider": "minimax_clone",
            }
            for v in clones
        ]
    except Exception as e:
        return [{"error": str(e)}]


# === TTS ===

def gerar_narracao_elevenlabs(
    api_key: str, voice_id: str, texto: str, model_id: str = "eleven_multilingual_v2"
) -> dict:
    """Inicia geração TTS via ElevenLabs. Retorna task_id."""
    try:
        resp = httpx.post(
            f"{API_BASE}/v1/text-to-speech/{voice_id}?output_format=mp3_44100_128",
            headers=_headers(api_key),
            json={
                "text": texto,
                "model_id": model_id,
                "with_transcript": True,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "ok": True,
            "task_id": data.get("task_id", ""),
            "credits": data.get("ec_remain_credits", 0),
        }
    except Exception as e:
        return {"ok": False, "erro": str(e)}


def gerar_narracao_minimax(
    api_key: str, voice_id: str, texto: str,
    model: str = "speech-2.6-hd", speed: float = 1.0, pitch: int = 0
) -> dict:
    """Inicia geração TTS via Minimax. Retorna task_id."""
    try:
        resp = httpx.post(
            f"{API_BASE}/v1m/task/text-to-speech",
            headers=_headers(api_key),
            json={
                "text": texto,
                "model": model,
                "voice_setting": {
                    "voice_id": voice_id,
                    "vol": 1,
                    "pitch": pitch,
                    "speed": speed,
                },
                "language_boost": "Auto",
                "with_transcript": True,
            },
            timeout=30.0,
        )
        resp.raise_for_status()
        data = resp.json()
        return {
            "ok": True,
            "task_id": data.get("task_id", ""),
            "credits": data.get("ec_remain_credits", 0),
        }
    except Exception as e:
        return {"ok": False, "erro": str(e)}


# === TASK POLLING ===

def consultar_tarefa(api_key: str, task_id: str) -> dict:
    """Consulta status de uma tarefa."""
    try:
        resp = httpx.get(
            f"{API_BASE}/v1/task/{task_id}",
            headers=_headers(api_key),
            timeout=15.0,
        )
        resp.raise_for_status()
        return resp.json()
    except Exception as e:
        return {"status": "error", "error_message": str(e)}


def baixar_audio(url: str, destino: str) -> str:
    """Baixa arquivo de áudio de uma URL para destino local."""
    try:
        resp = httpx.get(url, timeout=120.0, follow_redirects=True)
        resp.raise_for_status()
        Path(destino).parent.mkdir(parents=True, exist_ok=True)
        with open(destino, "wb") as f:
            f.write(resp.content)
        return destino
    except Exception as e:
        raise RuntimeError(f"Falha ao baixar áudio: {e}")


# === ESTADO DE NARRAÇÃO ===

ultimo_creditos = None

estado_narracao = {
    "ativo": False,
    "task_id": None,
    "provider": None,
    "status": "idle",
    "progresso": 0,
    "audio_url": None,
    "audio_local": None,
    "srt_url": None,
    "erro": None,
}


MINIMAX_CHUNK_LIMIT = 8000  # chars por chunk (margem do limite de 10k)


def _dividir_em_chunks(texto: str, max_chars: int) -> list:
    """Divide texto em chunks respeitando parágrafos."""
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

    # Se algum chunk ainda é grande demais, dividir por frases
    final_chunks = []
    for chunk in chunks:
        if len(chunk) <= max_chars:
            final_chunks.append(chunk)
        else:
            # Dividir por frases (. seguido de espaço ou newline)
            import re
            frases = re.split(r'(?<=[.!?])\s+', chunk)
            sub_chunk = ""
            for frase in frases:
                if len(sub_chunk) + len(frase) + 1 > max_chars and sub_chunk:
                    final_chunks.append(sub_chunk.strip())
                    sub_chunk = frase
                else:
                    sub_chunk += (" " if sub_chunk else "") + frase
            if sub_chunk.strip():
                final_chunks.append(sub_chunk.strip())

    return final_chunks if final_chunks else [texto]


def iniciar_narracao(api_key: str, provider: str, voice_id: str, texto: str, nome_saida: str, pasta: str = "", preview: bool = False, **kwargs) -> dict:
    """Inicia geração de narração. Suporta chunking para textos longos."""
    global estado_narracao

    if estado_narracao.get("ativo"):
        return {"ok": False, "erro": "Já existe uma narração em andamento. Aguarde."}

    # Determinar pasta de saída
    output_dir = Path(pasta) if pasta else NARRACOES_DIR
    if not output_dir.is_absolute():
        output_dir = BASE_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    # Chunking para Minimax com textos longos
    needs_chunking = provider in ("minimax", "minimax_clone") and len(texto) > MINIMAX_CHUNK_LIMIT

    if needs_chunking:
        chunks = _dividir_em_chunks(texto, MINIMAX_CHUNK_LIMIT)
        # Enviar todos os chunks como tasks separadas
        task_ids = []
        total_credits = 0
        for i, chunk in enumerate(chunks):
            result = gerar_narracao_minimax(
                api_key, voice_id, chunk,
                model=kwargs.get("model", "speech-2.6-hd"),
                speed=kwargs.get("speed", 1.0),
                pitch=kwargs.get("pitch", 0),
            )
            if not result.get("ok"):
                return {"ok": False, "erro": f"Chunk {i+1}/{len(chunks)}: {result.get('erro', '')}"}
            task_ids.append(result["task_id"])
            total_credits = result.get("credits", 0)

        global ultimo_creditos
        ultimo_creditos = total_credits

        estado_narracao = {
            "ativo": True,
            "task_id": task_ids[0],  # primeiro para compatibilidade
            "task_ids": task_ids,
            "chunks_total": len(chunks),
            "chunks_done": 0,
            "provider": provider,
            "status": "processing",
            "progresso": 5,
            "audio_url": None,
            "audio_local": None,
            "srt_url": None,
            "erro": None,
            "nome_saida": nome_saida,
            "output_dir": str(output_dir),
            "preview": preview,
            "api_key": api_key,
            "credit_cost": 0,
        }
        return {"ok": True, "task_id": task_ids[0], "credits": total_credits, "chunks": len(chunks)}

    else:
        # Single request (sem chunking)
        if provider in ("minimax", "minimax_clone"):
            result = gerar_narracao_minimax(
                api_key, voice_id, texto,
                model=kwargs.get("model", "speech-2.6-hd"),
                speed=kwargs.get("speed", 1.0),
                pitch=kwargs.get("pitch", 0),
            )
        else:
            result = gerar_narracao_elevenlabs(
                api_key, voice_id, texto,
                model_id=kwargs.get("model_id", "eleven_multilingual_v2"),
            )

        if not result.get("ok"):
            return result

        ultimo_creditos = result.get("credits")

        estado_narracao = {
            "ativo": True,
            "task_id": result["task_id"],
            "task_ids": None,
            "chunks_total": 1,
            "chunks_done": 0,
            "provider": provider,
            "status": "processing",
            "progresso": 10,
            "audio_url": None,
            "audio_local": None,
            "srt_url": None,
            "erro": None,
            "nome_saida": nome_saida,
            "output_dir": str(output_dir),
            "preview": preview,
            "api_key": api_key,
            "credit_cost": 0,
        }
        return result


def _concatenar_audios(paths: list, output: str):
    """Concatena múltiplos MP3 com FFmpeg."""
    import subprocess
    output_dir = Path(output).parent
    list_file = output_dir / "concat_list.txt"
    # Usar nomes relativos para evitar problemas com caracteres especiais no path
    with open(list_file, "w", encoding="utf-8") as f:
        for p in paths:
            nome = Path(p).name
            f.write(f"file '{nome}'\n")
    cmd = ["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(list_file), "-c", "copy", output]
    proc = subprocess.run(cmd, capture_output=True, timeout=120, cwd=str(output_dir))
    list_file.unlink(missing_ok=True)
    if proc.returncode != 0:
        raise RuntimeError(f"FFmpeg concat falhou: {proc.stderr.decode('utf-8', errors='replace')[:200]}")


def poll_narracao() -> dict:
    """Verifica status da narração em andamento."""
    global estado_narracao

    if not estado_narracao["ativo"] or not estado_narracao["task_id"]:
        safe = {k: v for k, v in estado_narracao.items() if k != "api_key"}
        return safe

    task_ids = estado_narracao.get("task_ids")
    api_key = estado_narracao["api_key"]
    nome = estado_narracao.get("nome_saida", "narracao")
    output_dir = Path(estado_narracao.get("output_dir", str(NARRACOES_DIR)))
    is_preview = estado_narracao.get("preview", False)

    if task_ids and len(task_ids) > 1:
        # === MODO CHUNKED: múltiplas tasks ===
        all_done = True
        any_error = False
        total_cost = 0
        audio_urls = []

        for i, tid in enumerate(task_ids):
            task = consultar_tarefa(api_key, tid)
            st = task.get("status", "doing")
            if st == "done":
                metadata = task.get("metadata", {})
                audio_urls.append((i, metadata.get("audio_url", "")))
                total_cost += task.get("credit_cost", 0)
            elif st == "error":
                any_error = True
                estado_narracao["erro"] = f"Chunk {i+1}: {task.get('error_message', 'erro')}"
                break
            else:
                all_done = False

        chunks_done = len(audio_urls)
        chunks_total = len(task_ids)
        estado_narracao["chunks_done"] = chunks_done
        estado_narracao["progresso"] = int(chunks_done / chunks_total * 90) if chunks_total else 0
        estado_narracao["status"] = "processing"

        if any_error:
            estado_narracao["status"] = "error"
            estado_narracao["ativo"] = False

        elif all_done:
            # Baixar todos os áudios e concatenar
            estado_narracao["progresso"] = 92
            audio_urls.sort(key=lambda x: x[0])

            if not is_preview:
                try:
                    chunk_paths = []
                    for i, url in audio_urls:
                        chunk_path = str(output_dir / f"_chunk_{nome}_{i}.mp3")
                        baixar_audio(url, chunk_path)
                        chunk_paths.append(chunk_path)

                    # Concatenar
                    destino = _output_path_com_data(output_dir, nome)
                    _concatenar_audios(chunk_paths, destino)

                    # Limpar chunks temporários
                    for cp in chunk_paths:
                        Path(cp).unlink(missing_ok=True)

                    estado_narracao["audio_local"] = destino
                except Exception as e:
                    estado_narracao["erro"] = str(e)
            else:
                estado_narracao["audio_local"] = "(preview - não salvo)"

            estado_narracao["audio_url"] = audio_urls[-1][1] if audio_urls else ""
            estado_narracao["credit_cost"] = total_cost
            estado_narracao["status"] = "done"
            estado_narracao["progresso"] = 100
            estado_narracao["ativo"] = False

    else:
        # === MODO SINGLE: uma task ===
        task = consultar_tarefa(api_key, estado_narracao["task_id"])
        status = task.get("status", "doing")

        if status == "done":
            metadata = task.get("metadata", {})
            audio_url = metadata.get("audio_url", "")
            srt_url = metadata.get("srt_url", "")

            if not is_preview:
                destino = str(output_dir / f"{nome}.mp3")
                try:
                    baixar_audio(audio_url, destino)
                    estado_narracao["audio_local"] = destino
                except Exception as e:
                    estado_narracao["erro"] = str(e)
            else:
                estado_narracao["audio_local"] = "(preview - não salvo)"

            estado_narracao["audio_url"] = audio_url
            estado_narracao["srt_url"] = srt_url
            estado_narracao["credit_cost"] = task.get("credit_cost", 0)
            estado_narracao["status"] = "done"
            estado_narracao["progresso"] = 100
            estado_narracao["ativo"] = False

        elif status == "error":
            estado_narracao["status"] = "error"
            estado_narracao["erro"] = task.get("error_message", "Erro desconhecido")
            estado_narracao["ativo"] = False

        else:
            estado_narracao["status"] = "processing"
            progress = task.get("progress", 0)
            estado_narracao["progresso"] = max(10, progress) if progress else 30

    # Limpar api_key do estado antes de retornar
    safe = {k: v for k, v in estado_narracao.items() if k != "api_key"}
    return safe
