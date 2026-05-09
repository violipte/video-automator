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
import sys
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
import video_log_db

try:
    import lib_thumbnail  # noqa: E402
except Exception as _e_thumb_import:
    lib_thumbnail = None
    print(f"[orchestrator] lib_thumbnail nao carregado: {_e_thumb_import}")

# Imports GPU — so necessarios em modo local (render_queue.REMOTE_MODE=False)
if not render_queue.REMOTE_MODE:
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


def _export_base():
    """Retorna o diretorio base de exports. Configuravel via config.json."""
    config = _obter_config()
    base = config.get("export_base", "")
    if base:
        return Path(base)
    if sys.platform == "win32":
        return Path("F:/Canal Dark/Automator Exports")
    return BASE_DIR / "exports"


# === ESTADO GLOBAL ===
estado = {
    "ativo": False,
    "cancelado": False,
    "loop": False,
    "loop_data_atual": None,
    "loop_total": 0,
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
        if sys.platform == "win32":
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

def _gerar_roteiro_para_canal(job_index, job, cel, data_ref, pasta_roteiros=None):
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

    # Marca inicio de etapa em video_log_db (pra calcular duracao depois)
    try:
        video_log_db.iniciar_etapa(data_ref, tag, "roteiro",
                                    template=tmpl.get("nome", ""),
                                    template_id=job.get("template_id", ""))
    except Exception:
        pass

    contexto = {
        "tema": cel.get("tema", ""),
        "canal": tag,
        "data": data_ref,
        "titulo": cel.get("titulo", ""),
        "thumb": cel.get("thumb", ""),
    }

    resultado = ""
    last_res = None
    for attempt in range(max_retries + 1):
        if estado["cancelado"]:
            return (job_index, None, "Cancelado")

        res = scriptwriter.executar_pipeline_isolado(pipeline_id, cel.get("tema", ""), contexto_extra=contexto)
        last_res = res

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
                try:
                    video_log_db.registrar_roteiro(
                        data_ref, tag, "erro", erro=erros,
                        template=tmpl.get("nome", ""), template_id=job.get("template_id", ""),
                    )
                except Exception:
                    pass
                return (job_index, None, erros)

    # Extrai provider + fallback do ultimo res
    def _extrai_provider(r):
        if not r: return ("", False)
        for etapa in reversed(r.get("etapas", [])):
            p = etapa.get("provider_usado") or etapa.get("credencial", {}).get("provedor") or ""
            if p:
                return (p, bool(etapa.get("fallback_used")))
        return ("", False)
    provider, fallback_used = _extrai_provider(last_res)

    if resultado and len(resultado) < min_chars:
        # Ultima cartada: forca fallback de provider (pula primario, vai direto pro proximo na chain).
        # Util quando o provider primario esta consistentemente gerando output curto.
        #
        # Tolerancia: como o fallback eh a ultima cartada antes de marcar erro, aceitamos
        # roteiros menores que min_chars desde que >= min_chars * tolerancia_fallback_pct.
        # Default 80% (ex: min 22000ch -> aceita >= 17600ch). Configuravel por template.
        tolerancia_pct = float(tmpl.get("tolerancia_fallback_pct", 0.80))
        min_aceito_fb = int(min_chars * tolerancia_pct)

        production_log.adicionar_log(
            f"{tag}: Roteiro CURTO ({len(resultado)} chars) apos {max_retries} retries. "
            f"Tentando FALLBACK forcado (aceita >= {min_aceito_fb}ch com tolerancia {int(tolerancia_pct*100)}%)..."
        )
        production_log.atualizar_canal(job_index, etapa_detalhe="Fallback forcado de provider...")
        try:
            res_fb = scriptwriter.executar_pipeline_isolado(
                pipeline_id, cel.get("tema", ""),
                contexto_extra=contexto, forcar_fallback=True,
            )
            fb_resultado = res_fb.get("resultado", "") if res_fb else ""
            fb_chars = len(fb_resultado)

            if res_fb and res_fb.get("ok") and fb_chars >= min_aceito_fb:
                resultado = fb_resultado
                last_res = res_fb
                provider, fallback_used = _extrai_provider(res_fb)
                if fb_chars >= min_chars:
                    production_log.adicionar_log(f"{tag}: Fallback forcado OK ({fb_chars} chars, provider={provider})")
                else:
                    production_log.adicionar_log(
                        f"{tag}: Fallback forcado ACEITO COM TOLERANCIA "
                        f"({fb_chars}ch >= {min_aceito_fb}ch min, target era {min_chars}ch, provider={provider})"
                    )
            else:
                # Mesmo com fallback ficou curto demais (abaixo da tolerancia), marca erro
                production_log.atualizar_canal(
                    job_index, etapa="erro",
                    erro=f"Roteiro curto ate com fallback ({fb_chars} chars, min aceito {min_aceito_fb})"
                )
                production_log.adicionar_log(
                    f"{tag}: ERRO - mesmo fallback gerou roteiro curto demais "
                    f"({fb_chars}ch < {min_aceito_fb}ch min com tolerancia)"
                )
                try:
                    video_log_db.registrar_roteiro(
                        data_ref, tag, "erro", provider=provider, fallback=True,
                        chars=fb_chars, erro=f"Roteiro curto mesmo com fallback ({fb_chars} chars, min aceito {min_aceito_fb})",
                        template=tmpl.get("nome", ""), template_id=job.get("template_id", ""),
                    )
                except Exception:
                    pass
                return (job_index, None, "Roteiro curto (fallback tambem falhou)")
        except Exception as e:
            production_log.atualizar_canal(job_index, etapa="erro", erro=f"Fallback forcado exception: {e}")
            production_log.adicionar_log(f"{tag}: ERRO - fallback forcado exception: {e}")
            try:
                video_log_db.registrar_roteiro(
                    data_ref, tag, "erro", provider=provider, fallback=fallback_used,
                    chars=len(resultado), erro=f"Fallback exception: {str(e)[:100]}",
                    template=tmpl.get("nome", ""), template_id=job.get("template_id", ""),
                )
            except Exception:
                pass
            return (job_index, None, f"Fallback forcado falhou: {e}")

    if resultado and len(resultado) > 100:
        temas_data = _carregar_temas()
        if key not in temas_data.get("celulas", {}):
            temas_data.setdefault("celulas", {})[key] = {}
        temas_data["celulas"][key]["roteiro"] = resultado
        _salvar_temas(temas_data)

        # Salvar .txt na pasta de data (fonte da verdade)
        if pasta_roteiros:
            txt_path = Path(pasta_roteiros) / f"{tag}.txt"
            txt_path.write_text(resultado, encoding="utf-8")

        production_log.atualizar_canal(job_index, etapa_detalhe=f"OK ({len(resultado)} chars)", roteiro_chars=len(resultado))
        production_log.adicionar_log(f"{tag}: Roteiro OK ({len(resultado)} chars)")
        try:
            video_log_db.registrar_roteiro(
                data_ref, tag, "ok", provider=provider, fallback=fallback_used,
                chars=len(resultado),
                template=tmpl.get("nome", ""), template_id=job.get("template_id", ""),
            )
        except Exception:
            pass
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

    # Salvar em Automator Exports/YYYY-MM-DD/Videos/
    EXPORT_BASE = _export_base()
    video_pasta = EXPORT_BASE / data_pasta / "Videos"
    video_pasta.mkdir(parents=True, exist_ok=True)
    video_nome = f"{tmpl.get('tag', tag)}_{data_ymd}_01.mp4"
    video_path = video_pasta / video_nome

    # Verificar tambem na pasta antiga
    pasta_saida_antiga = Path(tmpl.get("pasta_saida", str(TEMP_DIR))) / data_pasta
    video_path_antigo = pasta_saida_antiga / video_nome

    # Verificar video existente (pasta nova ou antiga)
    if video_path.exists() and video_path.stat().st_size > 1000:
        production_log.atualizar_canal(i, etapa="concluido", etapa_detalhe=f"Video existe ({video_nome})", video_path=str(video_path), fim=time.time())
        production_log.adicionar_log(f"{tag}: Video existe ({video_nome})")
        return True
    elif video_path_antigo.exists() and video_path_antigo.stat().st_size > 1000:
        production_log.atualizar_canal(i, etapa="concluido", etapa_detalhe=f"Video existe ({video_nome})", video_path=str(video_path_antigo), fim=time.time())
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
                cel_done = None
                if key in temas_data.get("celulas", {}):
                    temas_data["celulas"][key]["done"] = True
                    temas_data["celulas"][key]["done_type"] = "auto"
                    cel_done = temas_data["celulas"][key]
                    _salvar_temas(temas_data)

                # === Thumbnail generation (best-effort, nao bloqueia render OK) ===
                if lib_thumbnail and cel_done:
                    try:
                        thumb_pasta = EXPORT_BASE / data_pasta / "Thumbs"
                        thumb_pasta.mkdir(parents=True, exist_ok=True)
                        thumb_path = thumb_pasta / f"{tag}.jpg"
                        if thumb_path.exists() and thumb_path.stat().st_size > 1000:
                            production_log.adicionar_log(f"{tag}: Thumb existe, pulando geracao")
                            production_log.atualizar_canal(i, thumb_path=str(thumb_path))
                        else:
                            production_log.adicionar_log(f"{tag}: Gerando thumbnail...")
                            res = lib_thumbnail.gerar_thumbnail(
                                canal=tag,
                                tema=cel_done.get("tema", ""),
                                titulo=cel_done.get("titulo", ""),
                                thumb=cel_done.get("thumb", ""),
                                output_dir=thumb_pasta,
                            )
                            if res.get("ok"):
                                production_log.adicionar_log(f"{tag}: Thumb OK ({res.get('modo')}) -> {res.get('path')}")
                                production_log.atualizar_canal(i, thumb_path=res.get("path"))
                            else:
                                production_log.adicionar_log(f"{tag}: AVISO thumb falhou ({res.get('modo')}): {res.get('erro','?')[:120]}")
                    except Exception as _et:
                        production_log.adicionar_log(f"{tag}: AVISO thumb exception: {str(_et)[:120]}")

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

    # === PASTA DE DATA (fonte da verdade) ===
    EXPORT_BASE = _export_base()
    pasta_data = EXPORT_BASE / data_pasta
    pasta_roteiros = pasta_data / "Roteiros"
    pasta_narracoes = pasta_data / "Narracoes"
    pasta_thumbnails = pasta_data / "Thumbnails"
    pasta_videos = pasta_data / "Videos"
    for p in [pasta_roteiros, pasta_narracoes, pasta_thumbnails, pasta_videos]:
        p.mkdir(parents=True, exist_ok=True)

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

            # Verificar roteiro: .txt na pasta de data e a UNICA fonte da verdade
            # Skip robusto: precisa existir E ter size minimo (evita .txt vazio/corrompido).
            txt_path = pasta_roteiros / f"{tag}.txt"
            MIN_ROTEIRO_BYTES = 5000  # ~5KB = ~5min de narracao razoavel

            if txt_path.exists() and txt_path.stat().st_size >= MIN_ROTEIRO_BYTES:
                chars = txt_path.stat().st_size
                production_log.atualizar_canal(i, etapa="roteiro", etapa_detalhe=f"REAPROVEITANDO ({chars} chars)", roteiro_chars=chars)
                production_log.adicionar_log(f"{tag}: SKIP roteiro — reaproveitando {txt_path.name} ({chars} chars)")
                cel["roteiro"] = txt_path.read_text(encoding="utf-8")
                job["cel"] = cel
                roteiro_ok[i] = True
            else:
                if txt_path.exists():
                    production_log.adicionar_log(f"{tag}: roteiro {txt_path.name} muito pequeno ({txt_path.stat().st_size}B < {MIN_ROTEIRO_BYTES}B), regenerando")
                roteiro_para_gerar[i] = (job, cel)

        if roteiro_para_gerar and not estado["cancelado"]:
            with ThreadPoolExecutor(max_workers=3, thread_name_prefix="roteiro") as executor:
                futures = {}
                for i, (job, cel) in roteiro_para_gerar.items():
                    f = executor.submit(_gerar_roteiro_para_canal, i, job, cel, data_ref, pasta_roteiros)
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
        _render_pendentes = []

        def _encontrar_narracao(tag):
            """Busca MP3 na pasta nova (Automator Exports) e na antiga (narracoes/).
            Skip robusto: arquivo precisa existir E ter size minimo (>100KB = ~10s audio).
            """
            MIN_MP3_BYTES = 100_000
            # Pasta nova: Automator Exports/YYYY-MM-DD/Narracoes/TAG.mp3
            novo = pasta_narracoes / f"{tag}.mp3"
            if novo.exists() and novo.stat().st_size >= MIN_MP3_BYTES:
                return novo
            # Pasta antiga: narracoes/YYYY-MM-DD/TAG DD-MM.mp3
            antigo = NARRACOES_DIR / data_pasta / f"{tag} {data_formatada}.mp3"
            if antigo.exists() and antigo.stat().st_size >= MIN_MP3_BYTES:
                return antigo
            return None

        def _enfileirar_render(i, job, narr_path_val):
            """Enfileira render na fila compartilhada."""
            tag = job["tag"]

            # SKIP se ja tem render OK no DB (evita re-renderizar em modo REMOTE,
            # que nao tem acesso direto ao filesystem do pod pra checar o MP4).
            try:
                db_v = video_log_db.obter_video(data_ref, tag) or {}
                db_render = db_v.get("render", {}) or {}
                if db_render.get("status") == "ok" and db_render.get("path"):
                    db_path = db_render["path"]
                    production_log.atualizar_canal(
                        i, etapa="concluido",
                        etapa_detalhe=f"Ja renderizado ({Path(db_path).name})",
                        video_path=db_path, progresso=100, fim=time.time(),
                    )
                    production_log.adicionar_log(
                        f"{tag}: SKIP render — ja existe (DB: {db_path})"
                    )
                    # Marca cell como done no temas
                    try:
                        temas_data_local = _carregar_temas()
                        if job["key"] in temas_data_local.get("celulas", {}):
                            temas_data_local["celulas"][job["key"]]["done"] = True
                            temas_data_local["celulas"][job["key"]]["done_type"] = "auto"
                            _salvar_temas(temas_data_local)
                    except Exception:
                        pass
                    return  # NAO enfileira — economia de pod time
            except Exception as e:
                # Em caso de erro lendo DB, prossegue normal (rerendera, mas nao bloqueia)
                production_log.adicionar_log(f"{tag}: AVISO - check DB falhou ({e}), prosseguindo render")

            job_id = f"{tag}_{data_ymd}"
            evt = threading.Event()
            _render_pendentes.append(evt)

            def _on_done(video_path="", local_storage="local", tamanho_mb=0):
                # Em modo remoto, o worker reporta conclusao via API
                if render_queue.REMOTE_MODE:
                    production_log.atualizar_canal(i, etapa="concluido", etapa_detalhe=f"OK ({video_path.split('/')[-1] if video_path else 'render remoto'})", video_path=video_path, progresso=100, fim=time.time())
                    production_log.adicionar_log(f"{job['tag']}: Video OK -> {video_path or '(render remoto)'}")
                    temas_data_local = _carregar_temas()
                    cel_done_remoto = None
                    if job["key"] in temas_data_local.get("celulas", {}):
                        temas_data_local["celulas"][job["key"]]["done"] = True
                        temas_data_local["celulas"][job["key"]]["done_type"] = "auto"
                        cel_done_remoto = temas_data_local["celulas"][job["key"]]
                        _salvar_temas(temas_data_local)

                    # Thumbnail no fluxo remoto tambem (best-effort)
                    if lib_thumbnail and cel_done_remoto:
                        try:
                            EXPORT_BASE_T = _export_base()
                            thumb_pasta_r = EXPORT_BASE_T / data_pasta / "Thumbs"
                            thumb_pasta_r.mkdir(parents=True, exist_ok=True)
                            thumb_path_r = thumb_pasta_r / f"{job['tag']}.jpg"
                            if thumb_path_r.exists() and thumb_path_r.stat().st_size > 1000:
                                production_log.adicionar_log(f"{job['tag']}: Thumb existe, pulando")
                                production_log.atualizar_canal(i, thumb_path=str(thumb_path_r))
                            else:
                                production_log.adicionar_log(f"{job['tag']}: Gerando thumbnail (remoto)...")
                                res_t = lib_thumbnail.gerar_thumbnail(
                                    canal=job['tag'],
                                    tema=cel_done_remoto.get("tema", ""),
                                    titulo=cel_done_remoto.get("titulo", ""),
                                    thumb=cel_done_remoto.get("thumb", ""),
                                    output_dir=thumb_pasta_r,
                                )
                                if res_t.get("ok"):
                                    production_log.adicionar_log(f"{job['tag']}: Thumb OK ({res_t.get('modo')}) -> {res_t.get('path')}")
                                    production_log.atualizar_canal(i, thumb_path=res_t.get("path"))
                                else:
                                    production_log.adicionar_log(f"{job['tag']}: AVISO thumb falhou: {res_t.get('erro','?')[:120]}")
                        except Exception as _et2:
                            production_log.adicionar_log(f"{job['tag']}: AVISO thumb exception: {str(_et2)[:120]}")
                try:
                    video_log_db.registrar_render(
                        data_ref, job["tag"], "ok",
                        local_storage=local_storage,
                        path=video_path or "",
                        tamanho_mb=tamanho_mb,
                        template=job.get("template", {}).get("nome", ""),
                        template_id=job.get("template_id", ""),
                    )
                except Exception:
                    pass
                evt.set()

            def _on_error(erro):
                if render_queue.REMOTE_MODE:
                    production_log.atualizar_canal(i, etapa="erro", erro=str(erro))
                    production_log.adicionar_log(f"{job['tag']}: ERRO render remoto - {erro}")
                try:
                    video_log_db.registrar_render(
                        data_ref, job["tag"], "erro",
                        erro=str(erro),
                        template=job.get("template", {}).get("nome", ""),
                        template_id=job.get("template_id", ""),
                    )
                except Exception:
                    pass
                evt.set()

            # Marca inicio de etapa render em video_log_db (1ª vez)
            try:
                video_log_db.iniciar_etapa(data_ref, tag, "render",
                                            template=job.get("template", {}).get("nome", ""),
                                            template_id=job.get("template_id", ""))
            except Exception:
                pass

            if render_queue.REMOTE_MODE:
                # Modo remoto: enviar dados serializaveis pro worker externo
                EXPORT_BASE = _export_base()
                video_pasta = EXPORT_BASE / data_pasta / "Videos"
                video_nome = f"{job['template'].get('tag', job['tag'])}_{data_ymd}_01.mp4"

                job_data = {
                    "canal_idx": i,
                    "tag": job["tag"],
                    "key": job["key"],
                    "template_id": job.get("template_id", ""),
                    "template": job["template"],
                    "narr_filename": Path(str(narr_path_val)).name,
                    "narr_path_vps": str(narr_path_val),
                    "idioma": job["template"].get("idioma", "en"),
                    "data_formatada": data_formatada,
                    "data_ymd": data_ymd,
                    "data_pasta": data_pasta,
                    "video_pasta": str(video_pasta),
                    "video_nome": video_nome,
                }
                production_log.atualizar_canal(i, etapa="video", etapa_detalhe="Aguardando render worker...", inicio=time.time())
                render_queue.enfileirar(job_id, fonte="auto", on_done=_on_done, on_error=_on_error, job_data=job_data)
            else:
                # Modo local: callable direto
                def _do_render():
                    _renderizar_canal(i, job, narr_path_val, data_formatada, data_ymd, data_pasta)
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
                        narr_path = _encontrar_narracao(job['tag'])
                        if narr_path:
                            _enfileirar_render(i, job, narr_path)
                        continue

            tag = job["tag"]
            narr_path = _encontrar_narracao(tag)

            if narr_path:
                production_log.atualizar_canal(i, etapa="narracao", etapa_detalhe=f"Existe ({narr_path.name})", narracao_path=str(narr_path))
                production_log.adicionar_log(f"{tag}: Narracao existe ({narr_path.name})")
                _enfileirar_render(i, job, narr_path)
            else:
                canais_sem_narracao.append((i, job))

        production_log.adicionar_log(f"Render: {len(_render_pendentes)} canais enfileirados | Narracao: {len(canais_sem_narracao)} canais pendentes")

        # === SKIP PRIMARIO APOS N FALHAS CONSECUTIVAS ===
        # Se 2+ canais consecutivos cairem em fallback (sinal de outage do
        # ai33.pro), pula totalmente o primario nos canais subsequentes que
        # tem fallback Inworld configurado. Reseta quando o primario sucede
        # em algum canal. Escopo: apenas esta execucao de produzir_data_completa.
        falhas_consec_primario = 0
        SKIP_PRIMARIO_THRESHOLD = 2

        # === PROBE INICIAL DO ai33.pro ===
        # Antes de iniciar narracao, manda 1 chunk de 8000 chars com a voz
        # do primeiro canal pendente. Se ambas tentativas falharem (300s
        # primeira, 180s retry), considera primario OUT e ja ativa o gate
        # de skip para todos os canais subsequentes (mandatorio: todos os
        # canais tem fallback Inworld configurado).
        # Salta o probe se nao ha canal pendente (todos ja tem MP3).
        if canais_sem_narracao and api_key:
            _probe_job = canais_sem_narracao[0][1]
            _probe_voice_id_short = (_probe_job.get("voice_id", "") or "")[:12]
            production_log.adicionar_log(
                f"PROBE ai33.pro: testando voz {_probe_job.get('voice_provider', '')}/{_probe_voice_id_short} "
                f"(chunk 8k chars, 300s+180s)..."
            )
            try:
                probe_result = narrator.testar_ai33pro(
                    api_key=api_key,
                    voice_provider=_probe_job.get("voice_provider", ""),
                    voice_id=_probe_job.get("voice_id", ""),
                    voice_speed=_probe_job.get("voice_speed", 1.0),
                    voice_pitch=_probe_job.get("voice_pitch", 0),
                )
                if probe_result.get("ok"):
                    production_log.adicionar_log(
                        f"PROBE ai33.pro: OK em {probe_result.get('elapsed_s', 0):.1f}s. Producao normal."
                    )
                else:
                    production_log.adicionar_log(
                        f"PROBE ai33.pro: FALHOU - {probe_result.get('erro', '')}. "
                        f"Ativando skip primario para canais com Inworld."
                    )
                    # Forca o gate skip a partir do primeiro canal
                    falhas_consec_primario = SKIP_PRIMARIO_THRESHOLD
            except Exception as _e:
                production_log.adicionar_log(
                    f"PROBE ai33.pro: exception inesperada - {_e}. Producao segue sem skip forcado."
                )

        # --- DEPOIS: narrar os que faltam (sequencial) ---
        # Heuristica anti-timeout: se 2 canais consecutivos cairem em fallback Inworld
        # (Minimax via ai33.pro engasgando), os canais subsequentes da MESMA DATA
        # pulam Minimax e vao direto pro Inworld, economizando ~20min/canal.
        # Resetada a cada nova data (essa funcao roda 1x por data).
        fallbacks_consecutivos = 0
        forcar_inworld_resto_data = False

        for i, job in canais_sem_narracao:
            if estado["cancelado"]:
                break

            tag = job["tag"]
            cel = job.get("cel") or {}
            if not cel.get("roteiro"):
                temas_data = _carregar_temas()
                cel = temas_data.get("celulas", {}).get(job["key"], {})

            narr_nome = f"{tag}"
            narr_path = pasta_narracoes / f"{narr_nome}.mp3"

            voice_id = job["voice_id"]
            if not voice_id:
                production_log.atualizar_canal(i, etapa="erro", erro="Sem voz configurada")
                production_log.adicionar_log(f"{tag}: ERRO - sem voz")
                continue

            if not api_key:
                production_log.atualizar_canal(i, etapa="erro", erro="Sem API key ai33.pro")
                continue

            MAX_NARR_RETRIES = 2
            narr_succeeded = False  # default p/ caso o for_loop abaixo nao executar

            # Gate skip: se 2+ canais seguidos cairam em fallback E este canal tem
            # Inworld configurado, pula direto pro Inworld (zera retries primario).
            _fb_cfg = (job.get("template") or {}).get("narracao_voz", {}).get("fallback") or {}
            _inworld_key = config.get("inworld_api_key", "")
            _pode_inworld = bool(
                _fb_cfg.get("provider") == "inworld"
                and _inworld_key
                and _fb_cfg.get("voice_id")
            )
            _skip_primario = (falhas_consec_primario >= SKIP_PRIMARIO_THRESHOLD) and _pode_inworld
            if _skip_primario:
                production_log.adicionar_log(
                    f"{tag}: SKIP primario ({falhas_consec_primario} falhas consec) -> direto Inworld"
                )
                MAX_NARR_RETRIES = 0  # for_loop nao executa, vai direto pro bloco Inworld

            # Decisao: pular Minimax e ir direto pro Inworld?
            if forcar_inworld_resto_data:
                production_log.adicionar_log(f"{tag}: Pulando Minimax direto pra Inworld (2 fallbacks consecutivos detectados na data)")

            narr_succeeded = False
            # Marca inicio de etapa narracao em video_log_db (1ª vez apenas)
            try:
                video_log_db.iniciar_etapa(data_ref, tag, "narracao",
                                            template=job.get("template", {}).get("nome", ""),
                                            template_id=job.get("template_id", ""))
            except Exception:
                pass

            for narr_attempt in range(MAX_NARR_RETRIES if not forcar_inworld_resto_data else 0):
                production_log.atualizar_canal(i, etapa="narracao", etapa_detalhe=f"Gerando...{' (retry ' + str(narr_attempt) + ')' if narr_attempt > 0 else ''}", inicio=time.time())
                if narr_attempt == 0:
                    production_log.adicionar_log(f"{tag}: Gerando narracao ({len(cel.get('roteiro', ''))} chars)...")
                else:
                    production_log.adicionar_log(f"{tag}: Retentando narracao (tentativa {narr_attempt + 1}/{MAX_NARR_RETRIES})...")

                # Esperar narracao anterior terminar
                for _ in range(60):
                    if not narrator.estado_narracao_auto.get("ativo"):
                        break
                    time.sleep(2)
                # Garantir estado limpo antes de nova tentativa
                narrator.estado_narracao_auto["ativo"] = False
                narrator.estado_narracao_auto["status"] = "idle"

                try:
                    result = narrator.iniciar_narracao(
                        api_key, job["voice_provider"], voice_id,
                        cel.get("roteiro", ""), narr_nome,
                        pasta=str(pasta_narracoes),
                        speed=job["voice_speed"], pitch=job["voice_pitch"],
                        modo="auto",
                    )

                    if not result.get("ok"):
                        production_log.adicionar_log(f"{tag}: ERRO narracao - {result.get('erro', '')}")
                        if narr_attempt < MAX_NARR_RETRIES - 1:
                            production_log.adicionar_log(f"{tag}: Aguardando 15s antes de retry...")
                            time.sleep(15)
                            continue
                        production_log.atualizar_canal(i, etapa="erro", erro=result.get("erro", ""))
                        break

                    # Chunking sequencial retorna audio_local direto (sem poll)
                    if result.get("audio_local"):
                        narr_result_path = Path(result["audio_local"])
                        if narr_result_path.exists():
                            production_log.atualizar_canal(i, etapa_detalhe=f"OK ({narr_result_path.name})", narracao_path=str(narr_result_path))
                            production_log.adicionar_log(f"{tag}: Narracao OK -> {narr_result_path.name}")
                            try:
                                video_log_db.registrar_narracao(
                                    data_ref, tag, "ok",
                                    provider=job.get("voice_provider", ""),
                                    voice_id=voice_id,
                                    fallback=False,
                                    chunks=result.get("chunks", 0),
                                    path=str(narr_result_path),
                                    template=job.get("template", {}).get("nome", ""),
                                    template_id=job.get("template_id", ""),
                                )
                            except Exception:
                                pass
                            _enfileirar_render(i, job, narr_result_path)
                            narr_succeeded = True
                            # Minimax voltou — reseta contador de fallbacks consecutivos
                            fallbacks_consecutivos = 0
                            break
                        else:
                            production_log.adicionar_log(f"{tag}: ERRO - audio chunked nao encontrado")
                            if narr_attempt < MAX_NARR_RETRIES - 1:
                                time.sleep(15)
                                continue
                            production_log.atualizar_canal(i, etapa="erro", erro="Audio chunked nao encontrado")
                            break

                    # Modo single (sem chunking): poll normal
                    narr_ok = False
                    poll_sem_progresso = 0
                    narr_deadline = time.time() + TIMEOUT_NARRACAO

                    while time.time() < narr_deadline:
                        st = narrator.poll_narracao(modo="auto")
                        if st.get("status") == "idle" and not st.get("ativo"):
                            poll_sem_progresso += 1
                            if poll_sem_progresso > 5:
                                expected = pasta_narracoes / f"{narr_nome}.mp3"
                                if expected.exists():
                                    narr_path = expected
                                    narr_ok = True
                                    production_log.adicionar_log(f"{tag}: Narracao recuperada de {expected.name}")
                                    break
                                else:
                                    production_log.adicionar_log(f"{tag}: ERRO - narracao perdida (idle)")
                                    break
                        if st.get("status") == "done":
                            narr_path_result = st.get("audio_local") or ""
                            if narr_path_result and Path(narr_path_result).exists():
                                production_log.atualizar_canal(i, etapa_detalhe=f"OK ({Path(narr_path_result).name})", narracao_path=narr_path_result)
                                production_log.adicionar_log(f"{tag}: Narracao OK -> {Path(narr_path_result).name}")
                                narr_path = Path(narr_path_result)
                                narr_ok = True
                            else:
                                expected = pasta_narracoes / f"{narr_nome}.mp3"
                                if expected.exists():
                                    narr_path = expected
                                    narr_ok = True
                                    production_log.adicionar_log(f"{tag}: Narracao encontrada em {expected.name}")
                                else:
                                    production_log.adicionar_log(f"{tag}: ERRO - arquivo nao encontrado apos narracao")
                            break
                        elif st.get("status") == "error":
                            production_log.adicionar_log(f"{tag}: ERRO narracao - {st.get('erro', '')}")
                            break
                        time.sleep(3)
                    else:
                        # Timeout
                        narrator.estado_narracao_auto["ativo"] = False
                        narrator.estado_narracao_auto["status"] = "idle"
                        production_log.adicionar_log(f"{tag}: Narracao timeout ({TIMEOUT_NARRACAO // 60}min)")
                        if narr_attempt < MAX_NARR_RETRIES - 1:
                            production_log.adicionar_log(f"{tag}: Retentando apos timeout...")
                            time.sleep(10)
                            continue
                        production_log.atualizar_canal(i, etapa="erro", erro=f"Narracao timeout apos {MAX_NARR_RETRIES} tentativas")
                        break

                    if narr_ok:
                        try:
                            video_log_db.registrar_narracao(
                                data_ref, tag, "ok",
                                provider=job.get("voice_provider", ""),
                                voice_id=voice_id,
                                fallback=False,
                                path=str(narr_path),
                                template=job.get("template", {}).get("nome", ""),
                                template_id=job.get("template_id", ""),
                            )
                        except Exception:
                            pass
                        _enfileirar_render(i, job, narr_path)
                        narr_succeeded = True
                        # Minimax voltou — reseta contador de fallbacks consecutivos
                        fallbacks_consecutivos = 0
                        break
                    else:
                        # Narration failed, retry
                        if narr_attempt < MAX_NARR_RETRIES - 1:
                            time.sleep(15)
                            continue
                        production_log.atualizar_canal(i, etapa="erro", erro="MP3 nao encontrado apos geracao")
                        break

                except Exception as e:
                    production_log.adicionar_log(f"{tag}: ERRO narracao exception - {e}")
                    if narr_attempt < MAX_NARR_RETRIES - 1:
                        time.sleep(15)
                        continue
                    production_log.atualizar_canal(i, etapa="erro", erro=str(e))
                    break

            if narr_succeeded:
                falhas_consec_primario = 0  # primario voltou: reseta sinal de outage
                continue

            # Tentou primario e falhou: incrementa contador (so se REALMENTE tentou)
            if not _skip_primario:
                falhas_consec_primario += 1

            # === FALLBACK INWORLD ===
            # Todos os retries do provedor primario falharam. Tenta Inworld se configurado.
            fallback_cfg = (job.get("template") or {}).get("narracao_voz", {}).get("fallback")
            inworld_key = config.get("inworld_api_key", "")
            if fallback_cfg and fallback_cfg.get("provider") == "inworld" and inworld_key:
                fb_voice = fallback_cfg.get("voice_id", "")
                fb_model = fallback_cfg.get("model", "inworld-tts-1.5-max")
                if fb_voice:
                    production_log.atualizar_canal(i, etapa="narracao", etapa_detalhe="Fallback Inworld...", inicio=time.time())
                    production_log.adicionar_log(f"{tag}: Fallback Inworld ({fb_voice}) apos {MAX_NARR_RETRIES} falhas no primario")
                    try:
                        import narrator_inworld
                        fb_result = narrator_inworld.narrar_inworld_chunked(
                            api_key=inworld_key,
                            voice_id=fb_voice,
                            texto=cel.get("roteiro", ""),
                            nome_saida=narr_nome,
                            pasta=str(pasta_narracoes),
                            model=fb_model,
                        )
                        if fb_result.get("ok"):
                            fb_path = Path(fb_result["audio_local"])
                            production_log.atualizar_canal(i, etapa_detalhe=f"OK fallback ({fb_path.name})", narracao_path=str(fb_path), erro="")
                            production_log.adicionar_log(f"{tag}: Fallback Inworld OK -> {fb_path.name}")
                            try:
                                video_log_db.registrar_narracao(
                                    data_ref, tag, "ok",
                                    provider="inworld",
                                    voice_id=fb_voice,
                                    fallback=True,
                                    chunks=fb_result.get("chunks", 0),
                                    path=str(fb_path),
                                    template=job.get("template", {}).get("nome", ""),
                                    template_id=job.get("template_id", ""),
                                )
                            except Exception:
                                pass
                            _enfileirar_render(i, job, fb_path)
                            # Conta como fallback consecutivo (heuristica anti-timeout pra resto da data)
                            fallbacks_consecutivos += 1
                            if fallbacks_consecutivos >= 2 and not forcar_inworld_resto_data:
                                forcar_inworld_resto_data = True
                                production_log.adicionar_log(f"!!! 2 fallbacks consecutivos detectados — proximos canais da data {data_ref} pulam Minimax (vao direto pro Inworld). Economiza ~20min/canal de timeout.")
                            continue
                        else:
                            production_log.adicionar_log(f"{tag}: Fallback Inworld falhou - {fb_result.get('erro', '')}")
                            try:
                                video_log_db.registrar_narracao(
                                    data_ref, tag, "erro",
                                    provider="inworld",
                                    voice_id=fb_voice,
                                    fallback=True,
                                    erro=fb_result.get("erro", ""),
                                    template=job.get("template", {}).get("nome", ""),
                                    template_id=job.get("template_id", ""),
                                )
                            except Exception:
                                pass
                    except Exception as e:
                        production_log.adicionar_log(f"{tag}: Fallback Inworld exception - {e}")

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


REPASS_MAX = 2  # Passagens de reprocessamento no final do loop (alem da primeira)


def _data_teve_erro() -> bool:
    """Verifica se a data acabada de processar deixou algum canal em 'erro'."""
    try:
        canais = production_log.obter_estado().get("canais", [])
        return any(c.get("etapa") == "erro" for c in canais)
    except Exception:
        return False


def _produzir_loop(data_idx_inicio: int, ordem_colunas: list = None):
    """Produz em loop: completa uma data, faz repass IN-PLACE dela mesma se houver erros,
    e SO ENTAO avanca pra proxima.

    Cada data eh processada uma primeira vez; se deixar canais em 'erro', tenta de novo
    ate REPASS_MAX vezes adicionais ANTES de avancar pra proxima data. Como o orchestrator
    pula canais com MP4/MP3/.txt ja existentes, apenas os canais com erro sao re-tentados,
    e o forced fallback do scriptwriter cuida de pular o provider que falhou.
    """
    global estado
    temas_data = _carregar_temas()
    linhas = temas_data.get("linhas", [])
    colunas = temas_data.get("colunas", [])
    celulas = temas_data.get("celulas", {})
    total_datas = len(linhas)

    estado["loop"] = True
    estado["loop_data_atual"] = data_idx_inicio
    estado["loop_total"] = total_datas - data_idx_inicio

    # Auto-management: subir pods se nao tiver nenhum rodando
    if render_queue.REMOTE_MODE:
        try:
            import pods_manager
            atuais = [p for p in pods_manager.listar_pods() if p.get("status") == "RUNNING"]
            if not atuais:
                production_log.adicionar_log("LIFECYCLE: Nenhum pod ativo. Subindo 3 pods (boot ~3min)...")
                r = pods_manager.start_pods(n=3, aguardar_polls=True)
                production_log.adicionar_log(f"LIFECYCLE: {r.get('pods_prontos',0)}/{r.get('pods_criados',0)} pods bootstrapping. Aguardando workers conectarem (~3min adicional)...")
                # Espera workers reportarem polls
                time.sleep(180)  # buffer pra apt+pip+worker bundle terminar
            else:
                production_log.adicionar_log(f"LIFECYCLE: {len(atuais)} pods ja rodando, reaproveitando")
            pods_manager.marcar_atividade()
        except Exception as e:
            production_log.adicionar_log(f"LIFECYCLE: AVISO subir pods falhou ({e}); produzindo mesmo assim (workers podem nao existir)")

    for data_idx in range(data_idx_inicio, total_datas):
        if estado["cancelado"]:
            production_log.adicionar_log(f"LOOP: Cancelado pelo usuario na data {data_idx + 1}/{total_datas}")
            break

        # Verificar se essa data tem pelo menos 1 tema preenchido
        tem_tema = False
        col_indices = ordem_colunas if ordem_colunas else list(range(len(colunas)))
        for ci in col_indices:
            if ci >= len(colunas):
                continue
            key = f"{data_idx}_{ci}"
            cel = celulas.get(key, {})
            if cel.get("tema"):
                tem_tema = True
                break

        if not tem_tema:
            production_log.adicionar_log(f"LOOP: Data {linhas[data_idx].get('data','')} sem temas, parando loop")
            break

        data_ref = linhas[data_idx].get('data', '')
        estado["loop_data_atual"] = data_idx
        production_log.adicionar_log(f"LOOP: Iniciando data {data_idx + 1}/{total_datas} ({data_ref})")

        # Produzir essa data (bloqueia ate concluir)
        produzir_data_completa(data_idx, ordem_colunas=ordem_colunas)

        if estado["cancelado"]:
            break

        # === REPASS IN-PLACE ===
        # Se essa data deixou canais em erro, refaz a producao da mesma data
        # ate REPASS_MAX vezes ANTES de avancar pra proxima. Canais com MP4/MP3/.txt
        # ja gerados sao pulados automaticamente pelo orchestrator; apenas os em erro
        # sao re-tentados (e o forced fallback puxa outro provider).
        for pass_num in range(1, REPASS_MAX + 1):
            if estado["cancelado"]:
                break
            if not _data_teve_erro():
                break
            production_log.adicionar_log(
                f"LOOP: Data {data_ref} com erros, repass in-place {pass_num}/{REPASS_MAX} antes de avancar"
            )
            produzir_data_completa(data_idx, ordem_colunas=ordem_colunas)
            if not _data_teve_erro():
                production_log.adicionar_log(f"LOOP: Data {data_ref} OK apos repass in-place {pass_num}")
                break

        if not estado["cancelado"] and _data_teve_erro():
            production_log.adicionar_log(
                f"LOOP: Data {data_ref} ainda com erros apos {REPASS_MAX} repasses, "
                f"avancando para proxima data"
            )

        # Recarregar temas pra proxima data (pode ter mudado)
        temas_data = _carregar_temas()
        linhas = temas_data.get("linhas", [])
        colunas = temas_data.get("colunas", [])
        celulas = temas_data.get("celulas", {})

    estado["loop"] = False
    estado["ativo"] = False

    # Auto-management: parar pods quando produção terminar (loop completo ou cancelada)
    if render_queue.REMOTE_MODE:
        try:
            import pods_manager
            production_log.adicionar_log("LIFECYCLE: Produção encerrada. Parando todos os pods...")
            r = pods_manager.stop_all_pods()
            production_log.adicionar_log(f"LIFECYCLE: {r.get('parados', 0)} pods parados (custo GPU=$0)")
        except Exception as e:
            production_log.adicionar_log(f"LIFECYCLE: AVISO parar pods falhou ({e}); pare manualmente via /api/pods/stop")


def _iniciar_pods_se_necessario():
    """Hook universal: sobe pods se nao tem worker pollando, em modo REMOTE.
    Chamado pelo iniciar_producao (cobre tanto loop=true quanto false).

    NOVA LÓGICA: se há worker pollando o VPS recentemente (< 30s), assume
    que tem worker local rodando e NÃO sobe pods (evita competição).
    """
    if not render_queue.REMOTE_MODE:
        return
    try:
        import pods_manager
        # CHECA se já tem worker pollando (local ou remoto). Se sim, não sobe pods.
        try:
            workers_seen = getattr(render_queue, "_workers_seen", {}) or {}
            now = time.time()
            polls_recentes = [w for w, ts in workers_seen.items() if (now - ts) < 30]
            if polls_recentes:
                production_log.adicionar_log(f"LIFECYCLE: {len(polls_recentes)} worker(s) ja pollando ({list(polls_recentes)[:3]}), NAO subindo pods")
                pods_manager.marcar_atividade()
                return
        except Exception:
            pass

        atuais = [p for p in pods_manager.listar_pods() if p.get("status") == "RUNNING"]
        if atuais:
            production_log.adicionar_log(f"LIFECYCLE: {len(atuais)} pods ja rodando, reaproveitando")
            pods_manager.marcar_atividade()
            return
        production_log.adicionar_log("LIFECYCLE: Nenhum pod ativo nem worker local. Subindo 3 pods (boot ~3min)...")
        r = pods_manager.start_pods(n=3, aguardar_polls=True)
        production_log.adicionar_log(f"LIFECYCLE: {r.get('pods_prontos',0)}/{r.get('pods_criados',0)} pods bootstrapping. Workers conectam em ~3min.")
        pods_manager.marcar_atividade()
    except Exception as e:
        production_log.adicionar_log(f"LIFECYCLE: AVISO subir pods falhou ({e})")


def iniciar_producao(data_idx: int, temas_data: dict = None, ordem_colunas: list = None, loop: bool = False):
    """Inicia producao em thread separada. loop=True avanca pras proximas datas."""
    global _thread_producao
    if estado["ativo"]:
        return {"ok": False, "erro": "Producao ja em andamento"}

    estado["cancelado"] = False  # fix: evita que reset anterior bloqueie novo inicio
    # Limpar state anterior para forcar nova producao (nao resume)
    production_log._state = {
        "ativo": False, "data_ref": "", "data_idx": None, "ordem_colunas": None,
        "inicio": None, "total_canais": 0, "canal_atual": 0,
        "canais": [], "log": [], "concluidos": 0, "erros": 0, "pulados": 0, "cancelado": False,
    }
    production_log._salvar()

    # Wrapper que sobe pods antes da função real (em background pra não atrasar API response)
    def _wrap_loop(*args):
        _iniciar_pods_se_necessario()
        _produzir_loop(*args)

    def _wrap_data(*args):
        _iniciar_pods_se_necessario()
        produzir_data_completa(*args)
        # Quando termina (loop=false single data), parar pods também
        if render_queue.REMOTE_MODE:
            try:
                import pods_manager
                production_log.adicionar_log("LIFECYCLE: Produção encerrada. Parando pods...")
                r = pods_manager.stop_all_pods()
                production_log.adicionar_log(f"LIFECYCLE: {r.get('parados',0)} pods parados (custo GPU=$0)")
            except Exception as e:
                production_log.adicionar_log(f"LIFECYCLE: AVISO parar pods falhou ({e})")

    if loop:
        _thread_producao = threading.Thread(
            target=_wrap_loop, args=(data_idx, ordem_colunas), daemon=True
        )
    else:
        _thread_producao = threading.Thread(
            target=_wrap_data, args=(data_idx, temas_data, ordem_colunas), daemon=True
        )
    _thread_producao.start()
    return {"ok": True, "loop": loop}


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
