"""
Orquestrador de produção completa.
Roda no backend como thread. Cada etapa verifica, gera, salva e loga.
Fluxo: Roteiro → Narração → (Thumbnail futuro) → Vídeo
Tudo baseado em data + canal.
"""

import json
import time
import threading
import traceback
from pathlib import Path
from datetime import datetime

import production_log
import scriptwriter
import narrator
import transcriber
import subtitle_fixer
from engine import VideoEngine

BASE_DIR = Path(__file__).parent
TEMAS_FILE = BASE_DIR / "temas.json"
TEMPLATES_FILE = BASE_DIR / "templates.json"
NARRACOES_DIR = BASE_DIR / "narracoes"
TEMP_DIR = BASE_DIR / "temp"


def _carregar_temas():
    if TEMAS_FILE.exists():
        with open(TEMAS_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return {"colunas": [], "linhas": [], "celulas": {}}


def _salvar_temas(data):
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


def produzir_data_completa(data_idx: int, temas_data: dict = None, ordem_colunas: list = None):
    """Produz todos os canais de uma data. Roda em thread."""
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
        production_log.adicionar_log("ERRO: índice de data inválido")
        estado["ativo"] = False
        return

    row = linhas[data_idx]
    data_ref = row.get("data", "")

    # Parsear data para nomes de arquivo
    parts = data_ref.split("/")
    if len(parts) == 3:
        dd, mm, yyyy = parts[0], parts[1], parts[2]
    else:
        dd = mm = "00"
        yyyy = "2026"
    data_formatada = f"{dd}-{mm}"  # DD-MM para narração
    data_ymd = f"{yyyy}{mm}{dd}"  # YYYYMMDD para vídeo
    data_pasta = f"{yyyy}-{mm}-{dd}"  # YYYY-MM-DD para pastas

    # Montar lista de jobs (filtrada e ordenada)
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

        # Encontrar template associado
        template_id = col.get("template_id", "")
        tmpl = templates.get(template_id, {}) if template_id else {}

        # Voz do template
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

    # Iniciar production_log
    production_log.iniciar(data_ref, [{"tag": j["tag"], "template": j.get("template", {}).get("nome", "")} for j in jobs])

    try:
        for i, job in enumerate(jobs):
            if estado["cancelado"]:
                production_log.atualizar_canal(i, etapa="pulado", etapa_detalhe="Cancelado pelo usuário")
                continue

            tag = job["tag"]
            key = job["key"]
            production_log.atualizar_canal(i, etapa="iniciando", inicio=time.time())
            production_log.adicionar_log(f"{tag}: Iniciando produção")

            # Recarregar celula (pode ter sido atualizada)
            temas_data = _carregar_temas()
            cel = temas_data.get("celulas", {}).get(key, {})

            # ========================================
            # ETAPA 1: ROTEIRO
            # ========================================
            if cel.get("roteiro") and len(cel["roteiro"]) > 100:
                production_log.atualizar_canal(i, etapa="roteiro", etapa_detalhe=f"Existe ({len(cel['roteiro'])} chars)", roteiro_chars=len(cel["roteiro"]))
                production_log.adicionar_log(f"{tag}: Roteiro existe ({len(cel['roteiro'])} chars)")
            else:
                pipeline_id = job["pipeline_id"]
                if not pipeline_id:
                    production_log.atualizar_canal(i, etapa="erro", erro="Sem pipeline configurada")
                    production_log.adicionar_log(f"{tag}: ERRO - sem pipeline")
                    continue

                production_log.atualizar_canal(i, etapa="roteiro", etapa_detalhe="Gerando...")
                production_log.adicionar_log(f"{tag}: Gerando roteiro...")

                # Esperar pipeline anterior terminar
                for _ in range(60):
                    if not scriptwriter.estado_execucao.get("ativo"):
                        break
                    time.sleep(2)

                try:
                    scriptwriter.executar_pipeline(
                        pipeline_id, cel.get("tema", ""),
                        contexto_extra={
                            "tema": cel.get("tema", ""),
                            "canal": tag,
                            "data": data_ref,
                            "titulo": cel.get("titulo", ""),
                            "thumb": cel.get("thumb", ""),
                        }
                    )

                    # Esperar conclusão
                    for _ in range(300):
                        if not scriptwriter.estado_execucao.get("ativo"):
                            break
                        time.sleep(2)

                    resultado = scriptwriter.estado_execucao.get("resultado_final", "")

                    # Verificar tamanho mínimo (configurável por template)
                    min_chars = tmpl.get("min_roteiro_chars", 22000)
                    max_retries = 2
                    retry_count = 0

                    while resultado and len(resultado) < min_chars and retry_count < max_retries:
                        retry_count += 1
                        production_log.adicionar_log(f"{tag}: Roteiro CURTO ({len(resultado)} chars < {min_chars}) — retry {retry_count}/{max_retries}")
                        production_log.atualizar_canal(i, etapa_detalhe=f"Roteiro curto ({len(resultado)}ch), retry {retry_count}...")

                        # Esperar pipeline liberar
                        for _ in range(60):
                            if not scriptwriter.estado_execucao.get("ativo"):
                                break
                            time.sleep(2)

                        # Reger
                        scriptwriter.executar_pipeline(
                            pipeline_id, cel.get("tema", ""),
                            contexto_extra={"tema": cel.get("tema", ""), "canal": tag, "data": data_ref,
                                            "titulo": cel.get("titulo", ""), "thumb": cel.get("thumb", "")}
                        )
                        for _ in range(300):
                            if not scriptwriter.estado_execucao.get("ativo"):
                                break
                            time.sleep(2)
                        resultado = scriptwriter.estado_execucao.get("resultado_final", "")

                    # Após retries, verificar se ainda é curto
                    if resultado and len(resultado) < min_chars:
                        production_log.atualizar_canal(i, etapa="erro", erro=f"Roteiro muito curto após {max_retries} tentativas ({len(resultado)} chars)")
                        production_log.adicionar_log(f"{tag}: ERRO — roteiro curto após {max_retries} tentativas ({len(resultado)} chars)")
                        continue

                    if resultado and len(resultado) > 100:
                        # Salvar na célula
                        temas_data = _carregar_temas()
                        if key not in temas_data.get("celulas", {}):
                            temas_data.setdefault("celulas", {})[key] = {}
                        temas_data["celulas"][key]["roteiro"] = resultado
                        _salvar_temas(temas_data)
                        cel["roteiro"] = resultado

                        production_log.atualizar_canal(i, etapa_detalhe=f"OK ({len(resultado)} chars)", roteiro_chars=len(resultado))
                        production_log.adicionar_log(f"{tag}: Roteiro OK ({len(resultado)} chars)")
                    else:
                        erros = [e.get("erro", "") for e in scriptwriter.estado_execucao.get("etapas", []) if e.get("status") == "erro"]
                        production_log.atualizar_canal(i, etapa="erro", erro=f"Roteiro falhou: {'; '.join(erros)}")
                        production_log.adicionar_log(f"{tag}: ERRO roteiro - {'; '.join(erros)}")
                        continue

                except Exception as e:
                    production_log.atualizar_canal(i, etapa="erro", erro=str(e))
                    production_log.adicionar_log(f"{tag}: ERRO roteiro - {e}")
                    continue

            # ========================================
            # ETAPA 2: NARRAÇÃO
            # ========================================
            narr_nome = f"{tag} {data_formatada}"
            narr_subpasta = NARRACOES_DIR / data_pasta
            narr_path = narr_subpasta / f"{narr_nome}.mp3"

            if narr_path.exists():
                production_log.atualizar_canal(i, etapa="narracao", etapa_detalhe=f"Existe ({narr_path.name})", narracao_path=str(narr_path))
                production_log.adicionar_log(f"{tag}: Narração existe ({narr_path.name})")
            else:
                voice_id = job["voice_id"]
                if not voice_id:
                    production_log.atualizar_canal(i, etapa="erro", erro="Sem voz configurada")
                    production_log.adicionar_log(f"{tag}: ERRO - sem voz")
                    continue

                if not api_key:
                    production_log.atualizar_canal(i, etapa="erro", erro="Sem API key ai33.pro")
                    continue

                production_log.atualizar_canal(i, etapa="narracao", etapa_detalhe="Gerando...")
                production_log.adicionar_log(f"{tag}: Gerando narração ({len(cel.get('roteiro',''))} chars)...")

                # Esperar narração anterior
                for _ in range(60):
                    if not narrator.estado_narracao.get("ativo"):
                        break
                    time.sleep(2)

                try:
                    result = narrator.iniciar_narracao(
                        api_key, job["voice_provider"], voice_id,
                        cel.get("roteiro", ""), narr_nome,
                        speed=job["voice_speed"], pitch=job["voice_pitch"],
                    )

                    if not result.get("ok"):
                        production_log.atualizar_canal(i, etapa="erro", erro=result.get("erro", ""))
                        production_log.adicionar_log(f"{tag}: ERRO narração - {result.get('erro','')}")
                        continue

                    # Poll com timeout e detecção de estado perdido
                    narr_ok = False
                    poll_sem_progresso = 0
                    for _ in range(300):
                        st = narrator.poll_narracao()
                        # Detectar estado perdido (idle = processo morreu/reiniciou)
                        if st.get("status") == "idle" and not st.get("ativo"):
                            poll_sem_progresso += 1
                            if poll_sem_progresso > 5:
                                # Verificar se o arquivo já existe na pasta esperada
                                expected = narr_subpasta / f"{narr_nome}.mp3"
                                if expected.exists():
                                    narr_path = expected
                                    narr_ok = True
                                    production_log.adicionar_log(f"{tag}: Narração recuperada de {expected.name}")
                                    break
                                else:
                                    production_log.atualizar_canal(i, etapa="erro", erro="Narração perdida (estado idle)")
                                    production_log.adicionar_log(f"{tag}: ERRO - narração perdida (servidor reiniciou?)")
                                    break
                        if st.get("status") == "done":
                            narr_path_result = st.get("audio_local") or ""
                            if narr_path_result and Path(narr_path_result).exists():
                                production_log.atualizar_canal(i, etapa_detalhe=f"OK ({Path(narr_path_result).name})", narracao_path=narr_path_result)
                                production_log.adicionar_log(f"{tag}: Narração OK → {Path(narr_path_result).name}")
                                narr_path = Path(narr_path_result)
                                narr_ok = True
                            else:
                                # Chunking pode ter falhado — verificar se existe na pasta esperada
                                expected = narr_subpasta / f"{narr_nome}.mp3"
                                if expected.exists():
                                    narr_path = expected
                                    narr_ok = True
                                    production_log.adicionar_log(f"{tag}: Narração encontrada em {expected.name}")
                                else:
                                    production_log.atualizar_canal(i, etapa="erro", erro=f"Narração done mas arquivo não encontrado: {narr_path_result}")
                                    production_log.adicionar_log(f"{tag}: ERRO - arquivo não encontrado após narração")
                            break
                        elif st.get("status") == "error":
                            production_log.atualizar_canal(i, etapa="erro", erro=st.get("erro", ""))
                            production_log.adicionar_log(f"{tag}: ERRO narração - {st.get('erro','')}")
                            break
                        time.sleep(3)
                    else:
                        production_log.atualizar_canal(i, etapa="erro", erro="Narração timeout")
                        continue

                    if not narr_ok:
                        production_log.atualizar_canal(i, etapa="erro", erro="MP3 não encontrado após geração")
                        continue

                except Exception as e:
                    production_log.atualizar_canal(i, etapa="erro", erro=str(e))
                    production_log.adicionar_log(f"{tag}: ERRO narração - {e}")
                    continue

            # ========================================
            # ETAPA 3: VÍDEO
            # ========================================
            tmpl = job["template"]
            if not tmpl:
                production_log.atualizar_canal(i, etapa="erro", erro="Sem template de vídeo")
                continue

            pasta_saida = tmpl.get("pasta_saida", str(TEMP_DIR))
            video_pasta = Path(pasta_saida) / data_pasta
            video_pasta.mkdir(parents=True, exist_ok=True)
            video_nome = f"{tmpl.get('tag', tag)}_{data_ymd}_01.mp4"
            video_path = video_pasta / video_nome

            if video_path.exists() and video_path.stat().st_size > 1000:
                production_log.atualizar_canal(i, etapa="concluido", etapa_detalhe=f"Vídeo existe ({video_nome})", video_path=str(video_path), fim=time.time())
                production_log.adicionar_log(f"{tag}: Vídeo existe ({video_nome})")
                continue

            production_log.atualizar_canal(i, etapa="video", etapa_detalhe="Transcrevendo...")
            production_log.adicionar_log(f"{tag}: Produzindo vídeo...")

            try:
                mp3_path = str(narr_path)

                # Transcrever
                srt_path = transcriber.transcrever(mp3_path, tmpl.get("idioma"))

                # Corrigir legendas
                lc = tmpl.get("legenda_config", {})
                maiuscula = lc.get("maiuscula", tmpl.get("estilo_legenda") == 2)
                srt_corrigido = subtitle_fixer.corrigir_srt(
                    srt_path, tmpl.get("idioma", "en"), job["template_id"], maiuscula,
                    max_linhas=lc.get("max_linhas", 2),
                    max_chars=lc.get("max_chars", 30),
                    regras_template=tmpl.get("regras")
                )

                production_log.atualizar_canal(i, etapa_detalhe="Renderizando...", progresso=30)

                # Montar vídeo
                engine = VideoEngine(tmpl, mp3_path, str(video_path))
                engine.montar(srt_path=srt_corrigido)

                if video_path.exists() and video_path.stat().st_size > 1000:
                    production_log.atualizar_canal(i, etapa="concluido", etapa_detalhe=f"OK ({video_nome})", video_path=str(video_path), progresso=100, fim=time.time())
                    production_log.adicionar_log(f"{tag}: Vídeo OK → {video_nome}")

                    # Marcar como Done auto na célula
                    temas_data = _carregar_temas()
                    if key in temas_data.get("celulas", {}):
                        temas_data["celulas"][key]["done"] = True
                        temas_data["celulas"][key]["done_type"] = "auto"
                        _salvar_temas(temas_data)
                else:
                    production_log.atualizar_canal(i, etapa="erro", erro="Vídeo não gerado ou vazio")

            except Exception as e:
                production_log.atualizar_canal(i, etapa="erro", erro=str(e))
                production_log.adicionar_log(f"{tag}: ERRO vídeo - {e}")
                # Limpar arquivo corrompido
                if video_path.exists():
                    video_path.unlink(missing_ok=True)
                traceback.print_exc()

    except Exception as e:
        production_log.adicionar_log(f"ERRO FATAL: {e}")
        traceback.print_exc()
    finally:
        production_log.finalizar(estado["cancelado"])
        estado["ativo"] = False


def iniciar_producao(data_idx: int, temas_data: dict = None, ordem_colunas: list = None):
    """Inicia produção em thread separada."""
    if estado["ativo"]:
        return {"ok": False, "erro": "Produção já em andamento"}
    thread = threading.Thread(target=produzir_data_completa, args=(data_idx, temas_data, ordem_colunas), daemon=True)
    thread.start()
    return {"ok": True}


def cancelar():
    estado["cancelado"] = True
