"""
Motor de narração TTS via ai33.pro (ElevenLabs + Minimax).
Suporta vozes de banco, vozes clonadas, e polling de tarefas assíncronas.
"""

import json
import time
from pathlib import Path

import httpx

# Cliente httpx compartilhado com connection pooling (evita esgotamento de portas)
_http_client = httpx.Client(
    timeout=httpx.Timeout(300.0, connect=15.0),
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
)

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
        resp = _http_client.get(
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
        resp = _http_client.get(
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
        resp = _http_client.post(
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
        resp = _http_client.get(
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
        resp = _http_client.post(
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
        resp = _http_client.post(
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
    """Consulta status de uma tarefa. Retry em 502/503/timeout."""
    for attempt in range(5):
        try:
            resp = _http_client.get(
                f"{API_BASE}/v1/task/{task_id}",
                headers=_headers(api_key),
                timeout=15.0,
            )
            if resp.status_code in (502, 503, 429):
                time.sleep(5 * (attempt + 1))
                continue
            resp.raise_for_status()
            return resp.json()
        except Exception as e:
            if attempt < 4:
                time.sleep(5 * (attempt + 1))
                continue
            return {"status": "error", "error_message": str(e)}


def baixar_audio(url: str, destino: str) -> str:
    """Baixa arquivo de audio de uma URL para destino local. Retry 3x com timeout generoso."""
    import time as _time
    Path(destino).parent.mkdir(parents=True, exist_ok=True)
    for attempt in range(3):
        try:
            resp = _http_client.get(url, timeout=300.0, follow_redirects=True)
            resp.raise_for_status()
            with open(destino, "wb") as f:
                f.write(resp.content)
            return destino
        except Exception as e:
            if attempt < 2:
                _time.sleep(5 * (attempt + 1))
                continue
            raise RuntimeError(f"Falha ao baixar audio apos 3 tentativas: {e}")


# === ESTADOS DE NARRACAO (separados: auto vs manual) ===

ultimo_creditos = None

_estado_base = {
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

# Estado para modo manual (aba Producao/Narracao)
estado_narracao = dict(_estado_base)

# Estado para modo automatico (orchestrator/Produzir Tudo)
estado_narracao_auto = dict(_estado_base)


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


PENDING_TASKS_FILE = BASE_DIR / "narracoes" / "_pending_tasks.json"


def _salvar_pending_tasks(nome: str, task_ids: list, api_key: str):
    """Salva task_ids em disco para recuperacao em caso de falha no download."""
    pending = {}
    if PENDING_TASKS_FILE.exists():
        try:
            with open(PENDING_TASKS_FILE, "r", encoding="utf-8") as f:
                pending = json.load(f)
        except Exception:
            pass
    pending[nome] = {"task_ids": task_ids, "api_key": api_key, "ts": time.time()}
    with open(PENDING_TASKS_FILE, "w", encoding="utf-8") as f:
        json.dump(pending, f, ensure_ascii=False)


def _carregar_pending_tasks(nome: str) -> dict:
    """Carrega task_ids pendentes de uma narracao anterior que falhou."""
    if not PENDING_TASKS_FILE.exists():
        return None
    try:
        with open(PENDING_TASKS_FILE, "r", encoding="utf-8") as f:
            pending = json.load(f)
        entry = pending.get(nome)
        if entry and time.time() - entry.get("ts", 0) < 3600:  # valido por 1 hora
            return entry
    except Exception:
        pass
    return None


def _limpar_pending_tasks(nome: str):
    """Remove task_ids pendentes apos sucesso."""
    if not PENDING_TASKS_FILE.exists():
        return
    try:
        with open(PENDING_TASKS_FILE, "r", encoding="utf-8") as f:
            pending = json.load(f)
        pending.pop(nome, None)
        with open(PENDING_TASKS_FILE, "w", encoding="utf-8") as f:
            json.dump(pending, f, ensure_ascii=False)
    except Exception:
        pass


def _get_estado(modo: str = "manual"):
    """Retorna o estado correto baseado no modo."""
    return estado_narracao_auto if modo == "auto" else estado_narracao


def narrar_chunked_sequencial(api_key: str, provider: str, voice_id: str, texto: str, nome_saida: str, pasta: str = "", modo: str = "manual", **kwargs) -> dict:
    """Narra texto longo chunk por chunk: gera 1 -> espera -> baixa -> proximo.
    Retorna {"ok": bool, "audio_local": str, "erro": str, "chunks": int}.
    """
    global ultimo_creditos
    estado = _get_estado(modo)

    output_dir = Path(pasta) if pasta else NARRACOES_DIR
    if not output_dir.is_absolute():
        output_dir = BASE_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    destino = _output_path_com_data(output_dir, nome_saida)
    destino_dir = Path(destino).parent
    destino_dir.mkdir(parents=True, exist_ok=True)

    chunks = _dividir_em_chunks(texto, MINIMAX_CHUNK_LIMIT)
    total_chunks = len(chunks)
    chunk_paths = []
    total_cost = 0

    estado.update({
        "ativo": True, "task_id": None, "task_ids": None,
        "chunks_total": total_chunks, "chunks_done": 0,
        "provider": provider, "status": "processing", "progresso": 0,
        "audio_url": None, "audio_local": None, "srt_url": None, "erro": None,
        "nome_saida": nome_saida, "output_dir": str(output_dir),
        "preview": False, "api_key": api_key, "credit_cost": 0,
    })

    try:
        for i, chunk_text in enumerate(chunks):
            if estado.get("erro"):
                break

            estado["status"] = "processing"
            estado["progresso"] = int(i / total_chunks * 90)

            # Verificar se chunk ja existe no disco (resume apos crash/restart)
            chunk_path = str(destino_dir / f"_chunk_{nome_saida}_{i}.mp3")
            if Path(chunk_path).exists() and Path(chunk_path).stat().st_size > 1000:
                print(f"[NARRACAO-SEQ] {nome_saida}: Chunk {i+1}/{total_chunks} - JA EXISTE ({Path(chunk_path).stat().st_size} bytes), pulando")
                chunk_paths.append(chunk_path)
                estado["chunks_done"] = i + 1
                continue

            print(f"[NARRACAO-SEQ] {nome_saida}: Chunk {i+1}/{total_chunks} - gerando...")
            result = gerar_narracao_minimax(
                api_key, voice_id, chunk_text,
                model=kwargs.get("model", "speech-2.6-hd"),
                speed=kwargs.get("speed", 1.0),
                pitch=kwargs.get("pitch", 0),
            )
            if not result.get("ok"):
                raise RuntimeError(f"Chunk {i+1}/{total_chunks}: {result.get('erro', '')}")

            task_id = result["task_id"]
            estado["task_id"] = task_id
            ultimo_creditos = result.get("credits", 0)

            deadline = time.time() + 600
            audio_url = None
            while time.time() < deadline:
                task = consultar_tarefa(api_key, task_id)
                st = task.get("status", "doing")
                if st == "done":
                    metadata = task.get("metadata", {})
                    audio_url = metadata.get("audio_url", "")
                    total_cost += task.get("credit_cost", 0)
                    break
                elif st == "error":
                    raise RuntimeError(f"Chunk {i+1}: {task.get('error_message', 'erro')}")
                time.sleep(3)
            else:
                raise RuntimeError(f"Chunk {i+1}/{total_chunks}: timeout (10min)")

            if not audio_url:
                raise RuntimeError(f"Chunk {i+1}/{total_chunks}: audio_url vazio (API retornou done sem arquivo)")

            print(f"[NARRACAO-SEQ] {nome_saida}: Chunk {i+1}/{total_chunks} - baixando...")
            baixar_audio(audio_url, chunk_path)
            chunk_paths.append(chunk_path)

            estado["chunks_done"] = i + 1
            print(f"[NARRACAO-SEQ] {nome_saida}: Chunk {i+1}/{total_chunks} - OK")

        if len(chunk_paths) == total_chunks:
            estado["progresso"] = 95
            print(f"[NARRACAO-SEQ] {nome_saida}: Concatenando {total_chunks} chunks...")
            _concatenar_audios(chunk_paths, destino)

            for cp in chunk_paths:
                Path(cp).unlink(missing_ok=True)

            estado["audio_local"] = destino
            estado["status"] = "done"
            estado["progresso"] = 100
            estado["credit_cost"] = total_cost
            estado["ativo"] = False
            _limpar_pending_tasks(nome_saida)
            print(f"[NARRACAO-SEQ] {nome_saida}: Concluido -> {destino}")
            return {"ok": True, "audio_local": destino, "erro": "", "chunks": total_chunks}
        else:
            raise RuntimeError(f"Apenas {len(chunk_paths)}/{total_chunks} chunks baixados")

    except Exception as e:
        estado["status"] = "error"
        estado["erro"] = str(e)
        estado["ativo"] = False
        # NAO deletar chunks — permite resume na proxima tentativa
        print(f"[NARRACAO-SEQ] {nome_saida}: Erro ({e}). {len(chunk_paths)} chunks preservados para resume.")
        return {"ok": False, "audio_local": "", "erro": str(e), "chunks": total_chunks}


def iniciar_narracao(api_key: str, provider: str, voice_id: str, texto: str, nome_saida: str, pasta: str = "", preview: bool = False, modo: str = "manual", **kwargs) -> dict:
    """Inicia geracao de narracao. modo='auto' usa estado separado do manual."""
    estado = _get_estado(modo)

    if estado.get("ativo"):
        return {"ok": False, "erro": "Ja existe uma narracao em andamento. Aguarde."}

    output_dir = Path(pasta) if pasta else NARRACOES_DIR
    if not output_dir.is_absolute():
        output_dir = BASE_DIR / output_dir
    output_dir.mkdir(parents=True, exist_ok=True)

    needs_chunking = provider in ("minimax", "minimax_clone") and len(texto) > MINIMAX_CHUNK_LIMIT

    if needs_chunking:
        result = narrar_chunked_sequencial(api_key, provider, voice_id, texto, nome_saida, pasta, modo=modo, **kwargs)
        return result

    else:
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

        estado.update({
            "ativo": True, "task_id": result["task_id"], "task_ids": None,
            "chunks_total": 1, "chunks_done": 0, "provider": provider,
            "status": "processing", "progresso": 10,
            "audio_url": None, "audio_local": None, "srt_url": None, "erro": None,
            "nome_saida": nome_saida, "output_dir": str(output_dir),
            "preview": preview, "api_key": api_key, "credit_cost": 0,
        })
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


def poll_narracao(modo: str = "manual") -> dict:
    """Verifica status da narracao em andamento."""
    estado = _get_estado(modo)

    if not estado.get("ativo") or not estado.get("task_id"):
        safe = {k: v for k, v in estado.items() if k != "api_key"}
        return safe

    task_ids = estado.get("task_ids")
    api_key = estado.get("api_key", "")
    nome = estado.get("nome_saida", "narracao")
    output_dir = Path(estado.get("output_dir", str(NARRACOES_DIR)))
    is_preview = estado.get("preview", False)

    if task_ids and len(task_ids) > 1:
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
                estado["erro"] = f"Chunk {i+1}: {task.get('error_message', 'erro')}"
                break
            else:
                all_done = False

        chunks_done = len(audio_urls)
        chunks_total = len(task_ids)
        estado["chunks_done"] = chunks_done
        estado["progresso"] = int(chunks_done / chunks_total * 90) if chunks_total else 0
        estado["status"] = "processing"

        if any_error:
            estado["status"] = "error"
            estado["ativo"] = False

        elif all_done:
            estado["progresso"] = 92
            audio_urls.sort(key=lambda x: x[0])

            if not is_preview:
                try:
                    destino = _output_path_com_data(output_dir, nome)
                    destino_dir = Path(destino).parent
                    destino_dir.mkdir(parents=True, exist_ok=True)

                    chunk_paths = []
                    for i, url in audio_urls:
                        chunk_path = str(destino_dir / f"_chunk_{nome}_{i}.mp3")
                        baixar_audio(url, chunk_path)
                        chunk_paths.append(chunk_path)

                    _concatenar_audios(chunk_paths, destino)

                    for cp in chunk_paths:
                        Path(cp).unlink(missing_ok=True)

                    estado["audio_local"] = destino
                    _limpar_pending_tasks(nome)
                except Exception as e:
                    estado["erro"] = str(e)
            else:
                estado["audio_local"] = "(preview - nao salvo)"
                _limpar_pending_tasks(nome)

            estado["audio_url"] = audio_urls[-1][1] if audio_urls else ""
            estado["credit_cost"] = total_cost
            estado["status"] = "done"
            estado["progresso"] = 100
            estado["ativo"] = False

    else:
        task = consultar_tarefa(api_key, estado["task_id"])
        status = task.get("status", "doing")

        if status == "done":
            metadata = task.get("metadata", {})
            audio_url = metadata.get("audio_url", "")
            srt_url = metadata.get("srt_url", "")

            if not is_preview:
                destino = _output_path_com_data(output_dir, nome)
                try:
                    baixar_audio(audio_url, destino)
                    estado["audio_local"] = destino
                except Exception as e:
                    estado["erro"] = str(e)
            else:
                estado["audio_local"] = "(preview - nao salvo)"

            estado["audio_url"] = audio_url
            estado["srt_url"] = srt_url
            estado["credit_cost"] = task.get("credit_cost", 0)
            estado["status"] = "done"
            estado["progresso"] = 100
            estado["ativo"] = False

        elif status == "error":
            estado["status"] = "error"
            estado["erro"] = task.get("error_message", "Erro desconhecido")
            estado["ativo"] = False

        else:
            estado["status"] = "processing"
            progress = task.get("progress", 0)
            estado["progresso"] = max(10, progress) if progress else 30

    safe = {k: v for k, v in estado.items() if k != "api_key"}
    return safe
