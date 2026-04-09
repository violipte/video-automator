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


def iniciar_narracao(api_key: str, provider: str, voice_id: str, texto: str, nome_saida: str, pasta: str = "", preview: bool = False, **kwargs) -> dict:
    """Inicia geração de narração e retorna task_id."""
    global estado_narracao

    # Prevenir duplicação — não iniciar se já tem uma ativa
    if estado_narracao.get("ativo"):
        return {"ok": False, "erro": "Já existe uma narração em andamento. Aguarde."}

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

    global ultimo_creditos
    ultimo_creditos = result.get("credits")

    # Determinar pasta de saída
    output_dir = Path(pasta) if pasta else NARRACOES_DIR
    if not output_dir.is_absolute():
        output_dir = BASE_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    estado_narracao = {
        "ativo": True,
        "task_id": result["task_id"],
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
    }

    return result


def poll_narracao() -> dict:
    """Verifica status da narração em andamento."""
    global estado_narracao

    if not estado_narracao["ativo"] or not estado_narracao["task_id"]:
        return estado_narracao

    task = consultar_tarefa(estado_narracao["api_key"], estado_narracao["task_id"])
    status = task.get("status", "doing")

    if status == "done":
        metadata = task.get("metadata", {})
        audio_url = metadata.get("audio_url", "")
        srt_url = metadata.get("srt_url", "")

        # Baixar áudio (se não for preview)
        nome = estado_narracao.get("nome_saida", "narracao")
        output_dir = Path(estado_narracao.get("output_dir", str(NARRACOES_DIR)))
        is_preview = estado_narracao.get("preview", False)

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
