"""
Orquestrador de producao completa.
Roda no backend como thread. Pipeline continua:
  FASE 1: Roteiros em paralelo (3 workers)
  FASE 2+3: Narracao sequencial alimenta fila de render.
            Render comeca assim que 1 canal tem MP3 pronto.

RESILIENCIA:
- Auto-resume apos restart do servidor
- Timeouts por etapa (roteiro 10min, narracao 40min, video 90min)
- Thread tracking + health monitor externo
- Skip de canais ja concluidos/erro ao retomar
"""

import json
import time
import threading
import traceback
import subprocess as sp
from concurrent.futures import ThreadPoolExecutor, as_completed
from pathlib import Path
from datetime import datetime

import production_log
import scriptwriter
import narrator
import render_queue
import transcriber
import subtitle_fixer
from engine import VideoEngine

BASE_DIR = Path(__file__).parent
TEMAS_FILE = BASE_DIR / "temas.json"
TEMPLATES_FILE = BASE_DIR / "templates.json"
NARRACOES_DIR = BASE_DIR / "narracoes"
TEMP_DIR = BASE_DIR / "temp"

# === TIMEOUTS POR ETAPA (segundos) ===
TIMEOUT_ROTEIRO = 10 * 60    # 10 minutos
TIMEOUT_NARRACAO = 40 * 60   # 40 minutos
TIMEOUT_VIDEO = 90 * 60      # 90 minutos

# === LOCKS ===
_temas_lock = threading.Lock()


def _carregar_temas():
    with _temas_lock:
        if TEMAS_FILE.exists():
            with open(TEMAS_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        return {"colunas": [], "linhas": [], "celulas": {}}


def _salvar_temas(data):
    with _temas_lock:
        with open(TEMAS_FILE, "w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=2)


def _carregar_templates():
    if TEMPLATES_FILE.exists():
        with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {}


def _obter_config():
    return scriptwriter.carregar_config()


# === ESTADO GLOBAL ===
estado = {
    "ativo": False,
    "cancelado": False,
}

_thread_producao = None


def _render_com_timeout(engine, srt_path, timeout_s):
    """Roda engine.montar() em thread separada com timeout."""
    result = {"ok": False, "erro": ""}

    def _run():
        try:
            engine.montar(srt_path=srt_path)
            result["ok"] = True
        except Exception as e:
            result["erro"] = str(e)

    t = threading.Thread(target=_run, daemon=True)
    t.start()
    t.join(timeout=timeout_s)

    if t.is_alive():
        for _ in range(3):
            try:
                sp.run(["taskkill", "/F", "/IM", "ffmpeg.exe"], capture_output=True, timeout=10)
            except Exception:
                pass
            time.sleep(1)
        result["ok"] = False
        result["erro"] = f"Video render timeout ({timeout_s // 60}min)"

    return result["ok"], result["erro"]


# === FASE 1: GERAR ROTEIRO PARA 1 CANAL (thread-safe) ===

def _gerar_roteiro_para_canal(job_index, job, cel, data_ref):
    """Gera roteiro usando executar_pipeline_isolado. Thread-safe."""
    tag = job["tag"]
    key = job["key"]
    tmpl = job["template"]
    pipeline_id = job["pipeline_id"]
    min_chars = tmpl.get("min_roteiro_chars", 22000)
    max_retries = 2

    if not pipeline_id:
        production_log.atualizar_canal(job_index, etapa="erro", erro="Sem pipeline configurada")
        production_log.adicionar_log(f"{tag}: ERRO - sem pipeline")
        return (job_index, None, "Sem pipeline configurada")

    production_log.atualizar_canal(job_index, etapa="roteiro", etapa_detalhe="Gerando...")
    production_log.adicionar_log(f"{tag}: Gerando roteiro...")

    contexto = {
        "tema": cel.get("tema", ""),
        "canal": tag,
        "data": data_ref,
        "titulo": cel.get("titulo", ""),
        "thumb": cel.get("thumb", ""),
    }

    resultado = ""
    for attempt in range(max_retries + 1):
        if estado["cancelado"]:
            return (job_index, None, "Cancelado")

        res = scriptwriter.executar_pipeline_isolado(pipeline_id, cel.get("tema", ""), contexto_extra=contexto)

        if res["ok"] and res["resultado"]:
            resultado = res["resultado"]
            if len(resultado) >= min_chars:
                break
            if attempt < max_retries:
                production_log.adicionar_log(f"{tag}: Roteiro CURTO ({len(resultado)} chars < {min_chars}) - retry {attempt + 1}/{max_retries}")
                production_log.atualizar_canal(job_index, etapa_detalhe=f"Roteiro curto ({len(resultado)}ch), retry {attempt + 1}...")
        else:
            erros = "; ".join(e.get("erro", "") for e in res.get("etapas", []) if e.get("status") == "erro" and e.get("erro"))
            if attempt >= max_retries:
                production_log.atualizar_canal(job_index, etapa="erro", erro=f"Roteiro falhou: {erros}")
                production_log.adicionar_log(f"{tag}: ERRO roteiro - {erros}")
                return (job_index, None, erros)

    if resultado and len(resultado) < min_chars:
        production_log.atualizar_canal(job_index, etapa="erro", erro=f"Roteiro muito curto apos {max_retries} tentativas ({len(resultado)} chars)")
        production_log.adicionar_log(f"{tag}: ERRO - roteiro curto apos {max_retries} tentativas ({len(resultado)} chars)")
        return (job_index, None, "Roteiro curto")

    if resultado and len(resultado) > 100:
        temas_data = _carregar_temas()
        if key not in temas_data.get("celulas", {}):
            temas_data.setdefault("celulas", {})[key] = {}
        temas_data["celulas"][key]["roteiro"] = resultado
        _salvar_temas(temas_data)

        production_log.atualizar_canal(job_index, etapa_detalhe=f"OK ({len(resultado)} chars)", roteiro_chars=len(resultado))
        production_log.adicionar_log(f"{tag}: Roteiro OK ({len(resultado)} chars)")
        return (job_index, resultado, None)

    return (job_index, None, "Roteiro vazio")


# === RENDER DE 1 CANAL ===

def _renderizar_canal(i, job, narr_path, data_formatada, data_ymd, data_pasta):
    """Renderiza video de 1 canal. Retorna True se sucesso."""
    tag = job["tag"]
    key = job["key"]
    tmpl = job["template"]

    if not tmpl:
        production_log.atualizar_canal(i, etapa="erro", erro="Sem template de video")
        return False

    pasta_saida = tmpl.get("pasta_saida", str(TEMP_DIR))
    video_pasta = Path(pasta_saida) / data_pasta
    video_pasta.mkdir(parents=True, exist_ok=True)
    video_nome = f"{tmpl.get('tag', tag)}_{data_ymd}_01.mp4"
    video_path = video_pasta / video_nome

    if video_path.exists() and video_path.stat().st_size > 1000:
        production_log.atualizar_canal(i, etapa="concluido", etapa_detalhe=f"Video existe ({video_nome})", video_path=str(video_path), fim=time.time())
        production_log.adicionar_log(f"{tag}: Video existe ({video_nome})")
        return True

    production_log.atualizar_canal(i, etapa="video", etapa_detalhe="Transcrevendo...", inicio=time.time())
    production_log.adicionar_log(f"{tag}: Produzindo video...")

    max_video_retries = 2
    for video_attempt in range(max_video_retries + 1):
        try:
            mp3_path = str(narr_path)
            srt_path = transcriber.transcrever(mp3_path, tmpl.get("idioma"))

            lc = tmpl.get("legenda_config", {})
            maiuscula = lc.get("maiuscula", tmpl.get("estilo_legenda") == 2)
            srt_corrigido = subtitle_fixer.corrigir_srt(
                srt_path, tmpl.get("idioma", "en"), job["template_id"], maiuscula,
                max_linhas=lc.get("max_linhas", 2),
                max_chars=lc.get("max_chars", 30),
                regras_template=tmpl.get("regras")
            )

            production_log.atualizar_canal(i, etapa_detalhe=f"Renderizando...{' (retry ' + str(video_attempt) + ')' if video_attempt > 0 else ''}", progresso=30)

            engine = VideoEngine(tmpl, mp3_path, str(video_path))
            render_ok, render_erro = _render_com_timeout(engine, srt_corrigido, TIMEOUT_VIDEO)

            if not render_ok:
                raise RuntimeError(render_erro)

            if video_path.exists() and video_path.stat().st_size > 1000:
                production_log.atualizar_canal(i, etapa="concluido", etapa_detalhe=f"OK ({video_nome})", video_path=str(video_path), progresso=100, fim=time.time())
                production_log.adicionar_log(f"{tag}: Video OK -> {video_nome}")

                temas_data = _carregar_temas()
                if key in temas_data.get("celulas", {}):
                    temas_data["celulas"][key]["done"] = True
                    temas_data["celulas"][key]["done_type"] = "auto"
                    _salvar_temas(temas_data)
                return True
            else:
                raise RuntimeError("Video nao gerado ou vazio")

        except Exception as e:
            if video_path.exists():
                video_path.unlink(missing_ok=True)
            if video_attempt < max_video_retries:
                production_log.adicionar_log(f"{tag}: ERRO video (tentativa {video_attempt + 1}) - {e}. Retentando em 10s...")
                production_log.atualizar_canal(i, etapa_detalhe=f"Erro, retry {video_attempt + 1}/{max_video_retries}...")
                time.sleep(10)
            else:
                production_log.atualizar_canal(i, etapa="erro", erro=str(e))
                production_log.adicionar_log(f"{tag}: ERRO video apos {max_video_retries + 1} tentativas - {e}")
                traceback.print_exc()
                return False
    return False


# === PRODUCAO COMPLETA (PIPELINE CONTINUA) ===

def produzir_data_completa(data_idx: int, temas_data: dict = None, ordem_colunas: list = None):
    """Produz todos os canais de uma data. Pipeline continua:
    roteiros paralelos, depois narracao+render em pipeline (render comeca sem esperar todos narrarem).
    """
    global estado
    estado["ativo"] = True
    estado["cancelado"] = False

    if not temas_data:
        temas_data = _carregar_temas()

    templates = _carregar_templates()
    config = _obter_config()
    api_key = config.get("ai33_api_key", "")

    linhas = temas_data.get("linhas", [])
    colunas = temas_data.get("colunas", [])
    celulas = temas_data.get("celulas", {})

    if data_idx >= len(linhas):
        production_log.adicionar_log("ERRO: indice de data invalido")
        estado["ativo"] = False
        return

    row = linhas[data_idx]
    data_ref = row.get("data", "")

    parts = data_ref.split("/")
    if len(parts) == 3:
        dd, mm, yyyy = parts[0], parts[1], parts[2]
    else:
        dd = mm = "00"
        yyyy = "2026"
    data_formatada = f"{dd}-{mm}"
    data_ymd = f"{yyyy}{mm}{dd}"
    data_pasta = f"{yyyy}-{mm}-{dd}"

    # Montar lista de jobs
    jobs = []
    col_indices = ordem_colunas if ordem_colunas else list(range(len(colunas)))
    for ci in col_indices:
        if ci >= len(colunas):
            continue
        col = colunas[ci]
        key = f"{data_idx}_{ci}"
        cel = celulas.get(key, {})
        if not cel.get("tema"):
            continue

        template_id = col.get("template_id", "")
        tmpl = templates.get(template_id, {}) if template_id else {}
        voz = tmpl.get("narracao_voz", {})

        jobs.append({
            "ci": ci,
            "key": key,
            "tag": col.get("nome", f"COL{ci}"),
            "pipeline_id": cel.get("pipeline_id") or col.get("pipeline_id", ""),
            "template_id": template_id,
            "template": tmpl,
            "voice_id": voz.get("voice_id", "") or col.get("voice_id", ""),
            "voice_provider": voz.get("provider", "") or col.get("voice_provider", ""),
            "voice_speed": voz.get("speed", 1.0),
            "voice_pitch": voz.get("pitch", 0),
            "cel": cel,
        })

    # Verificar se estamos retomando
    existing_state = production_log.obter_estado()
    is_resume = (
        existing_state.get("ativo") and
        existing_state.get("data_ref") == data_ref and
        existing_state.get("data_idx") == data_idx
    )

    if not is_resume:
        production_log.iniciar(
            data_ref,
            [{"tag": j["tag"], "template": j.get("template", {}).get("nome", "")} for j in jobs],
            data_idx=data_idx,
            ordem_colunas=ordem_colunas
        )
    else:
        production_log.adicionar_log(f"RETOMANDO producao: {data_ref}")

    try:
        # ============================================================
        # FASE 1: ROTEIROS (paralelo, 3 workers)
        # ============================================================
        production_log.adicionar_log("=== FASE 1: Roteiros (paralelo) ===")

        roteiro_ok = {}  # job_index -> True

        roteiro_para_gerar = {}
        for i, job in enumerate(jobs):
            if estado["cancelado"]:
                break

            if is_resume:
                existing_canais = existing_state.get("canais", [])
                if i < len(existing_canais):
                    existing_etapa = existing_canais[i].get("etapa", "")
                    if existing_etapa in ("concluido", "erro", "pulado"):
                        production_log.adicionar_log(f"{job['tag']}: Pulando (estado anterior: {existing_etapa})")
                        if existing_etapa == "concluido":
                            roteiro_ok[i] = True
                        continue

            tag = job["tag"]
            key = job["key"]
            production_log.atualizar_canal(i, etapa="iniciando")

            temas_data = _carregar_temas()
            cel = temas_data.get("celulas", {}).get(key, {})
            job["cel"] = cel

            if cel.get("roteiro") and len(cel["roteiro"]) > 100:
                production_log.atualizar_canal(i, etapa="roteiro", etapa_detalhe=f"Existe ({len(cel['roteiro'])} chars)", roteiro_chars=len(cel["roteiro"]))
                production_log.adicionar_log(f"{tag}: Roteiro existe ({len(cel['roteiro'])} chars)")
                roteiro_ok[i] = True
            else:
                roteiro_para_gerar[i] = (job, cel)

        if roteiro_para_gerar and not estado["cancelado"]:
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="roteiro") as executor:
                futures = {}
                for i, (job, cel) in roteiro_para_gerar.items():
                    f = executor.submit(_gerar_roteiro_para_canal, i, job, cel, data_ref)
                    futures[f] = i

                for future in as_completed(futures):
                    idx = futures[future]
                    try:
                        job_idx, resultado, erro = future.result(timeout=TIMEOUT_ROTEIRO)
                        if resultado:
                            roteiro_ok[job_idx] = True
                            jobs[job_idx]["cel"]["roteiro"] = resultado
                    except Exception as e:
                        production_log.atualizar_canal(idx, etapa="erro", erro=str(e))
                        production_log.adicionar_log(f"{jobs[idx]['tag']}: ERRO roteiro - {e}")

        # ============================================================
        # FASE 2+3: NARRACAO + RENDER EM PIPELINE CONTINUA
        # Narracao sequencial alimenta fila de render.
        # Render consome da fila (1 por vez), comeca imediatamente.
        # ============================================================
        production_log.adicionar_log("=== FASE 2+3: Narracao -> Render (pipeline) ===")

        render_queue.iniciar_worker()
        _render_pendentes = []  # lista de Events pra esperar no final

        def _enfileirar_render(i, job, narr_path_val):
            """Enfileira render na fila compartilhada."""
            job_id = f"{job['tag']}_{data_ymd}"
            evt = threading.Event()
            _render_pendentes.append(evt)

            def _do_render():
                _renderizar_canal(i, job, narr_path_val, data_formatada, data_ymd, data_pasta)

            def _on_done():
                evt.set()

            def _on_error(erro):
                evt.set()

            render_queue.enfileirar(job_id, _do_render, fonte="auto", on_done=_on_done, on_error=_on_error)

        # --- PRIMEIRO: enfileirar render dos que JA TEM MP3 ---
        canais_sem_narracao = []
        for i, job in enumerate(jobs):
            if estado["cancelado"]:
                break
            if i not in roteiro_ok:
                continue

            # Skip resume
            if is_resume:
                existing_canais = existing_state.get("canais", [])
                if i < len(existing_canais):
                    existing_etapa = existing_canais[i].get("etapa", "")
                    if existing_etapa in ("concluido", "pulado"):
                        narr_nome = f"{job['tag']} {data_formatada}"
                        narr_subpasta = NARRACOES_DIR / data_pasta
                        narr_path = narr_subpasta / f"{narr_nome}.mp3"
                        if narr_path.exists():
                            _enfileirar_render(i, job, narr_path)
                        continue

            tag = job["tag"]
            narr_nome = f"{tag} {data_formatada}"
            narr_subpasta = NARRACOES_DIR / data_pasta
            narr_path = narr_subpasta / f"{narr_nome}.mp3"

            if narr_path.exists():
                production_log.atualizar_canal(i, etapa="narracao", etapa_detalhe=f"Existe ({narr_path.name})", narracao_path=str(narr_path))
                production_log.adicionar_log(f"{tag}: Narracao existe ({narr_path.name})")
                _enfileirar_render(i, job, narr_path)
            else:
                canais_sem_narracao.append((i, job))

        production_log.adicionar_log(f"Render: {len(_render_pendentes)} canais enfileirados | Narracao: {len(canais_sem_narracao)} canais pendentes")

        # --- DEPOIS: narrar os que faltam (sequencial) ---
        for i, job in canais_sem_narracao:
            if estado["cancelado"]:
                break

            tag = job["tag"]
            cel = job.get("cel") or {}
            if not cel.get("roteiro"):
                temas_data = _carregar_temas()
                cel = temas_data.get("celulas", {}).get(job["key"], {})

            narr_nome = f"{tag} {data_formatada}"
            narr_subpasta = NARRACOES_DIR / data_pasta
            narr_path = narr_subpasta / f"{narr_nome}.mp3"

            voice_id = job["voice_id"]
            if not voice_id:
                production_log.atualizar_canal(i, etapa="erro", erro="Sem voz configurada")
                production_log.adicionar_log(f"{tag}: ERRO - sem voz")
                continue

            if not api_key:
                production_log.atualizar_canal(i, etapa="erro", erro="Sem API key ai33.pro")
                continue

            production_log.atualizar_canal(i, etapa="narracao", etapa_detalhe="Gerando...", inicio=time.time())
            production_log.adicionar_log(f"{tag}: Gerando narracao ({len(cel.get('roteiro', ''))} chars)...")

            # Esperar narracao anterior terminar
            for _ in range(60):
                if not narrator.estado_narracao_auto.get("ativo"):
                    break
                time.sleep(2)

            try:
                result = narrator.iniciar_narracao(
                    api_key, job["voice_provider"], voice_id,
                    cel.get("roteiro", ""), narr_nome,
                    speed=job["voice_speed"], pitch=job["voice_pitch"],
                    modo="auto",
                )

                if not result.get("ok"):
                    production_log.atualizar_canal(i, etapa="erro", erro=result.get("erro", ""))
                    production_log.adicionar_log(f"{tag}: ERRO narracao - {result.get('erro', '')}")
                    continue

                # Chunking sequencial retorna audio_local direto (sem poll)
                if result.get("audio_local"):
                    narr_result_path = Path(result["audio_local"])
                    if narr_result_path.exists():
                        production_log.atualizar_canal(i, etapa_detalhe=f"OK ({narr_result_path.name})", narracao_path=str(narr_result_path))
                        production_log.adicionar_log(f"{tag}: Narracao OK -> {narr_result_path.name}")
                        _enfileirar_render(i, job, narr_result_path)
                        continue
                    else:
                        production_log.atualizar_canal(i, etapa="erro", erro="Audio chunked nao encontrado")
                        production_log.adicionar_log(f"{tag}: ERRO - audio chunked nao encontrado")
                        continue

                # Modo single (sem chunking): poll normal
                narr_ok = False
                poll_sem_progresso = 0
                narr_deadline = time.time() + TIMEOUT_NARRACAO

                while time.time() < narr_deadline:
                    st = narrator.poll_narracao(modo="auto")
                    if st.get("status") == "idle" and not st.get("ativo"):
                        poll_sem_progresso += 1
                        if poll_sem_progresso > 5:
                            expected = narr_subpasta / f"{narr_nome}.mp3"
                            if expected.exists():
                                narr_path = expected
                                narr_ok = True
                                production_log.adicionar_log(f"{tag}: Narracao recuperada de {expected.name}")
                                break
                            else:
                                production_log.atualizar_canal(i, etapa="erro", erro="Narracao perdida (estado idle)")
                                production_log.adicionar_log(f"{tag}: ERRO - narracao perdida")
                                break
                    if st.get("status") == "done":
                        narr_path_result = st.get("audio_local") or ""
                        if narr_path_result and Path(narr_path_result).exists():
                            production_log.atualizar_canal(i, etapa_detalhe=f"OK ({Path(narr_path_result).name})", narracao_path=narr_path_result)
                            production_log.adicionar_log(f"{tag}: Narracao OK -> {Path(narr_path_result).name}")
                            narr_path = Path(narr_path_result)
                            narr_ok = True
                        else:
                            expected = narr_subpasta / f"{narr_nome}.mp3"
                            if expected.exists():
                                narr_path = expected
                                narr_ok = True
                                production_log.adicionar_log(f"{tag}: Narracao encontrada em {expected.name}")
                            else:
                                production_log.atualizar_canal(i, etapa="erro", erro="Narracao done mas arquivo nao encontrado")
                                production_log.adicionar_log(f"{tag}: ERRO - arquivo nao encontrado apos narracao")
                        break
                    elif st.get("status") == "error":
                        production_log.atualizar_canal(i, etapa="erro", erro=st.get("erro", ""))
                        production_log.adicionar_log(f"{tag}: ERRO narracao - {st.get('erro', '')}")
                        break
                    time.sleep(3)
                else:
                    narrator.estado_narracao_auto["ativo"] = False
                    narrator.estado_narracao_auto["status"] = "idle"
                    production_log.atualizar_canal(i, etapa="erro", erro=f"Narracao timeout ({TIMEOUT_NARRACAO // 60}min)")
                    production_log.adicionar_log(f"{tag}: ERRO narracao - timeout")
                    continue

                if narr_ok:
                    _enfileirar_render(i, job, narr_path)
                else:
                    if not any(c.get("etapa") == "erro" for idx, c in enumerate(production_log.obter_estado().get("canais", [])) if idx == i):
                        production_log.atualizar_canal(i, etapa="erro", erro="MP3 nao encontrado apos geracao")
                    continue

            except Exception as e:
                narrator.estado_narracao_auto["ativo"] = False
                narrator.estado_narracao_auto["status"] = "idle"
                production_log.atualizar_canal(i, etapa="erro", erro=str(e))
                production_log.adicionar_log(f"{tag}: ERRO narracao - {e}")
                continue

        # Garantir narracao nao ficou travada
        narrator.estado_narracao_auto["ativo"] = False
        narrator.estado_narracao_auto["status"] = "idle"

        # Esperar todos os renders enfileirados terminarem
        production_log.adicionar_log(f"Aguardando {len(_render_pendentes)} renders na fila...")
        for evt in _render_pendentes:
            evt.wait(timeout=TIMEOUT_VIDEO)

    except Exception as e:
        production_log.adicionar_log(f"ERRO FATAL: {e}")
        traceback.print_exc()
    finally:
        production_log.finalizar(estado["cancelado"])
        estado["ativo"] = False


def iniciar_producao(data_idx: int, temas_data: dict = None, ordem_colunas: list = None):
    """Inicia producao em thread separada."""
    global _thread_producao
    if estado["ativo"]:
        return {"ok": False, "erro": "Producao ja em andamento"}
    _thread_producao = threading.Thread(
        target=produzir_data_completa, args=(data_idx, temas_data, ordem_colunas), daemon=True
    )
    _thread_producao.start()
    return {"ok": True}


def cancelar():
    estado["cancelado"] = True


def tentar_retomar():
    """Verifica production_state.json e retoma producao interrompida."""
    global _thread_producao

    if estado.get("ativo"):
        return False

    log_state = production_log.obter_estado()

    if not log_state.get("ativo"):
        return False

    data_idx = log_state.get("data_idx")
    ordem_colunas = log_state.get("ordem_colunas")

    if data_idx is None:
        production_log.adicionar_log("AUTO-RESUME: impossivel retomar - data_idx ausente no state")
        production_log.finalizar(cancelado=False)
        return False

    temas_data = _carregar_temas()
    linhas = temas_data.get("linhas", [])
    if data_idx >= len(linhas):
        production_log.adicionar_log("AUTO-RESUME: data_idx invalido (temas mudou?)")
        production_log.finalizar(cancelado=False)
        return False

    data_ref_state = log_state.get("data_ref", "")
    data_ref_temas = linhas[data_idx].get("data", "")
    if data_ref_state and data_ref_temas and data_ref_state != data_ref_temas:
        production_log.adicionar_log(f"AUTO-RESUME: data mismatch ({data_ref_state} vs {data_ref_temas})")
        production_log.finalizar(cancelado=False)
        return False

    canais = log_state.get("canais", [])
    has_pending = False

    for c in canais:
        etapa = c.get("etapa", "")
        if etapa in ("roteiro", "narracao", "video", "iniciando"):
            c["etapa"] = "aguardando"
            c["etapa_detalhe"] = "Reset apos reinicio"
            c["erro"] = ""
            has_pending = True
        elif etapa == "aguardando":
            has_pending = True

    if not has_pending:
        production_log.adicionar_log("AUTO-RESUME: sem canais pendentes, finalizando")
        production_log.finalizar(cancelado=False)
        return False

    production_log.adicionar_log("AUTO-RESUME: retomando producao interrompida")
    production_log._state["canais"] = canais
    production_log._salvar()

    _thread_producao = threading.Thread(
        target=produzir_data_completa,
        args=(data_idx,),
        kwargs={"ordem_colunas": ordem_colunas},
        daemon=True
    )
    _thread_producao.start()
    estado["ativo"] = True
    return True
