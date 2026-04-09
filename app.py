"""
Video Automator - Servidor FastAPI com interface web.
Gerenciamento de templates, produção de vídeos e processamento em lote.
"""

import json
import os
import sys
import threading
import time
import uuid
import shutil
from datetime import datetime
from pathlib import Path

# Adicionar DLLs NVIDIA ao PATH para CUDA funcionar
import ctypes
_nvidia_path = Path(sys.executable).parent / "Lib" / "site-packages" / "nvidia"
if _nvidia_path.exists():
    for dll_dir in _nvidia_path.glob("*/bin"):
        os.add_dll_directory(str(dll_dir))
        os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
    # Forçar carregamento das DLLs críticas
    _cublas = _nvidia_path / "cublas" / "bin" / "cublas64_12.dll"
    if _cublas.exists():
        ctypes.CDLL(str(_cublas))

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import HTMLResponse, JSONResponse
from fastapi.staticfiles import StaticFiles

import transcriber
import subtitle_fixer
import scriptwriter
import narrator
import thumbnail
import production_log
import orchestrator
from engine import VideoEngine

# === CONFIG ===
BASE_DIR = Path(__file__).parent
TEMPLATES_FILE = BASE_DIR / "templates.json"
HISTORICO_FILE = BASE_DIR / "historico.json"
TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)

app = FastAPI(title="Video Automator")

SERVER_START_TIME = time.time()

# === ESTADO GLOBAL ===
ESTADO_BATCH_FILE = BASE_DIR / "estado_batch.json"

def _carregar_estado_batch():
    if ESTADO_BATCH_FILE.exists():
        try:
            with open(ESTADO_BATCH_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            pass
    return {"ativo": False, "jobs": [], "job_atual": -1, "inicio": None, "cancelado": False}

def _salvar_estado_batch():
    try:
        with open(ESTADO_BATCH_FILE, "w", encoding="utf-8") as f:
            json.dump(estado_batch, f, ensure_ascii=False, default=str)
    except Exception:
        pass

estado_batch = _carregar_estado_batch()
# Se estava ativo quando o servidor morreu, marcar como inativo
if estado_batch.get("ativo"):
    estado_batch["ativo"] = False
    for j in estado_batch.get("jobs", []):
        if j.get("status") in ("transcrevendo", "corrigindo", "montando"):
            j["status"] = "erro"
            j["erro"] = "Servidor reiniciou durante produção"
    _salvar_estado_batch()

engine_atual: VideoEngine = None


# === HELPERS ===

def carregar_templates() -> dict:
    if not TEMPLATES_FILE.exists():
        return {}
    with open(TEMPLATES_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def carregar_historico() -> list:
    if not HISTORICO_FILE.exists():
        return []
    with open(HISTORICO_FILE, "r", encoding="utf-8") as f:
        return json.load(f)


def salvar_historico(historico: list):
    with open(HISTORICO_FILE, "w", encoding="utf-8") as f:
        json.dump(historico, f, ensure_ascii=False, indent=2)


def registrar_historico(template_id: str, tag: str, nome: str, mp3: str, output: str, duracao_seg: float, status: str, erro: str = None):
    historico = carregar_historico()
    historico.insert(0, {
        "template_id": template_id,
        "tag": tag,
        "nome": nome,
        "mp3": mp3,
        "output": output,
        "duracao_producao": round(duracao_seg, 1),
        "status": status,
        "erro": erro,
        "data": datetime.now().isoformat(),
    })
    # Manter últimos 500
    historico = historico[:500]
    salvar_historico(historico)


def salvar_templates(templates: dict):
    with open(TEMPLATES_FILE, "w", encoding="utf-8") as f:
        json.dump(templates, f, ensure_ascii=False, indent=2)


# === API: TEMPLATES ===

@app.get("/api/templates")
def listar_templates():
    templates = carregar_templates()
    lista = sorted(templates.values(), key=lambda t: t.get("ordem", 0))
    return lista


@app.post("/api/templates")
async def criar_template(request: Request):
    dados = await request.json()
    template_id = dados.get("id") or str(uuid.uuid4())[:8]
    dados["id"] = template_id
    dados["criado_em"] = datetime.now().isoformat()
    if "ordem" not in dados:
        templates = carregar_templates()
        dados["ordem"] = len(templates)

    templates = carregar_templates()
    templates[template_id] = dados
    salvar_templates(templates)
    return dados


@app.put("/api/templates/{template_id}")
async def atualizar_template(template_id: str, request: Request):
    templates = carregar_templates()
    if template_id not in templates:
        raise HTTPException(404, "Template não encontrado")
    dados = await request.json()
    dados["id"] = template_id
    templates[template_id] = dados
    salvar_templates(templates)
    return dados


@app.delete("/api/templates/{template_id}")
def deletar_template(template_id: str):
    templates = carregar_templates()
    if template_id not in templates:
        raise HTTPException(404, "Template não encontrado")
    del templates[template_id]
    salvar_templates(templates)
    return {"ok": True}


# === API: REGRAS ===

@app.get("/api/rules/{idioma}")
def listar_regras(idioma: str):
    return subtitle_fixer.listar_regras(idioma)


@app.get("/api/rules/{idioma}/{template_id}")
def obter_regras_template(idioma: str, template_id: str):
    todas = subtitle_fixer.listar_regras(idioma)
    return todas.get(template_id, todas.get("_global", {}))


@app.put("/api/rules/{idioma}/{template_id}")
async def salvar_regras(idioma: str, template_id: str, request: Request):
    dados = await request.json()
    subtitle_fixer.salvar_regras(idioma, template_id, dados)
    return {"ok": True}


# === API: PRODUÇÃO ===

@app.post("/api/produce")
async def produzir_video(request: Request):
    """Produz um único vídeo (síncrono para teste, batch usa thread)."""
    dados = await request.json()
    template_id = dados.get("template_id")
    mp3_path = dados.get("mp3_path")

    if not template_id or not mp3_path:
        raise HTTPException(400, "template_id e mp3_path são obrigatórios")

    templates = carregar_templates()
    if template_id not in templates:
        raise HTTPException(404, "Template não encontrado")

    template = templates[template_id]
    tag = template.get("tag", template_id)
    data = datetime.now().strftime("%Y%m%d")
    data_pasta = datetime.now().strftime("%Y-%m-%d")
    nome_saida = f"{tag}_{data}.mp4"
    pasta_saida = template.get("pasta_saida", str(TEMP_DIR))
    pasta_final = Path(pasta_saida) / data_pasta
    pasta_final.mkdir(parents=True, exist_ok=True)
    output_path = str(pasta_final / nome_saida)

    try:
        # Transcrever
        srt_path = transcriber.transcrever(mp3_path, template.get("idioma"))

        # Corrigir legendas
        lc = template.get("legenda_config", {})
        maiuscula = lc.get("maiuscula", template.get("estilo_legenda") == 2)
        srt_corrigido = subtitle_fixer.corrigir_srt(
            srt_path, template.get("idioma", "en"), template_id, maiuscula,
            regras_template=template.get("regras")
        )

        # Montar vídeo
        engine = VideoEngine(template, mp3_path, output_path)
        resultado = engine.montar(srt_path=srt_corrigido)

        return {"ok": True, "output": resultado}
    except Exception as e:
        raise HTTPException(500, str(e))


# === API: FONTES ===

_fontes_cache = None

@app.get("/api/fonts")
def listar_fontes():
    global _fontes_cache
    if _fontes_cache is not None:
        return _fontes_cache
    import subprocess as sp
    try:
        r = sp.run(
            ['powershell', '-Command',
             '[System.Reflection.Assembly]::LoadWithPartialName("System.Drawing") | Out-Null; '
             '(New-Object System.Drawing.Text.InstalledFontCollection).Families | ForEach-Object { $_.Name }'],
            capture_output=True, text=True, timeout=10
        )
        fontes = sorted(set(f.strip() for f in r.stdout.strip().split('\n') if f.strip()))
    except Exception:
        fontes = ["Arial", "Arial Black", "Calibri", "Cambria", "Comic Sans MS", "Consolas",
                  "Courier New", "Georgia", "Impact", "Segoe UI", "Tahoma", "Times New Roman",
                  "Trebuchet MS", "Verdana"]
    _fontes_cache = fontes
    return fontes


# === API: PREVIEW ===

import subprocess as _sp
import base64 as _b64
from fastapi.responses import FileResponse

@app.get("/api/preview/frame")
def preview_frame(path: str):
    """Extract first frame of a video file as base64 JPEG."""
    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "Arquivo não encontrado")
    out = TEMP_DIR / f"frame_{uuid.uuid4().hex[:8]}.jpg"
    try:
        _sp.run([
            "ffmpeg", "-y", "-i", str(p), "-vframes", "1",
            "-vf", "scale=640:-1", "-q:v", "3", str(out)
        ], capture_output=True, timeout=15)
        if not out.exists():
            raise HTTPException(500, "Falha ao extrair frame")
        with open(out, "rb") as f:
            data = _b64.b64encode(f.read()).decode()
        return {"base64": data, "mime": "image/jpeg"}
    finally:
        if out.exists():
            out.unlink()


@app.get("/api/preview/image")
def preview_image(path: str):
    """Return first image from a folder as base64 JPEG (resized to 640px wide)."""
    p = Path(path)
    if not p.exists() or not p.is_dir():
        raise HTTPException(404, "Pasta não encontrada")
    exts = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
    images = sorted([f for f in p.iterdir() if f.suffix.lower() in exts])
    if not images:
        raise HTTPException(404, "Nenhuma imagem encontrada na pasta")
    img_path = images[0]
    out = TEMP_DIR / f"preview_{uuid.uuid4().hex[:8]}.jpg"
    try:
        _sp.run([
            "ffmpeg", "-y", "-i", str(img_path),
            "-vf", "scale=640:-1", "-q:v", "3", str(out)
        ], capture_output=True, timeout=15)
        if not out.exists():
            raise HTTPException(500, "Falha ao processar imagem")
        with open(out, "rb") as f:
            data = _b64.b64encode(f.read()).decode()
        return {"base64": data, "mime": "image/jpeg"}
    finally:
        if out.exists():
            out.unlink()


@app.post("/api/preview/audio")
async def preview_audio(request: Request):
    """Generate a 15-second mp3 preview at specified volume."""
    dados = await request.json()
    trilha = dados.get("trilha", "")
    volume = float(dados.get("volume", 0.15))
    p = Path(trilha)
    if not p.exists():
        raise HTTPException(404, "Arquivo de trilha não encontrado")
    out_name = f"audio_preview_{uuid.uuid4().hex[:8]}.mp3"
    out = TEMP_DIR / out_name
    try:
        _sp.run([
            "ffmpeg", "-y", "-i", str(p), "-t", "15",
            "-af", f"volume={volume}", "-q:a", "5", str(out)
        ], capture_output=True, timeout=30)
        if not out.exists():
            raise HTTPException(500, "Falha ao gerar preview de áudio")
        return {"file": out_name}
    except Exception as e:
        raise HTTPException(500, str(e))


@app.get("/api/preview/audio/play")
def play_audio_preview(file: str):
    """Serve the generated audio preview file."""
    # Sanitize filename to prevent path traversal
    safe = Path(file).name
    p = TEMP_DIR / safe
    if not p.exists():
        raise HTTPException(404, "Preview não encontrado")
    return FileResponse(str(p), media_type="audio/mpeg")


# === API: HISTÓRICO ===

@app.get("/api/historico")
def listar_historico():
    return carregar_historico()


@app.delete("/api/historico")
def limpar_historico():
    salvar_historico([])
    return {"ok": True}


# === API: BATCH ===

def _executar_batch():
    """Thread de processamento em lote."""
    global engine_atual
    import traceback

    try:
        templates = carregar_templates()

        for i, job in enumerate(estado_batch["jobs"]):
            if estado_batch["cancelado"]:
                for j in range(i, len(estado_batch["jobs"])):
                    estado_batch["jobs"][j]["status"] = "cancelado"
                break

            estado_batch["job_atual"] = i
            job["status"] = "transcrevendo"
            job["inicio"] = time.time()

            template_id = job["template_id"]
            mp3_path = job["mp3"]
            template = templates.get(template_id)

            if not template:
                job["status"] = "erro"
                job["erro"] = "Template não encontrado"
                continue

            if not mp3_path or not Path(mp3_path).exists():
                job["status"] = "erro"
                job["erro"] = "Arquivo MP3 não encontrado"
                continue

            try:
                # Transcrever (progresso 0-10%)
                job["etapa"] = "Transcrevendo áudio..."
                def callback_transcricao(pct):
                    job["progresso"] = round(pct * 0.1, 1)

                srt_path = transcriber.transcrever(
                    mp3_path, template.get("idioma"),
                    callback_progresso=callback_transcricao
                )

                # Corrigir
                job["status"] = "corrigindo"
                job["etapa"] = "Corrigindo legendas..."
                job["progresso"] = 11
                lc = template.get("legenda_config", {})
                maiuscula = lc.get("maiuscula", template.get("estilo_legenda") == 2)
                srt_corrigido = subtitle_fixer.corrigir_srt(
                    srt_path, template.get("idioma", "en"), template_id, maiuscula,
                    max_linhas=lc.get("max_linhas", 2),
                    max_chars=lc.get("max_chars", 42),
                    regras_template=template.get("regras")
                )

                # Montar (progresso 12-100%, engine gerencia internamente)
                job["status"] = "montando"
                job["etapa"] = "Iniciando montagem..."
                tag = template.get("tag", template_id)
                # Usar data de referência se disponível (vem do grid de Temas)
                data_ref = job.get("data_ref", "")
                if data_ref:
                    # Formato: DD/MM/YYYY ou DD-MM ou YYYY-MM-DD
                    parts = data_ref.replace("-", "/").split("/")
                    if len(parts) == 3 and len(parts[0]) == 4:
                        # YYYY/MM/DD
                        data = parts[0] + parts[1] + parts[2]
                        data_pasta = parts[0] + "-" + parts[1] + "-" + parts[2]
                    elif len(parts) == 3:
                        # DD/MM/YYYY
                        data = parts[2] + parts[1] + parts[0]
                        data_pasta = parts[2] + "-" + parts[1] + "-" + parts[0]
                    else:
                        data = datetime.now().strftime("%Y%m%d")
                        data_pasta = datetime.now().strftime("%Y-%m-%d")
                else:
                    data = datetime.now().strftime("%Y%m%d")
                    data_pasta = datetime.now().strftime("%Y-%m-%d")
                seq = str(i + 1).zfill(2)
                nome_saida = f"{tag}_{data}_{seq}.mp4"
                pasta_saida = template.get("pasta_saida", str(TEMP_DIR))
                # Organizar por data: pasta_saida/YYYY-MM-DD/arquivo.mp4
                pasta_final = Path(pasta_saida) / data_pasta
                pasta_final.mkdir(parents=True, exist_ok=True)
                output_path = str(pasta_final / nome_saida)

                def callback_progresso(pct):
                    job["progresso"] = round(12 + pct * 0.88, 1)

                def callback_etapa(etapa):
                    job["etapa"] = etapa

                # Retry até 2x em caso de falha
                max_retries = 2
                for attempt in range(max_retries + 1):
                    try:
                        engine = VideoEngine(template, mp3_path, output_path)
                        engine_atual = engine
                        if attempt > 0:
                            job["etapa"] = f"Retry {attempt}/{max_retries}..."
                            job["progresso"] = 12
                        resultado = engine.montar(srt_path=srt_corrigido, callback_progresso=callback_progresso, callback_etapa=callback_etapa)
                        break  # sucesso
                    except Exception as retry_err:
                        if attempt < max_retries:
                            # Deletar arquivo parcial e tentar de novo
                            if Path(output_path).exists():
                                Path(output_path).unlink(missing_ok=True)
                            import time as _time
                            _time.sleep(5)
                            continue
                        raise retry_err  # falhou todas as tentativas

                job["status"] = "concluido"
                job["etapa"] = "Concluído"
                job["progresso"] = 100
                job["output"] = resultado
                job["fim"] = time.time()
                registrar_historico(template_id, tag, template.get("nome", ""), mp3_path, resultado, job["fim"] - job["inicio"], "concluido")

            except Exception as e:
                job["status"] = "erro"
                job["erro"] = str(e)
                job["etapa"] = "Erro"
                job["fim"] = time.time()
                # Limpar arquivo corrompido
                if Path(output_path).exists():
                    Path(output_path).unlink(missing_ok=True)
                registrar_historico(template_id, template.get("tag", ""), template.get("nome", ""), mp3_path, "", job["fim"] - job["inicio"], "erro", str(e))
                # Log para debug
                with open(TEMP_DIR / "erro_batch.log", "a", encoding="utf-8") as f:
                    f.write(f"\n{'='*60}\n{datetime.now().isoformat()} - Job {template_id}\n")
                    traceback.print_exc(file=f)

    except Exception as e:
        # Erro fatal na thread
        with open(TEMP_DIR / "erro_batch.log", "a", encoding="utf-8") as f:
            f.write(f"\n{'='*60}\nERRO FATAL: {datetime.now().isoformat()}\n")
            traceback.print_exc(file=f)
        # Marcar job atual como erro
        idx = estado_batch.get("job_atual", -1)
        if 0 <= idx < len(estado_batch["jobs"]):
            estado_batch["jobs"][idx]["status"] = "erro"
            estado_batch["jobs"][idx]["erro"] = str(e)
            estado_batch["jobs"][idx]["etapa"] = "Erro fatal"
    finally:
        engine_atual = None
        estado_batch["ativo"] = False
        _salvar_estado_batch()
        # Matar qualquer FFmpeg órfão
        try:
            import subprocess as _sp
            _sp.run(["taskkill", "/F", "/IM", "ffmpeg.exe"], capture_output=True, timeout=5)
        except Exception:
            pass


@app.post("/api/batch")
async def iniciar_batch(request: Request):
    global estado_batch
    if estado_batch["ativo"]:
        raise HTTPException(409, "Já existe um batch em andamento")

    dados = await request.json()
    jobs = dados.get("jobs", [])

    if not jobs:
        raise HTTPException(400, "Nenhum job fornecido")

    estado_batch = {
        "ativo": True,
        "jobs": [
            {
                "template_id": j["template_id"],
                "mp3": j.get("mp3", ""),
                "data_ref": j.get("data_ref", ""),
                "status": "aguardando",
                "progresso": 0,
                "erro": None,
                "output": None,
                "inicio": None,
                "fim": None,
                "etapa": "",
            }
            for j in jobs
        ],
        "job_atual": -1,
        "inicio": datetime.now().isoformat(),
        "cancelado": False,
    }

    _salvar_estado_batch()
    thread = threading.Thread(target=_executar_batch, daemon=True)
    thread.start()
    return {"ok": True, "total_jobs": len(jobs)}


@app.get("/api/batch/status")
def status_batch():
    return estado_batch


@app.post("/api/batch/cancel")
def cancelar_batch():
    global engine_atual
    estado_batch["cancelado"] = True
    if engine_atual:
        engine_atual.cancelar()
    return {"ok": True}


# === API: BROWSE FILESYSTEM ===

@app.get("/api/browse")
def browse_filesystem(path: str = ""):
    """Navega pelo sistema de arquivos local para seleção de pastas/arquivos."""
    if not path:
        # Listar drives no Windows
        import string
        drives = []
        for letra in string.ascii_uppercase:
            drive = f"{letra}:/"
            if Path(drive).exists():
                drives.append({"name": f"{letra}:", "path": drive, "type": "drive"})
        return drives

    p = Path(path)
    if not p.exists():
        raise HTTPException(404, "Caminho não encontrado")

    itens = []
    try:
        for item in sorted(p.iterdir(), key=lambda x: (not x.is_dir(), x.name.lower())):
            try:
                itens.append({
                    "name": item.name,
                    "path": str(item).replace("\\", "/"),
                    "type": "folder" if item.is_dir() else "file",
                    "ext": item.suffix.lower() if item.is_file() else None,
                })
            except PermissionError:
                continue
    except PermissionError:
        raise HTTPException(403, "Sem permissão para acessar este caminho")

    return itens


# === API: PIPELINES (ROTEIROS) ===

@app.get("/api/pipelines")
def listar_pipelines():
    pipelines = scriptwriter.carregar_pipelines()
    return sorted(pipelines.values(), key=lambda p: p.get("ordem", 0))


@app.post("/api/pipelines")
async def criar_pipeline(request: Request):
    dados = await request.json()
    pipeline_id = dados.get("id") or str(uuid.uuid4())[:8]
    dados["id"] = pipeline_id
    dados["criado_em"] = datetime.now().isoformat()
    pipelines = scriptwriter.carregar_pipelines()
    if "ordem" not in dados:
        dados["ordem"] = len(pipelines)
    pipelines[pipeline_id] = dados
    scriptwriter.salvar_pipelines(pipelines)
    return dados


@app.put("/api/pipelines/{pipeline_id}")
async def atualizar_pipeline(pipeline_id: str, request: Request):
    pipelines = scriptwriter.carregar_pipelines()
    if pipeline_id not in pipelines:
        raise HTTPException(404, "Pipeline não encontrada")
    dados = await request.json()
    dados["id"] = pipeline_id
    pipelines[pipeline_id] = dados
    scriptwriter.salvar_pipelines(pipelines)
    return dados


@app.delete("/api/pipelines/{pipeline_id}")
def deletar_pipeline(pipeline_id: str):
    pipelines = scriptwriter.carregar_pipelines()
    if pipeline_id not in pipelines:
        raise HTTPException(404, "Pipeline não encontrada")
    del pipelines[pipeline_id]
    scriptwriter.salvar_pipelines(pipelines)
    return {"ok": True}


@app.post("/api/pipelines/{pipeline_id}/executar")
async def executar_pipeline(pipeline_id: str, request: Request):
    if scriptwriter.estado_execucao["ativo"]:
        raise HTTPException(409, "Já existe uma execução em andamento")
    dados = await request.json()
    entrada = dados.get("entrada", "")
    if not entrada:
        raise HTTPException(400, "Entrada/tema é obrigatório")

    contexto = {
        "tema": dados.get("tema", entrada),
        "titulo": dados.get("titulo", ""),
        "thumb": dados.get("thumb", ""),
        "canal": dados.get("canal", ""),
        "data": dados.get("data", ""),
    }

    thread = threading.Thread(
        target=scriptwriter.executar_pipeline,
        args=(pipeline_id, entrada, contexto),
        daemon=True,
    )
    thread.start()
    return {"ok": True}


@app.post("/api/pipelines/testar-etapa")
async def testar_etapa(request: Request):
    """Testa uma etapa individual com entrada de teste."""
    dados = await request.json()
    etapa = dados.get("etapa", {})
    entrada = dados.get("entrada", "")

    variaveis = {
        "entrada": entrada,
        "tema": entrada,
        "saida_anterior": entrada,
        "roteiro_atual": entrada,
        "canal": "Teste",
        "data": "",
        "titulo": "",
        "thumb": "",
    }

    tipo = etapa.get("tipo", "llm")
    try:
        if tipo == "texto":
            resultado = scriptwriter._substituir_variaveis(etapa.get("prompt", ""), variaveis)
        elif tipo == "code":
            code = scriptwriter._substituir_variaveis(etapa.get("prompt", ""), variaveis)
            import re as _re
            exec_globals = {
                "entrada": entrada, "saida_anterior": entrada,
                "roteiro_atual": entrada, "variaveis": variaveis,
                "resultado": "", "len": len, "str": str, "int": int,
                "float": float, "re": _re,
            }
            exec(code, exec_globals)
            resultado = str(exec_globals.get("resultado", ""))
        else:
            cred_id = etapa.get("credencial", "")
            cred = scriptwriter.obter_credencial(cred_id)
            if not cred:
                return {"ok": False, "erro": f"Credencial não encontrada: {cred_id}"}
            provedor = cred.get("provedor", "claude")
            api_key = cred.get("api_key", "")
            modelo = etapa.get("modelo", "")
            system_msg = scriptwriter._substituir_variaveis(etapa.get("system_message", ""), variaveis)
            user_msg = scriptwriter._substituir_variaveis(etapa.get("prompt", ""), variaveis)
            fn = scriptwriter.CHAMADAS.get(provedor)
            if not fn:
                return {"ok": False, "erro": f"Provedor desconhecido: {provedor}"}
            resultado = fn(system_msg, user_msg, api_key, modelo)

        return {"ok": True, "resultado": resultado}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


@app.post("/api/pipelines/testar-cadeia")
async def testar_cadeia(request: Request):
    """Testa uma cadeia de etapas sequencialmente."""
    dados = await request.json()
    etapas = dados.get("etapas", [])
    entrada = dados.get("entrada", "")

    variaveis = {
        "entrada": entrada, "tema": entrada,
        "saida_anterior": entrada, "roteiro_atual": entrada,
        "canal": "", "data": "", "titulo": "", "thumb": "",
    }

    resultados = []
    try:
        for i, etapa in enumerate(etapas):
            tipo = etapa.get("tipo", "llm")
            resultado = ""
            status = "ok"
            try:
                if tipo == "texto":
                    resultado = scriptwriter._substituir_variaveis(etapa.get("prompt", ""), variaveis)
                elif tipo == "code":
                    code = scriptwriter._substituir_variaveis(etapa.get("prompt", ""), variaveis)
                    import re as _re
                    exec_globals = {
                        "entrada": variaveis["entrada"], "saida_anterior": variaveis["saida_anterior"],
                        "roteiro_atual": variaveis["roteiro_atual"], "variaveis": variaveis,
                        "resultado": "", "len": len, "str": str, "int": int, "float": float, "re": _re,
                    }
                    exec(code, exec_globals)
                    resultado = str(exec_globals.get("resultado", ""))
                else:
                    cred = scriptwriter.obter_credencial(etapa.get("credencial", ""))
                    if not cred:
                        raise ValueError(f"Credencial não encontrada")
                    fn = scriptwriter.CHAMADAS.get(cred["provedor"])
                    if not fn:
                        raise ValueError(f"Provedor desconhecido: {cred['provedor']}")
                    system_msg = scriptwriter._substituir_variaveis(etapa.get("system_message", ""), variaveis)
                    user_msg = scriptwriter._substituir_variaveis(etapa.get("prompt", ""), variaveis)
                    resultado = fn(system_msg, user_msg, cred["api_key"], etapa.get("modelo", ""))

                variaveis["saida_anterior"] = resultado
                variaveis[f"saida_etapa_{i+1}"] = resultado
                variaveis["roteiro_atual"] = resultado
            except Exception as e:
                resultado = f"ERRO: {e}"
                status = "erro"

            resultados.append({"resultado": resultado, "status": status})

        return {"ok": True, "resultados": resultados}
    except Exception as e:
        return {"ok": False, "erro": str(e)}


@app.get("/api/pipelines/execucao")
def status_execucao():
    return scriptwriter.estado_execucao


@app.post("/api/pipelines/execucao/cancelar")
def cancelar_execucao():
    scriptwriter.estado_execucao["cancelado"] = True
    return {"ok": True}


# === API: CREDENCIAIS ===

@app.get("/api/credenciais")
def listar_credenciais():
    creds = scriptwriter.carregar_credenciais()
    # Mascarar keys
    safe = []
    for c in creds:
        sc = dict(c)
        k = sc.get("api_key", "")
        if len(k) > 8:
            sc["api_key_masked"] = "..." + k[-4:]
        else:
            sc["api_key_masked"] = "***"
        del sc["api_key"]
        safe.append(sc)
    return safe


@app.post("/api/credenciais")
async def criar_credencial(request: Request):
    dados = await request.json()
    creds = scriptwriter.carregar_credenciais()
    cred_id = dados.get("id") or str(uuid.uuid4())[:8]
    cred = {
        "id": cred_id,
        "nome": dados.get("nome", ""),
        "provedor": dados.get("provedor", "claude"),
        "api_key": dados.get("api_key", ""),
        "modelos": [],
    }
    # Testar e buscar modelos
    teste = scriptwriter.testar_credencial(cred["provedor"], cred["api_key"])
    if teste["ok"]:
        cred["modelos"] = teste["modelos"]
        cred["status"] = "ok"
    else:
        cred["status"] = "erro"
        cred["erro"] = teste.get("erro", "")
    creds.append(cred)
    scriptwriter.salvar_credenciais(creds)
    safe = dict(cred)
    safe["api_key_masked"] = "..." + cred["api_key"][-4:]
    del safe["api_key"]
    return safe


@app.put("/api/credenciais/{cred_id}")
async def atualizar_credencial(cred_id: str, request: Request):
    dados = await request.json()
    creds = scriptwriter.carregar_credenciais()
    for i, c in enumerate(creds):
        if c["id"] == cred_id:
            if dados.get("api_key") and not dados["api_key"].startswith("..."):
                c["api_key"] = dados["api_key"]
            if dados.get("nome"):
                c["nome"] = dados["nome"]
            if dados.get("provedor"):
                c["provedor"] = dados["provedor"]
            # Re-testar
            teste = scriptwriter.testar_credencial(c["provedor"], c["api_key"])
            if teste["ok"]:
                c["modelos"] = teste["modelos"]
                c["status"] = "ok"
            else:
                c["status"] = "erro"
                c["erro"] = teste.get("erro", "")
            creds[i] = c
            scriptwriter.salvar_credenciais(creds)
            safe = dict(c)
            safe["api_key_masked"] = "..." + c["api_key"][-4:]
            del safe["api_key"]
            return safe
    raise HTTPException(404, "Credencial não encontrada")


@app.delete("/api/credenciais/{cred_id}")
def deletar_credencial(cred_id: str):
    creds = scriptwriter.carregar_credenciais()
    creds = [c for c in creds if c["id"] != cred_id]
    scriptwriter.salvar_credenciais(creds)
    return {"ok": True}


@app.post("/api/credenciais/{cred_id}/refresh")
def refresh_modelos(cred_id: str):
    creds = scriptwriter.carregar_credenciais()
    for i, c in enumerate(creds):
        if c["id"] == cred_id:
            teste = scriptwriter.testar_credencial(c["provedor"], c["api_key"])
            if teste["ok"]:
                c["modelos"] = teste["modelos"]
                c["status"] = "ok"
            else:
                c["status"] = "erro"
                c["erro"] = teste.get("erro", "")
            creds[i] = c
            scriptwriter.salvar_credenciais(creds)
            return {"ok": teste["ok"], "modelos": c.get("modelos", []), "erro": c.get("erro", "")}
    raise HTTPException(404, "Credencial não encontrada")


# === API: TEMAS ===

@app.get("/api/temas")
def listar_temas(light: bool = True):
    data = scriptwriter.carregar_temas()
    if isinstance(data, list):
        return {"colunas": [], "linhas": [], "celulas": {}}
    if light:
        # Versão leve: sem roteiros (economiza ~380KB)
        light_data = {
            "colunas": data.get("colunas", []),
            "linhas": data.get("linhas", []),
            "celulas": {}
        }
        for k, v in data.get("celulas", {}).items():
            light_data["celulas"][k] = {key: val for key, val in v.items() if key != "roteiro"}
            # Indicar se tem roteiro (sem enviar o texto)
            if v.get("roteiro"):
                light_data["celulas"][k]["tem_roteiro"] = len(v["roteiro"])
        return light_data
    return data


@app.post("/api/temas")
async def salvar_temas_grid(request: Request):
    """Salva o grid inteiro de temas."""
    dados = await request.json()
    scriptwriter.salvar_temas(dados)
    # Sync com Supabase: enviar cada célula com título
    config = scriptwriter.carregar_config()
    if config.get("supabase_url") and config.get("supabase_key"):
        colunas = dados.get("colunas", [])
        linhas = dados.get("linhas", [])
        celulas = dados.get("celulas", {})
        for key, cel in celulas.items():
            if cel.get("titulo") and not cel.get("synced"):
                parts = key.split("_")
                ri, ci = int(parts[0]), int(parts[1])
                row_data = linhas[ri]["data"] if ri < len(linhas) else ""
                col_nome = colunas[ci]["nome"] if ci < len(colunas) else ""
                scriptwriter.sync_supabase("temas", {
                    "data": row_data,
                    "canal": col_nome,
                    "titulo": cel.get("titulo", ""),
                    "thumb": cel.get("thumb", ""),
                }, config)
                cel["synced"] = True
        scriptwriter.salvar_temas(dados)
    return {"ok": True}


# === API: NARRAÇÃO ===

_vozes_cache = {"data": [], "ts": 0}

@app.get("/api/narration/voices")
def listar_vozes(refresh: bool = False):
    # Cache de 10 minutos para evitar 10s de latência
    if not refresh and _vozes_cache["data"] and (time.time() - _vozes_cache["ts"]) < 600:
        return _vozes_cache["data"]

    config = scriptwriter.carregar_config()
    api_key = config.get("ai33_api_key", "")
    if not api_key:
        # Retornar cache antigo se tiver, mesmo expirado
        if _vozes_cache["data"]:
            return _vozes_cache["data"]
        raise HTTPException(400, "API key do ai33.pro não configurada (Config > Sync)")
    vozes = []
    vozes.extend(narrator.listar_vozes_clonadas(api_key))
    vozes.extend(narrator.listar_vozes_elevenlabs(api_key))
    vozes.extend(narrator.listar_vozes_elevenlabs_shared(api_key))
    vozes.extend(narrator.listar_vozes_minimax(api_key))
    result = [v for v in vozes if "error" not in v]
    _vozes_cache["data"] = result
    _vozes_cache["ts"] = time.time()
    return result


@app.post("/api/narration/generate")
async def gerar_narracao(request: Request):
    dados = await request.json()
    config = scriptwriter.carregar_config()
    api_key = config.get("ai33_api_key", "")
    if not api_key:
        raise HTTPException(400, "API key do ai33.pro não configurada")

    provider = dados.get("provider", "elevenlabs")
    voice_id = dados.get("voice_id", "")
    texto = dados.get("texto", "")
    nome = dados.get("nome", "narracao")

    if not voice_id or not texto:
        raise HTTPException(400, "voice_id e texto são obrigatórios")

    pasta = dados.get("pasta", "")
    preview = dados.get("preview", False)
    result = narrator.iniciar_narracao(
        api_key, provider, voice_id, texto, nome,
        model=dados.get("model", "speech-2.6-hd"),
        model_id=dados.get("model_id", "eleven_multilingual_v2"),
        speed=dados.get("speed", 1.0),
        pitch=dados.get("pitch", 0),
        pasta=pasta,
        preview=preview,
    )
    return result


@app.get("/api/narration/status")
def status_narracao():
    return narrator.poll_narracao()


@app.get("/api/narration/credits")
def obter_creditos():
    """Retorna créditos restantes da última operação."""
    return {"credits": narrator.ultimo_creditos}


# === API: PRODUÇÃO COMPLETA (ORQUESTRADOR) ===

estado_producao_completa = {
    "ativo": False,
    "etapa": "",
    "coluna_atual": "",
    "progresso": 0,
    "log": [],
}

@app.get("/api/producao-completa/status")
def status_producao_completa():
    return estado_producao_completa


# === API: THUMBNAILS ===

@app.get("/api/thumbnail/preview")
async def preview_thumbnail(request: Request):
    """Gera preview de thumbnail com texto sobreposto (retorna base64)."""
    params = dict(request.query_params)
    imagem = params.get("imagem", "")
    texto = params.get("texto", "")

    if not imagem or not Path(imagem).exists():
        raise HTTPException(400, "Imagem de fundo não encontrada")
    if not texto:
        raise HTTPException(400, "Texto é obrigatório")

    config = {}
    for k in ("font", "size", "color", "outline_color", "outline_width",
              "shadow", "shadow_offset", "position", "margin", "line_spacing"):
        if k in params:
            val = params[k]
            if k in ("size", "outline_width", "shadow_offset", "margin", "line_spacing"):
                val = int(val)
            elif k == "shadow":
                val = val.lower() in ("true", "1", "yes")
            config[k] = val

    try:
        b64 = thumbnail.gerar_thumbnail_base64(imagem, texto, config)
        return {"base64": b64, "mime": "image/jpeg"}
    except Exception as e:
        raise HTTPException(500, f"Erro ao gerar preview: {e}")


@app.post("/api/thumbnail/generate")
async def gerar_thumbnail_endpoint(request: Request):
    """Gera e salva thumbnail."""
    dados = await request.json()
    imagem = dados.get("imagem", "")
    texto = dados.get("texto", "")
    output = dados.get("output", "")
    config = dados.get("config", {})

    if not imagem or not Path(imagem).exists():
        raise HTTPException(400, "Imagem de fundo não encontrada")
    if not texto:
        raise HTTPException(400, "Texto é obrigatório")
    if not output:
        raise HTTPException(400, "Caminho de saída é obrigatório")

    try:
        result = thumbnail.salvar_thumbnail(imagem, texto, config, output)
        return {"ok": True, "output": result}
    except Exception as e:
        raise HTTPException(500, f"Erro ao gerar thumbnail: {e}")


@app.post("/api/thumbnail/extract-youtube")
async def extrair_thumb_youtube_endpoint(request: Request):
    """Extrai thumbnail de URL do YouTube."""
    dados = await request.json()
    url = dados.get("url", "")
    if not url:
        raise HTTPException(400, "URL é obrigatória")

    result = thumbnail.extrair_thumb_youtube(url)
    if "error" in result:
        raise HTTPException(400, result["error"])

    # Se pedir para baixar
    if dados.get("baixar") and dados.get("output"):
        try:
            thumb_url = result.get("thumbnail_url", result.get("thumbnail_hq", ""))
            downloaded = thumbnail.baixar_imagem(thumb_url, dados["output"])
            result["local_path"] = downloaded
        except Exception as e:
            result["download_error"] = str(e)

    return result


# === API: MONITOR ===

@app.get("/api/monitor")
def monitor_status():
    """Dashboard de monitoramento geral."""
    # Batch status
    batch = dict(estado_batch)

    # Narração status
    narracao = {k: v for k, v in narrator.estado_narracao.items() if k != "api_key"}

    # Pipeline status
    pipeline = dict(scriptwriter.estado_execucao)

    # Créditos
    creditos = narrator.ultimo_creditos

    # Histórico summary
    historico = carregar_historico()
    por_status = {}
    por_data = {}
    por_tag = {}
    for h in historico:
        s = h.get("status", "desconhecido")
        por_status[s] = por_status.get(s, 0) + 1
        d = h.get("data", "")[:10]
        if d:
            por_data[d] = por_data.get(d, 0) + 1
        t = h.get("tag", "")
        if t:
            por_tag[t] = por_tag.get(t, 0) + 1

    # Últimos 10 vídeos
    ultimos = historico[:10]

    # Uptime
    uptime_seg = time.time() - SERVER_START_TIME
    horas = int(uptime_seg // 3600)
    minutos = int((uptime_seg % 3600) // 60)
    segundos = int(uptime_seg % 60)
    uptime_str = f"{horas:02d}:{minutos:02d}:{segundos:02d}"

    # Disk usage
    disco = {}
    pastas_disco = {
        "cache": BASE_DIR / "cache",
        "narracoes": BASE_DIR / "narracoes",
        "temp": TEMP_DIR,
    }
    # Adicionar pastas de saída dos templates
    try:
        templates = carregar_templates()
        for t in templates.values():
            p = t.get("pasta_saida", "")
            if p:
                disco_key = f"output ({t.get('tag', t.get('id', '?'))})"
                pastas_disco[disco_key] = Path(p)
    except Exception:
        pass

    for nome, pasta in pastas_disco.items():
        try:
            if pasta.exists():
                total = sum(f.stat().st_size for f in pasta.rglob("*") if f.is_file())
                disco[nome] = {"bytes": total, "humano": f"{total / (1024*1024):.1f} MB" if total < 1024**3 else f"{total / (1024**3):.2f} GB"}
            else:
                disco[nome] = {"bytes": 0, "humano": "0 MB"}
        except Exception:
            disco[nome] = {"bytes": -1, "humano": "erro"}

    # Estatísticas calculadas
    hoje = datetime.now().strftime("%Y-%m-%d")
    import datetime as _dt
    inicio_semana = (datetime.now() - _dt.timedelta(days=datetime.now().weekday())).strftime("%Y-%m-%d")
    videos_hoje = sum(1 for h in historico if h.get("data", "")[:10] == hoje and h.get("status") == "concluido")
    videos_semana = sum(1 for h in historico if h.get("data", "")[:10] >= inicio_semana and h.get("status") == "concluido")
    videos_total = sum(1 for h in historico if h.get("status") == "concluido")
    tempos = [h.get("duracao_producao", 0) for h in historico if h.get("status") == "concluido" and h.get("duracao_producao")]
    tempo_medio = round(sum(tempos) / len(tempos), 1) if tempos else 0

    return {
        "batch": batch,
        "narracao": narracao,
        "pipeline": pipeline,
        "creditos": creditos,
        "historico_resumo": {
            "por_status": por_status,
            "por_data": por_data,
            "por_tag": por_tag,
        },
        "ultimos": ultimos,
        "uptime": uptime_str,
        "disco": disco,
        "estatisticas": {
            "videos_hoje": videos_hoje,
            "videos_semana": videos_semana,
            "videos_total": videos_total,
            "tempo_medio": tempo_medio,
        },
        "producao_completa": production_log.obter_estado(),
    }


# === API: PRODUCTION LOG ===

@app.post("/api/production-log/start")
async def prod_log_start(request: Request):
    dados = await request.json()
    production_log.iniciar(dados.get("data_ref", ""), dados.get("canais", []))
    return {"ok": True}

@app.post("/api/production-log/update")
async def prod_log_update(request: Request):
    dados = await request.json()
    production_log.atualizar_canal(dados.get("index", 0), **{k:v for k,v in dados.items() if k != "index"})
    if dados.get("log_msg"):
        production_log.adicionar_log(dados["log_msg"])
    return {"ok": True}

@app.post("/api/production-log/finish")
async def prod_log_finish(request: Request):
    dados = await request.json()
    production_log.finalizar(dados.get("cancelado", False))
    return {"ok": True}


# === API: PRODUÇÃO COMPLETA (BACKEND) ===

@app.post("/api/producao-completa/iniciar")
async def iniciar_producao_completa(request: Request):
    """Inicia produção completa no backend (thread)."""
    dados = await request.json()
    data_idx = dados.get("data_idx", -1)
    if data_idx < 0:
        raise HTTPException(400, "data_idx é obrigatório")
    result = orchestrator.iniciar_producao(data_idx)
    if not result.get("ok"):
        raise HTTPException(409, result.get("erro", "Erro"))
    return result


@app.post("/api/producao-completa/cancelar")
def cancelar_producao_completa():
    orchestrator.cancelar()
    return {"ok": True}


# === API: CHAT (CLAUDE CLI) ===

AGENTS_DIR = BASE_DIR / "agents"

@app.get("/api/chat/instructions")
def obter_instrucoes_chat(agent: str = "temas"):
    """Retorna o CLAUDE.md do agente selecionado."""
    claude_md = AGENTS_DIR / agent / "CLAUDE.md"
    if claude_md.exists():
        return {"instrucoes": claude_md.read_text(encoding="utf-8"), "agent": agent}
    return {"instrucoes": "", "agent": agent}


@app.put("/api/chat/instructions")
async def salvar_instrucoes_chat(request: Request):
    """Salva o CLAUDE.md do agente selecionado."""
    dados = await request.json()
    agent = dados.get("agent", "temas")
    claude_md = AGENTS_DIR / agent / "CLAUDE.md"
    claude_md.parent.mkdir(parents=True, exist_ok=True)
    claude_md.write_text(dados.get("instrucoes", ""), encoding="utf-8")
    return {"ok": True}


CHAT_HISTORICO_FILE = AGENTS_DIR / "temas" / "historico.json"


def _carregar_chat_historico() -> list:
    if CHAT_HISTORICO_FILE.exists():
        with open(CHAT_HISTORICO_FILE, "r", encoding="utf-8") as f:
            return json.load(f)
    return []


def _salvar_chat_historico(historico: list):
    CHAT_HISTORICO_FILE.parent.mkdir(parents=True, exist_ok=True)
    with open(CHAT_HISTORICO_FILE, "w", encoding="utf-8") as f:
        json.dump(historico[-100:], f, ensure_ascii=False, indent=2)  # max 100 msgs


@app.get("/api/chat/history")
def obter_chat_historico():
    return _carregar_chat_historico()


@app.delete("/api/chat/history")
def limpar_chat_historico():
    _salvar_chat_historico([])
    return {"ok": True}


@app.post("/api/chat")
async def chat_claude_cli(request: Request):
    dados = await request.json()
    prompt = dados.get("prompt", "")
    agent = dados.get("agent", "temas")
    if not prompt:
        raise HTTPException(400, "Prompt é obrigatório")

    # Salvar mensagem do usuário no histórico
    historico = _carregar_chat_historico()
    historico.append({"role": "user", "text": prompt, "ts": datetime.now().isoformat(), "agent": agent})

    agent_dir = str(AGENTS_DIR / agent)
    Path(agent_dir).mkdir(parents=True, exist_ok=True)
    claude_cmd = os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")
    if not os.path.exists(claude_cmd):
        claude_cmd = "claude"

    # Usar --continue para manter a sessão (Claude CLI lembra o contexto)
    # Na primeira mensagem não tem sessão, então --continue falha silenciosamente e cria nova
    cmd = [claude_cmd, "-p", "--continue", "--output-format", "text", prompt]

    try:
        import subprocess as sp
        env = dict(os.environ)
        for key in list(env.keys()):
            if "CLAUDE" in key.upper():
                del env[key]
        proc = sp.run(
            cmd, capture_output=True, text=True, timeout=120,
            encoding="utf-8", errors="replace",
            cwd=agent_dir, shell=True, env=env,
        )
        resposta = proc.stdout.strip() if proc.returncode == 0 else f"Erro: {proc.stderr.strip()}"

        # Salvar resposta no histórico
        historico.append({"role": "assistant", "text": resposta, "ts": datetime.now().isoformat()})
        _salvar_chat_historico(historico)

        return {"resposta": resposta}
    except Exception as e:
        return {"resposta": f"Erro: {e}"}


# === API: CONFIG ===

@app.get("/api/config")
def obter_config():
    config = scriptwriter.carregar_config()
    safe = {}
    for k, v in config.items():
        if "key" in k.lower() and isinstance(v, str) and len(v) > 8:
            safe[k] = "..." + v[-4:]
        else:
            safe[k] = v
    return safe


@app.put("/api/config")
async def salvar_config_endpoint(request: Request):
    dados = await request.json()
    config = scriptwriter.carregar_config()
    for k, v in dados.items():
        if "key" in k.lower() and isinstance(v, str) and v.startswith("..."):
            continue
        config[k] = v
    scriptwriter.salvar_config(config)
    return {"ok": True}


# === INTERFACE WEB ===

DASHBOARD_HTML = """<!DOCTYPE html>
<html lang="pt-BR">
<head>
<meta charset="UTF-8">
<meta name="viewport" content="width=device-width, initial-scale=1.0">
<title>Video Automator</title>
<link rel="icon" href="data:image/svg+xml,<svg xmlns='http://www.w3.org/2000/svg' viewBox='0 0 100 100'><rect width='100' height='100' rx='20' fill='%230d1117'/><rect x='8' y='8' width='84' height='84' rx='14' fill='%23161b22' stroke='%232ecc71' stroke-width='3'/><polygon points='38,25 38,75 78,50' fill='%232ecc71'/></svg>">
<style>
* { margin:0; padding:0; box-sizing:border-box; }
:root {
  --bg: #0d1117; --panel: #161b22; --border: #21262d;
  --accent: #2ecc71; --accent-hover: #27ae60; --accent-dim: #1a3a2a;
  --text: #e6edf3; --text-sec: #8b949e; --danger: #f85149;
  --warn: #d29922; --info: #58a6ff;
}
body { font-family: -apple-system,BlinkMacSystemFont,'Segoe UI',sans-serif; background:var(--bg); color:var(--text); display:flex; height:100vh; overflow:hidden; }

/* SIDEBAR */
.sidebar { width:220px; background:var(--panel); border-right:1px solid var(--border); display:flex; flex-direction:column; flex-shrink:0; }
.sidebar-header { padding:20px 16px; border-bottom:1px solid var(--border); }
.sidebar-header h1 { font-size:16px; color:var(--accent); letter-spacing:0.5px; }
.sidebar-header span { font-size:11px; color:var(--text-sec); }
.sidebar-nav { flex:1; padding:8px 0; }
.sidebar-nav a { display:flex; align-items:center; gap:10px; padding:10px 16px; color:var(--text-sec); text-decoration:none; font-size:14px; transition:all .15s; cursor:pointer; }
.sidebar-nav a:hover { color:var(--text); background:var(--border); }
.sidebar-nav a.active { color:var(--accent); background:var(--accent-dim); border-right:2px solid var(--accent); }
.sidebar-nav a svg { width:18px; height:18px; flex-shrink:0; }

/* MAIN */
.main { flex:1; overflow-y:auto; padding:24px 32px; }
.page { display:none; }
.page.active { display:block; }
.page-header { display:flex; align-items:center; justify-content:space-between; margin-bottom:24px; }
.page-header h2 { font-size:22px; font-weight:600; }

/* BUTTONS */
.btn { padding:8px 16px; border:none; border-radius:6px; font-size:13px; font-weight:500; cursor:pointer; transition:all .15s; display:inline-flex; align-items:center; gap:6px; }
.btn-primary { background:var(--accent); color:#000; }
.btn-primary:hover { background:var(--accent-hover); }
.btn-secondary { background:var(--border); color:var(--text); }
.btn-secondary:hover { background:#30363d; }
.btn-danger { background:var(--danger); color:#fff; }
.btn-danger:hover { background:#da3633; }
.btn-sm { padding:5px 10px; font-size:12px; }

/* CARDS GRID */
.cards-grid { display:grid; grid-template-columns:repeat(auto-fill,minmax(300px,1fr)); gap:16px; }
.card { background:var(--panel); border:1px solid var(--border); border-radius:8px; padding:16px; transition:border-color .15s; cursor:pointer; }
.card:hover { border-color:var(--accent); }
.card-header { display:flex; justify-content:space-between; align-items:flex-start; margin-bottom:12px; }
.card-title { font-size:15px; font-weight:600; }
.card-tag { background:var(--accent-dim); color:var(--accent); padding:2px 8px; border-radius:4px; font-size:11px; font-weight:600; }
.card-info { font-size:12px; color:var(--text-sec); line-height:1.8; }
.card-actions { display:flex; gap:8px; margin-top:12px; padding-top:12px; border-top:1px solid var(--border); }

/* FORMS */
.form-group { margin-bottom:16px; }
.form-group label { display:block; font-size:13px; color:var(--text-sec); margin-bottom:6px; font-weight:500; }
.form-row { display:grid; grid-template-columns:1fr 1fr; gap:16px; }
.form-row-3 { display:grid; grid-template-columns:1fr 1fr 1fr; gap:16px; }
input[type=text], input[type=number], select, textarea {
  width:100%; padding:8px 12px; background:var(--bg); border:1px solid var(--border);
  border-radius:6px; color:var(--text); font-size:13px; font-family:inherit;
}
input:focus, select:focus, textarea:focus { outline:none; border-color:var(--accent); }
textarea { resize:vertical; min-height:80px; }
.input-with-btn { display:flex; gap:8px; }
.input-with-btn input { flex:1; }

/* MODAL */
.modal-overlay { position:fixed; top:0; left:0; right:0; bottom:0; background:rgba(0,0,0,.6); display:none; justify-content:center; align-items:flex-start; z-index:1000; padding:40px; overflow-y:auto; }
.modal-overlay.active { display:flex; }
.modal { background:var(--panel); border:1px solid var(--border); border-radius:10px; width:100%; max-width:720px; }
.modal-header { display:flex; justify-content:space-between; align-items:center; padding:16px 20px; border-bottom:1px solid var(--border); }
.modal-header h3 { font-size:17px; }
.modal-close { background:none; border:none; color:var(--text-sec); font-size:22px; cursor:pointer; padding:4px 8px; }
.modal-close:hover { color:var(--text); }
.modal-body { padding:20px; max-height:calc(100vh - 200px); overflow-y:auto; }
.modal-footer { padding:12px 20px; border-top:1px solid var(--border); display:flex; justify-content:flex-end; gap:8px; }

/* TABS */
.tabs { display:flex; gap:0; border-bottom:1px solid var(--border); margin-bottom:20px; }
.tab { padding:10px 16px; font-size:13px; color:var(--text-sec); cursor:pointer; border-bottom:2px solid transparent; transition:all .15s; background:none; border-top:none; border-left:none; border-right:none; }
.tab:hover { color:var(--text); }
.tab.active { color:var(--accent); border-bottom-color:var(--accent); }
.tab-content { display:none; }
.tab-content.active { display:block; }

/* SLIDER */
.slider-group { display:flex; align-items:center; gap:12px; }
.slider-group input[type=range] { flex:1; accent-color:var(--accent); }
.slider-val { font-size:13px; color:var(--accent); min-width:40px; text-align:right; }

/* TEMAS GRID */
.temas-grid { width:100%; border-collapse:collapse; min-width:600px; }
.temas-grid th { background:var(--panel); padding:8px 12px; font-size:12px; font-weight:600; color:var(--text); border:1px solid var(--border); cursor:grab; user-select:none; min-width:160px; position:relative; }
.temas-grid th:first-child { min-width:100px; cursor:default; }
.temas-grid th .col-actions { position:absolute; top:2px; right:4px; display:none; }
.temas-grid th:hover .col-actions { display:flex; gap:2px; }
.temas-grid td { padding:0; border:1px solid var(--border); vertical-align:top; position:relative; }
.temas-grid td:first-child { background:var(--panel); padding:8px 10px; font-size:12px; font-weight:600; color:var(--text); min-width:100px; cursor:grab; }
.tema-cell { padding:8px 10px; min-height:60px; cursor:pointer; transition:background .15s; font-size:12px; color:var(--text-sec); }
.tema-cell:hover { background:rgba(46,204,113,0.05); }
.tema-cell .tc-titulo { font-size:12px; color:var(--text); font-weight:500; line-height:1.3; margin-bottom:4px; }
.tema-cell .tc-thumb { font-size:11px; color:var(--accent); font-style:italic; }
.tema-cell .tc-empty { color:var(--text-sec); opacity:0.4; font-size:11px; }
.tema-cell .tc-status { position:absolute; top:4px; right:4px; width:8px; height:8px; border-radius:50%; }
.tc-status.synced { background:var(--accent); }
.tc-status.pending { background:var(--warn); }
.temas-grid .row-actions { display:none; position:absolute; right:-24px; top:50%; transform:translateY(-50%); }
.temas-grid tr:hover .row-actions { display:block; }
.dragging { opacity:0.5; }

/* BATCH TABLE */
.batch-table { width:100%; border-collapse:collapse; }
.batch-table th { text-align:left; padding:10px 12px; font-size:12px; color:var(--text-sec); font-weight:500; border-bottom:1px solid var(--border); }
.batch-table td { padding:10px 12px; border-bottom:1px solid var(--border); font-size:13px; vertical-align:middle; }
.batch-table tr:hover { background:rgba(255,255,255,.02); }

/* PROGRESS BAR */
.progress-bar { background:var(--border); border-radius:4px; height:8px; overflow:hidden; min-width:120px; }
.progress-fill { height:100%; background:var(--accent); border-radius:4px; transition:width .3s; }

/* PROGRESS CIRCLE (rosca) */
.progress-circle { position:relative; width:52px; height:52px; display:inline-block; }
.progress-circle svg { transform:rotate(-90deg); width:52px; height:52px; }
.progress-circle .bg { fill:none; stroke:var(--border); stroke-width:4; }
.progress-circle .fg { fill:none; stroke:var(--accent); stroke-width:4; stroke-linecap:round; transition:stroke-dashoffset .4s ease; }
.progress-circle .pct { position:absolute; top:0; left:0; width:100%; height:100%; display:flex; align-items:center; justify-content:center; font-size:11px; font-weight:700; color:var(--accent); }

/* STATUS BADGES */
.badge { padding:3px 8px; border-radius:4px; font-size:11px; font-weight:600; max-width:200px; white-space:nowrap; overflow:hidden; text-overflow:ellipsis; display:inline-block; }
.badge-waiting { background:#21262d; color:var(--text-sec); }
.badge-transcribing { background:#1a2332; color:var(--info); }
.badge-fixing { background:#2a2000; color:var(--warn); }
.badge-encoding { background:var(--accent-dim); color:var(--accent); }
.badge-done { background:#1a3a2a; color:var(--accent); }
.badge-error { background:#3a1a1a; color:var(--danger); }
.badge-cancelled { background:#21262d; color:var(--text-sec); }

/* FILE BROWSER MODAL */
.file-list { max-height:350px; overflow-y:auto; border:1px solid var(--border); border-radius:6px; }
.file-item { display:flex; align-items:center; gap:10px; padding:8px 12px; cursor:pointer; font-size:13px; border-bottom:1px solid var(--border); }
.file-item:hover { background:var(--border); }
.file-item.folder { color:var(--info); }
.file-item.file { color:var(--text-sec); }
.file-breadcrumb { display:flex; gap:4px; margin-bottom:8px; flex-wrap:wrap; font-size:13px; }
.file-breadcrumb span { color:var(--info); cursor:pointer; }
.file-breadcrumb span:hover { text-decoration:underline; }

/* PRESET PREVIEW */
.preset-preview { background:var(--bg); border:1px solid var(--border); border-radius:6px; padding:20px; text-align:center; margin-top:12px; min-height:80px; display:flex; align-items:center; justify-content:center; }

/* TOAST */
.toast { position:fixed; bottom:24px; right:24px; padding:12px 20px; border-radius:8px; font-size:13px; z-index:9999; animation:slideIn .3s ease; }
.toast-success { background:var(--accent); color:#000; }
.toast-error { background:var(--danger); color:#fff; }
@keyframes slideIn { from{transform:translateY(20px);opacity:0} to{transform:translateY(0);opacity:1} }
@keyframes spin { to { transform:rotate(360deg); } }

/* EMPTY STATE */
.empty-state { text-align:center; padding:60px 20px; color:var(--text-sec); }
.empty-state svg { width:64px; height:64px; margin-bottom:16px; opacity:.4; }
.empty-state h3 { font-size:16px; margin-bottom:8px; color:var(--text); }

/* COLOR PICKER */
input[type=color] { width:48px; height:32px; padding:2px; border:1px solid var(--border); border-radius:4px; background:var(--bg); cursor:pointer; }
.color-row { display:flex; align-items:center; gap:8px; }
.color-row label { min-width:0; margin:0; }
.checkbox-row { display:flex; align-items:center; gap:8px; margin-bottom:8px; }
.checkbox-row input[type=checkbox] { accent-color:var(--accent); width:16px; height:16px; }
.checkbox-row label { font-size:13px; color:var(--text-sec); margin:0; cursor:pointer; }

/* OVERLAY PREVIEW */
.overlay-preview-area { background:var(--bg); border:1px solid var(--border); border-radius:8px; padding:0; margin-top:16px; aspect-ratio:16/9; position:relative; overflow:hidden; display:flex; align-items:center; justify-content:center; }
.overlay-preview-area img { position:absolute; top:0; left:0; width:100%; height:100%; object-fit:cover; }
.overlay-preview-area .ov-label { position:absolute; bottom:8px; right:10px; font-size:11px; color:var(--text-sec); background:rgba(0,0,0,.6); padding:2px 8px; border-radius:4px; }

/* KEN BURNS PREVIEW */
.kenburns-preview { background:var(--bg); border:1px solid var(--border); border-radius:8px; margin-top:16px; aspect-ratio:16/9; position:relative; overflow:hidden; }
.kenburns-preview img { width:100%; height:100%; object-fit:cover; transform-origin:center center; }
.kenburns-preview .kb-label { position:absolute; bottom:8px; right:10px; font-size:11px; color:var(--text-sec); background:rgba(0,0,0,.6); padding:2px 8px; border-radius:4px; z-index:2; }
@keyframes kenburns { from { transform:scale(1.0); } to { transform:scale(var(--kb-zoom,1.04)); } }

/* AUDIO PREVIEW */
.audio-preview-row { display:flex; align-items:center; gap:12px; margin-top:12px; }
.audio-preview-row .btn { flex-shrink:0; }
.audio-preview-row span { font-size:12px; color:var(--text-sec); }
</style>
</head>
<body>

<!-- SIDEBAR -->
<nav class="sidebar">
  <div class="sidebar-header">
    <h1>VIDEO AUTOMATOR</h1>
    <span>Produção automatizada</span>
  </div>
  <div class="sidebar-nav">
    <a data-page="temas" class="active" onclick="showPage('temas')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0l-.548.547A3.374 3.374 0 0014 18.469V19a2 2 0 11-4 0v-.531c0-.895-.356-1.754-.988-2.386l-.548-.547z"/></svg>
      Temas
    </a>
    <a data-page="roteiros" onclick="showPage('roteiros')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14,2 14,8 20,8"/><line x1="16" y1="13" x2="8" y2="13"/><line x1="16" y1="17" x2="8" y2="17"/></svg>
      Roteiros
    </a>
    <a data-page="narracao" onclick="showPage('narracao')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M12 1a3 3 0 00-3 3v8a3 3 0 006 0V4a3 3 0 00-3-3z"/><path d="M19 10v2a7 7 0 01-14 0v-2"/><line x1="12" y1="19" x2="12" y2="23"/><line x1="8" y1="23" x2="16" y2="23"/></svg>
      Narração
    </a>
    <a data-page="thumbnail" onclick="showPage('thumbnail')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21,15 16,10 5,21"/></svg>
      Thumbnail
    </a>
    <a data-page="templates" onclick="showPage('templates')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><rect x="3" y="3" width="7" height="7"/><rect x="14" y="3" width="7" height="7"/><rect x="3" y="14" width="7" height="7"/><rect x="14" y="14" width="7" height="7"/></svg>
      Templates
    </a>
    <a data-page="batch" onclick="showPage('batch')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><polygon points="5,3 19,12 5,21"/></svg>
      Produção
    </a>
    <a data-page="historico" onclick="showPage('historico')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="10"/><polyline points="12,6 12,12 16,14"/></svg>
      Histórico
    </a>
    <a data-page="monitor" onclick="showPage('monitor')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><path d="M22 12h-4l-3 9L9 3l-3 9H2"/></svg>
      Monitor
    </a>
    <a data-page="config" onclick="showPage('config')">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="2"><circle cx="12" cy="12" r="3"/><path d="M19.4 15a1.65 1.65 0 00.33 1.82l.06.06a2 2 0 01-2.83 2.83l-.06-.06a1.65 1.65 0 00-1.82-.33 1.65 1.65 0 00-1 1.51V21a2 2 0 01-4 0v-.09A1.65 1.65 0 009 19.4a1.65 1.65 0 00-1.82.33l-.06.06a2 2 0 01-2.83-2.83l.06-.06A1.65 1.65 0 004.68 15a1.65 1.65 0 00-1.51-1H3a2 2 0 010-4h.09A1.65 1.65 0 004.6 9a1.65 1.65 0 00-.33-1.82l-.06-.06a2 2 0 012.83-2.83l.06.06A1.65 1.65 0 009 4.68a1.65 1.65 0 001-1.51V3a2 2 0 014 0v.09a1.65 1.65 0 001 1.51 1.65 1.65 0 001.82-.33l.06-.06a2 2 0 012.83 2.83l-.06.06A1.65 1.65 0 0019.4 9a1.65 1.65 0 001.51 1H21a2 2 0 010 4h-.09a1.65 1.65 0 00-1.51 1z"/></svg>
      Config
    </a>
  </div>
</nav>

<!-- MAIN CONTENT -->
<div class="main">

  <!-- PAGE: TEMPLATES -->
  <div id="page-templates" class="page">
    <div class="page-header">
      <h2>Templates</h2>
      <button class="btn btn-primary" onclick="abrirEditor()">+ Novo Template</button>
    </div>
    <div id="templates-grid" class="cards-grid"></div>
    <div id="templates-empty" class="empty-state" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><line x1="12" y1="8" x2="12" y2="16"/><line x1="8" y1="12" x2="16" y2="12"/></svg>
      <h3>Nenhum template criado</h3>
      <p>Crie seu primeiro template para começar a produzir vídeos.</p>
    </div>
  </div>

  <!-- PAGE: BATCH -->
  <div id="page-batch" class="page">
    <div class="page-header">
      <h2>Produção em Lote</h2>
      <div style="display:flex;gap:8px">
        <button class="btn btn-secondary btn-sm" onclick="limparTodosAudios()" style="font-size:11px">Limpar Áudios</button>
        <button id="btn-batch-start" class="btn btn-primary" onclick="iniciarBatch()">Iniciar Produção</button>
        <button id="btn-batch-cancel" class="btn btn-danger" onclick="cancelarBatch()" style="display:none">Cancelar</button>
      </div>
    </div>
    <div id="batch-progress-global" style="display:none;margin-bottom:20px">
      <div style="display:flex;justify-content:space-between;margin-bottom:6px">
        <span style="font-size:13px;color:var(--text-sec)">Progresso geral</span>
        <div style="display:flex;gap:16px;align-items:center">
          <span id="batch-timer-total" style="font-size:14px;color:var(--text);font-family:monospace;font-weight:600">00:00:00</span>
          <span id="batch-progress-text" style="font-size:13px;color:var(--accent)">0%</span>
        </div>
      </div>
      <div class="progress-bar" style="height:12px"><div id="batch-progress-fill" class="progress-fill" style="width:0%"></div></div>
    </div>
    <table class="batch-table">
      <thead><tr><th style="width:30px">#</th><th>Template</th><th>Narração (MP3)</th><th style="width:50px"></th><th style="width:140px">Status</th><th style="width:80px">Progresso</th><th style="width:90px">Tempo</th></tr></thead>
      <tbody id="batch-tbody"></tbody>
    </table>
    <div id="batch-empty" class="empty-state">
      <p>Adicione templates primeiro para configurar a produção.</p>
    </div>
  </div>

  <!-- PAGE: RULES -->
  <div id="page-rules" class="page">
    <div class="page-header">
      <h2>Regras de Correção</h2>
    </div>
    <div class="tabs" id="rules-tabs"></div>
    <div id="rules-content"></div>
  </div>

  <!-- PAGE: HISTÓRICO -->
  <div id="page-historico" class="page">
    <div class="page-header">
      <h2>Histórico de Produção</h2>
      <button class="btn btn-danger btn-sm" onclick="limparHistorico()">Limpar Histórico</button>
    </div>
    <table class="batch-table" id="historico-table">
      <thead><tr>
        <th style="width:140px">Data</th>
        <th style="width:60px">Tag</th>
        <th>Template</th>
        <th>Arquivo de Saída</th>
        <th style="width:80px">Tempo</th>
        <th style="width:80px">Status</th>
      </tr></thead>
      <tbody id="historico-tbody"></tbody>
    </table>
    <div id="historico-empty" class="empty-state" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><circle cx="12" cy="12" r="10"/><polyline points="12,6 12,12 16,14"/></svg>
      <h3>Nenhum vídeo produzido ainda</h3>
      <p>O histórico aparecerá aqui após produzir vídeos.</p>
    </div>
  </div>

  <!-- PAGE: MONITOR -->
  <div id="page-monitor" class="page">
    <div class="page-header">
      <h2>Monitor de Producao</h2>
      <div style="display:flex;align-items:center;gap:12px">
        <span id="mon-data-ref" style="font-size:13px;font-weight:600;color:var(--accent)"></span>
        <span id="mon-timer-global" style="font-size:14px;font-family:monospace;color:var(--text)">00:00:00</span>
        <span id="mon-uptime" style="font-size:11px;color:var(--text-sec)">Uptime: --:--:--</span>
        <span style="font-size:10px;color:var(--text-sec)">Auto-refresh: 3s</span>
      </div>
    </div>

    <!-- PROGRESS OVERVIEW -->
    <div id="mon-progress-panel" style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:16px;margin-bottom:8px">
        <span id="mon-status-label" style="font-size:14px;font-weight:600;color:var(--text)">Nenhuma producao registrada</span>
        <span id="mon-count-label" style="font-size:13px;color:var(--text-sec)"></span>
      </div>
      <div class="progress-bar" style="height:12px;margin-bottom:10px"><div id="mon-progress-fill" class="progress-fill" style="width:0%;transition:width 0.3s"></div></div>
      <div style="display:flex;gap:20px;font-size:12px">
        <span style="color:#4ade80" id="mon-sum-ok">0 concluidos</span>
        <span style="color:#f87171" id="mon-sum-erros">0 erros</span>
        <span style="color:#60a5fa" id="mon-sum-pulados">0 pulados</span>
        <span style="color:#facc15" id="mon-sum-processando">0 processando</span>
        <span style="color:var(--text-sec)" id="mon-sum-aguardando">0 aguardando</span>
      </div>
    </div>

    <!-- CANAL CARDS GRID -->
    <div id="mon-canal-grid" style="display:grid;grid-template-columns:repeat(auto-fill,minmax(320px,1fr));gap:12px;margin-bottom:16px"></div>

    <!-- LOG PANEL -->
    <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px">
      <h3 style="font-size:13px;margin-bottom:8px;color:var(--text-sec)">Log de Producao</h3>
      <div id="mon-log-panel" style="max-height:240px;overflow-y:auto;font-size:11px;font-family:monospace;color:var(--text-sec);background:var(--bg);border-radius:6px;padding:10px"></div>
    </div>

    <!-- GENERAL STATUS CARDS (batch, narracao, pipeline, credits) -->
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px">
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px">
        <div style="font-size:10px;color:var(--text-sec);margin-bottom:4px">Batch Video</div>
        <span id="mon-batch-badge" class="badge badge-waiting">Inativo</span>
      </div>
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px">
        <div style="font-size:10px;color:var(--text-sec);margin-bottom:4px">Narracao</div>
        <span id="mon-narr-badge" class="badge badge-waiting">Inativo</span>
      </div>
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px">
        <div style="font-size:10px;color:var(--text-sec);margin-bottom:4px">Pipeline</div>
        <span id="mon-pipe-badge" class="badge badge-waiting">Inativo</span>
      </div>
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px">
        <div style="font-size:10px;color:var(--text-sec);margin-bottom:4px">Creditos TTS</div>
        <div id="mon-credits" style="font-size:20px;font-weight:700;color:var(--accent)">--</div>
      </div>
    </div>

    <!-- STATS + DISCO -->
    <div style="display:grid;grid-template-columns:repeat(4,1fr);gap:12px;margin-bottom:16px">
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px;text-align:center">
        <div style="font-size:24px;font-weight:700;color:var(--accent)" id="mon-stat-hoje">0</div>
        <div style="font-size:10px;color:var(--text-sec)">Videos hoje</div>
      </div>
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px;text-align:center">
        <div style="font-size:24px;font-weight:700;color:var(--info)" id="mon-stat-semana">0</div>
        <div style="font-size:10px;color:var(--text-sec)">Esta semana</div>
      </div>
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px;text-align:center">
        <div style="font-size:24px;font-weight:700;color:var(--text)" id="mon-stat-total">0</div>
        <div style="font-size:10px;color:var(--text-sec)">Total produzidos</div>
      </div>
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:14px;text-align:center">
        <div style="font-size:24px;font-weight:700;color:var(--warn)" id="mon-stat-tempo">0s</div>
        <div style="font-size:10px;color:var(--text-sec)">Tempo medio</div>
      </div>
    </div>

    <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:16px">
      <h3 style="font-size:13px;margin-bottom:8px;color:var(--text-sec)">Uso de Disco</h3>
      <div id="mon-disco" style="display:flex;gap:16px;flex-wrap:wrap"></div>
    </div>
  </div>

  <!-- PAGE: NARRAÇÃO -->
  <div id="page-narracao" class="page">
    <div class="page-header">
      <h2>Narração</h2>
      <div style="display:flex;align-items:center;gap:16px">
        <span style="font-size:12px;color:var(--text-sec)">Créditos:</span>
        <span id="narr-credits" style="font-size:14px;font-weight:600;color:var(--accent)">--</span>
        <button class="btn btn-secondary btn-sm" onclick="carregarVozes()" style="font-size:10px">Atualizar vozes</button>
      </div>
    </div>

    <!-- GERADOR INDIVIDUAL (compacto) -->
    <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:20px">
      <div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">
        <label style="font-size:11px;color:var(--text-sec)">Puxar roteiro:</label>
        <select id="narr-pull-data" style="font-size:11px;padding:3px 6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
          <option value="">Data...</option>
        </select>
        <select id="narr-pull-canal" style="font-size:11px;padding:3px 6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
          <option value="">Canal...</option>
        </select>
        <button class="btn btn-secondary btn-sm" style="font-size:10px;padding:2px 8px" onclick="puxarRoteiroNarracao()">Puxar</button>
      </div>
      <div style="display:grid;grid-template-columns:1fr 200px 200px auto;gap:12px;align-items:end">
        <div class="form-group" style="margin:0">
          <label style="font-size:11px">Texto</label>
          <textarea id="narr-texto" rows="3" placeholder="Cole o roteiro ou puxe de uma célula..." style="font-size:12px" oninput="document.getElementById('narr-char-count').textContent=this.value.length+' chars'"></textarea>
          <span id="narr-char-count" style="font-size:10px;color:var(--text-sec)">0 chars</span>
        </div>
        <div>
          <div class="form-group" style="margin:0 0 6px">
            <label style="font-size:11px">Provedor</label>
            <select id="narr-provider" onchange="filtrarVozes()" style="font-size:11px;padding:5px">
              <option value="all">Todos</option>
              <option value="minimax_clone">Minimax (Clonadas)</option>
              <option value="minimax">Minimax (Banco)</option>
              <option value="elevenlabs">ElevenLabs</option>
              <option value="elevenlabs_shared">ElevenLabs (Library)</option>
              <option value="elevenlabs_fav">ElevenLabs (Fav)</option>
            </select>
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:11px">Voz</label>
            <select id="narr-voice" style="font-size:11px;padding:5px">
              <option value="">Carregando...</option>
            </select>
          </div>
        </div>
        <div>
          <div class="form-group" style="margin:0 0 6px">
            <label style="font-size:11px">Saída</label>
            <input type="text" id="narr-nome" placeholder="nome_arquivo" style="font-size:11px;padding:5px">
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:11px">Pasta</label>
            <div class="input-with-btn">
              <input type="text" id="narr-pasta-saida" placeholder="narracoes/" style="font-size:11px;padding:5px">
              <button class="btn btn-secondary btn-sm" style="font-size:10px;padding:3px 6px" onclick="abrirBrowser('narr-pasta-saida','folder')">...</button>
            </div>
          </div>
        </div>
        <div style="display:flex;flex-direction:column;gap:4px">
          <button class="btn btn-primary btn-sm" onclick="gerarNarracao(false)" id="narr-gerar-btn" style="white-space:nowrap">Gerar e Salvar</button>
          <button class="btn btn-secondary btn-sm" onclick="gerarNarracao(true)" id="narr-preview-btn" style="white-space:nowrap;font-size:10px">Ouvir (preview)</button>
        </div>
      </div>
      <!-- Progress inline -->
      <div id="narr-status-panel" style="display:none;margin-top:10px">
        <div style="display:flex;align-items:center;gap:12px">
          <div class="progress-bar" style="flex:1;height:6px"><div id="narr-progress-fill" class="progress-fill" style="width:0%"></div></div>
          <span id="narr-status-badge" class="badge badge-transcribing" style="font-size:10px">Processando</span>
        </div>
      </div>
    </div>

    <!-- HISTÓRICO DE NARRAÇÕES -->
    <div style="margin-bottom:20px">
      <h3 style="font-size:15px;margin-bottom:12px">Histórico</h3>
      <div id="narr-historico" style="display:flex;flex-direction:column;gap:4px"></div>
      <div id="narr-hist-empty" style="color:var(--text-sec);font-size:12px;text-align:center;padding:16px">Nenhuma narração gerada ainda</div>
    </div>

    <!-- NARRAÇÃO EM LOTE -->
    <div>
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap">
        <h3 style="font-size:15px;margin:0">Narração em Lote</h3>
        <div style="display:flex;gap:6px;align-items:center">
          <label style="font-size:11px;color:var(--text-sec)">Data:</label>
          <input type="date" id="narr-batch-data" style="font-size:11px;padding:3px 6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
        </div>
        <div style="display:flex;gap:6px;align-items:center">
          <label style="font-size:11px;color:var(--text-sec)">Pasta:</label>
          <div class="input-with-btn">
            <input type="text" id="narr-batch-pasta" value="narracoes/" placeholder="Pasta de saída..." style="font-size:11px;padding:3px 6px;width:180px">
            <button class="btn btn-secondary btn-sm" style="font-size:10px;padding:2px 5px" onclick="abrirBrowser('narr-batch-pasta','folder')">...</button>
          </div>
        </div>
        <button class="btn btn-secondary btn-sm" onclick="puxarRoteirosBatch()" style="font-size:11px">Puxar Roteiros</button>
        <button class="btn btn-primary btn-sm" onclick="iniciarBatchNarracao()" id="btn-narr-batch-start" style="font-size:11px">Gerar Todos</button>
        <button class="btn btn-danger btn-sm" onclick="cancelarBatchNarracao()" id="btn-narr-batch-cancel" style="font-size:11px;display:none">Cancelar Tudo</button>
      </div>
      <div id="narr-batch-cards" class="cards-grid"></div>
      <div id="narr-batch-status" style="display:none;margin-top:8px;background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:10px">
        <div style="display:flex;align-items:center;gap:12px">
          <div class="progress-bar" style="flex:1;height:6px"><div id="narr-batch-fill" class="progress-fill" style="width:0%"></div></div>
          <span id="narr-batch-timer-total" style="font-size:12px;color:var(--text);font-family:monospace;font-weight:600">00:00</span>
          <span id="narr-batch-count" style="font-size:11px;color:var(--accent)">0/0</span>
        </div>
        <div id="narr-batch-log" style="margin-top:6px;font-size:10px;color:var(--text-sec);max-height:80px;overflow-y:auto"></div>
      </div>
    </div>
  </div>

  <!-- PAGE: THUMBNAIL -->
  <div id="page-thumbnail" class="page">
    <div class="page-header">
      <h2>Thumbnail</h2>
      <div style="display:flex;align-items:center;gap:12px">
        <div style="display:flex;gap:6px;align-items:center">
          <label style="font-size:11px;color:var(--text-sec)">Data:</label>
          <input type="date" id="thumb-batch-data" style="font-size:11px;padding:3px 6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
        </div>
        <button class="btn btn-secondary btn-sm" onclick="puxarTextosThumb()" style="font-size:11px">Puxar Textos</button>
        <button class="btn btn-primary btn-sm" onclick="gerarTodasThumbs()" id="btn-thumb-gerar-todas" style="font-size:11px">Gerar Todas</button>
      </div>
    </div>

    <!-- CONFIG DE TEXTO (shared) -->
    <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:12px 16px;margin-bottom:16px">
      <div style="display:flex;align-items:center;gap:8px;margin-bottom:8px;cursor:pointer" onclick="this.parentElement.querySelector('.thumb-cfg-body').style.display=this.parentElement.querySelector('.thumb-cfg-body').style.display==='none'?'block':'none'; this.querySelector('.cfg-arrow').textContent=this.parentElement.querySelector('.thumb-cfg-body').style.display==='none'?'\\u25B6':'\\u25BC'">
        <span class="cfg-arrow" style="font-size:10px;color:var(--text-sec)">\\u25BC</span>
        <span style="font-size:13px;font-weight:600">Configuracao de Texto</span>
      </div>
      <div class="thumb-cfg-body">
        <div style="display:grid;grid-template-columns:1fr 1fr 1fr 1fr 1fr 1fr;gap:12px;align-items:end">
          <div class="form-group" style="margin:0">
            <label style="font-size:10px">Fonte</label>
            <select id="thumb-cfg-font" style="font-size:11px;padding:4px" onchange="thumbCfgChanged()">
              <option>Arial Black</option>
              <option>Impact</option>
              <option>Arial</option>
              <option>Segoe UI</option>
            </select>
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:10px">Tamanho</label>
            <input type="number" id="thumb-cfg-size" value="72" min="20" max="200" style="font-size:11px;padding:4px" onchange="thumbCfgChanged()">
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:10px">Cor do Texto</label>
            <div class="color-row">
              <input type="color" id="thumb-cfg-color" value="#FFFFFF" onchange="thumbCfgChanged()">
              <span style="font-size:10px;color:var(--text-sec)" id="thumb-cfg-color-hex">#FFFFFF</span>
            </div>
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:10px">Cor Contorno</label>
            <div class="color-row">
              <input type="color" id="thumb-cfg-outline-color" value="#000000" onchange="thumbCfgChanged()">
              <input type="number" id="thumb-cfg-outline-w" value="4" min="0" max="20" style="font-size:11px;padding:4px;width:50px" onchange="thumbCfgChanged()">
            </div>
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:10px">Posicao</label>
            <select id="thumb-cfg-position" style="font-size:11px;padding:4px" onchange="thumbCfgChanged()">
              <option value="top">Topo</option>
              <option value="center" selected>Centro</option>
              <option value="bottom">Rodape</option>
            </select>
          </div>
          <div class="form-group" style="margin:0">
            <label style="font-size:10px">Sombra</label>
            <div class="checkbox-row">
              <input type="checkbox" id="thumb-cfg-shadow" checked onchange="thumbCfgChanged()">
              <label for="thumb-cfg-shadow" style="font-size:11px">Ativa</label>
            </div>
          </div>
        </div>
      </div>
    </div>

    <!-- CARDS DOS TEMPLATES -->
    <div id="thumb-batch-cards" class="cards-grid"></div>
    <div id="thumb-empty" class="empty-state" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><rect x="3" y="3" width="18" height="18" rx="2"/><circle cx="8.5" cy="8.5" r="1.5"/><polyline points="21,15 16,10 5,21"/></svg>
      <h3>Nenhum template configurado</h3>
      <p>Crie templates na aba Templates para gerar thumbnails.</p>
    </div>

    <!-- YOUTUBE EXTRACTOR -->
    <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:12px 16px;margin-top:20px">
      <h3 style="font-size:14px;margin-bottom:10px">Extrair Thumbnail do YouTube</h3>
      <div style="display:flex;gap:8px;align-items:end">
        <div class="form-group" style="margin:0;flex:1">
          <label style="font-size:10px">URL do Video</label>
          <input type="text" id="thumb-yt-url" placeholder="https://www.youtube.com/watch?v=..." style="font-size:12px">
        </div>
        <button class="btn btn-secondary btn-sm" onclick="extrairThumbYT()" style="white-space:nowrap">Extrair</button>
      </div>
      <div id="thumb-yt-result" style="display:none;margin-top:10px">
        <div style="display:flex;gap:16px;align-items:flex-start">
          <div style="width:320px;aspect-ratio:16/9;border-radius:6px;overflow:hidden;border:1px solid var(--border);flex-shrink:0">
            <img id="thumb-yt-img" style="width:100%;height:100%;object-fit:cover">
          </div>
          <div>
            <div id="thumb-yt-title" style="font-size:13px;font-weight:600;margin-bottom:4px"></div>
            <div id="thumb-yt-author" style="font-size:11px;color:var(--text-sec);margin-bottom:8px"></div>
            <div style="display:flex;gap:6px;flex-wrap:wrap">
              <button class="btn btn-secondary btn-sm" onclick="copiarUrlThumbYT('maxres')" style="font-size:10px">Copiar URL (MaxRes)</button>
              <button class="btn btn-secondary btn-sm" onclick="copiarUrlThumbYT('hq')" style="font-size:10px">Copiar URL (HQ)</button>
              <button class="btn btn-primary btn-sm" onclick="baixarThumbYT()" style="font-size:10px">Baixar</button>
            </div>
          </div>
        </div>
      </div>
    </div>
  </div>

  <!-- PAGE: TEMAS -->
  <div id="page-temas" class="page active">
    <div class="page-header">
      <h2>Temas</h2>
      <div style="display:flex;gap:8px">
        <button class="btn btn-secondary btn-sm" onclick="adicionarColunaTemas()">+ Coluna</button>
        <button class="btn btn-primary btn-sm" onclick="adicionarLinhaTemas()">+ Linha</button>
        <button class="btn btn-secondary btn-sm" onclick="toggleAllRows()" style="font-size:10px">Colapsar/Expandir</button>
        <button class="btn btn-secondary btn-sm" onclick="undoTemas()" id="btn-undo-temas" style="display:none">Desfazer</button>
        <button class="btn btn-secondary btn-sm" onclick="syncTemasSupabase()">Sync Supabase</button>
      </div>
    </div>
    <div style="overflow-x:auto">
      <table class="temas-grid" id="temas-grid">
        <thead id="temas-thead"></thead>
        <tbody id="temas-tbody"></tbody>
      </table>
    </div>
    <div id="temas-empty" class="empty-state" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M9.663 17h4.673M12 3v1m6.364 1.636l-.707.707M21 12h-1M4 12H3m3.343-5.657l-.707-.707m2.828 9.9a5 5 0 117.072 0"/></svg>
      <h3>Nenhum tema configurado</h3>
      <p>Adicione colunas (canais) e linhas (datas) para começar.</p>
    </div>

    <!-- MODAL: EDITAR CÉLULA -->
    <div class="modal-overlay" id="modal-celula">
      <div class="modal" style="max-width:500px">
        <div class="modal-header">
          <h3 id="celula-title">Editar Tema</h3>
          <button class="modal-close" onclick="fecharCelula()">&times;</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label>Tema do Vídeo</label>
            <textarea id="cel-tema" rows="2" placeholder="Ex: Someone from the past is returning with a request..." style="font-size:12px;color:var(--accent)"></textarea>
            <div style="display:flex;justify-content:space-between;align-items:center;margin-top:2px">
              <span style="font-size:10px;color:var(--text-sec)">Gancho central</span>
              <div style="display:flex;gap:4px;align-items:center"><span class="case-btns" data-target="cel-tema"></span>
              <button type="button" class="btn btn-secondary btn-sm" style="font-size:9px;padding:1px 6px" onclick="abrirReplicar('tema')">Replicar &rarr;</button></div>
            </div>
          </div>
          <div class="form-group">
            <label>Título do Vídeo</label>
            <textarea id="cel-titulo" rows="2" placeholder="Título completo para o YouTube..."></textarea>
            <div style="display:flex;justify-content:flex-end;gap:4px;margin-top:2px"><span class="case-btns" data-target="cel-titulo"></span>
            <button type="button" class="btn btn-secondary btn-sm" style="font-size:9px;padding:1px 6px" onclick="abrirReplicar('titulo')">Replicar &rarr;</button></div>
          </div>
          <div class="form-group">
            <label>Texto da Thumbnail</label>
            <textarea id="cel-thumb" rows="1" placeholder="Texto curto da thumb..."></textarea>
            <div style="display:flex;justify-content:flex-end;gap:4px;margin-top:2px"><span class="case-btns" data-target="cel-thumb"></span>
            <button type="button" class="btn btn-secondary btn-sm" style="font-size:9px;padding:1px 6px" onclick="abrirReplicar('thumb')">Replicar &rarr;</button></div>
          </div>
          <div class="form-group">
            <label>Pipeline (para geração)</label>
            <select id="cel-pipeline">
              <option value="">Nenhuma</option>
            </select>
          </div>
        </div>
        <div class="form-group">
          <label>Roteiro Gerado</label>
          <textarea id="cel-roteiro" rows="6" placeholder="O roteiro aparecerá aqui após gerar..." style="font-size:11px;background:var(--bg)"></textarea>
          <div style="display:flex;justify-content:space-between;margin-top:2px">
            <span id="cel-roteiro-chars" style="font-size:10px;color:var(--text-sec)">0 chars</span>
            <button type="button" class="btn btn-secondary btn-sm" style="font-size:9px" onclick="navigator.clipboard.writeText(document.getElementById('cel-roteiro').value);toast('Copiado!','success')">Copiar Roteiro</button>
          </div>
        </div>
        <!-- Status de geração -->
        <div id="cel-gen-status" style="display:none;margin-top:8px;padding:8px 12px;background:var(--bg);border:1px solid var(--border);border-radius:6px">
          <div style="display:flex;align-items:center;gap:8px">
            <div class="progress-bar" style="flex:1;height:6px"><div id="cel-gen-fill" class="progress-fill" style="width:0%"></div></div>
            <span id="cel-gen-badge" class="badge badge-transcribing" style="font-size:10px">Gerando...</span>
          </div>
          <div id="cel-gen-etapas" style="font-size:10px;color:var(--text-sec);margin-top:4px"></div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary btn-sm" onclick="abrirReplicarTudo()">Replicar Tudo</button>
          <button class="btn btn-primary btn-sm" onclick="gerarRoteiroCelula()" id="cel-gerar-btn">Gerar Roteiro</button>
          <button class="btn btn-sm" onclick="toggleDone()" id="cel-done-btn" style="background:var(--accent);color:#000;font-size:11px">Done</button>
          <div style="flex:1"></div>
          <button class="btn btn-secondary" onclick="fecharCelula()">Cancelar</button>
          <button class="btn btn-primary" onclick="salvarCelula()">Salvar</button>
        </div>
      </div>
    </div>

    <!-- MODAL: REPLICAR PARA -->
    <div class="modal-overlay" id="modal-replicar">
      <div class="modal" style="max-width:420px">
        <div class="modal-header">
          <h3 id="replicar-title">Replicar para...</h3>
          <button class="modal-close" onclick="document.getElementById('modal-replicar').classList.remove('active')">&times;</button>
        </div>
        <div class="modal-body">
          <div class="form-group">
            <label>Conteúdo a replicar</label>
            <textarea id="replicar-valor" rows="2" readonly style="font-size:12px;background:var(--bg);color:var(--accent)"></textarea>
          </div>
          <div class="form-group">
            <label>Data de destino</label>
            <select id="replicar-data" style="font-size:12px"></select>
          </div>
          <div class="form-group">
            <label>Canais de destino</label>
            <div id="replicar-canais" style="display:flex;flex-direction:column;gap:4px;max-height:200px;overflow-y:auto"></div>
          </div>
          <div class="form-group">
            <label>Campo de destino</label>
            <select id="replicar-campo-destino" style="font-size:12px">
              <option value="mesmo">Mesmo campo</option>
              <option value="tema">Tema</option>
              <option value="titulo">Título</option>
              <option value="thumb">Thumbnail</option>
            </select>
          </div>
        </div>
        <div class="modal-footer">
          <button class="btn btn-secondary btn-sm" onclick="replicarTodos()">Replicar para Todos</button>
          <button class="btn btn-primary btn-sm" onclick="replicarSelecionados()">Replicar Selecionados</button>
        </div>
      </div>
    </div>

    <!-- GERAÇÃO EM LOTE -->
    <div style="margin-top:20px;background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:16px">
      <div style="display:flex;align-items:center;gap:12px;margin-bottom:12px;flex-wrap:wrap">
        <h3 style="font-size:15px;margin:0">Geração de Roteiros em Lote</h3>
        <div style="display:flex;gap:6px;align-items:center">
          <label style="font-size:11px;color:var(--text-sec)">Data:</label>
          <select id="lote-data-select" style="font-size:11px;padding:3px 6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--text)">
            <option value="">Selecione a data</option>
          </select>
        </div>
        <button class="btn btn-primary btn-sm" onclick="gerarLoteRoteiros()" id="btn-lote-roteiros" style="font-size:11px">Gerar Roteiros</button>
        <button class="btn btn-danger btn-sm" onclick="cancelarLoteRoteiros()" id="btn-lote-roteiros-cancel" style="font-size:11px;display:none">Cancelar</button>
        <button class="btn btn-primary btn-sm" onclick="produzirDataCompleta()" id="btn-produzir-tudo" style="font-size:11px;background:var(--warn);color:#000">Produzir Tudo</button>
        <button class="btn btn-danger btn-sm" onclick="cancelarProduzirTudo()" id="btn-produzir-tudo-cancel" style="font-size:11px;display:none">Cancelar</button>
      </div>
      <div id="lote-preview" style="font-size:11px;color:var(--text-sec);margin-bottom:8px"></div>
      <div id="lote-status" style="display:none">
        <div style="display:flex;align-items:center;gap:8px;margin-bottom:6px">
          <div class="progress-bar" style="flex:1;height:6px"><div id="lote-fill" class="progress-fill" style="width:0%"></div></div>
          <span id="lote-count" style="font-size:11px;color:var(--accent)">0/0</span>
        </div>
        <div id="lote-log" style="font-size:10px;color:var(--text-sec);max-height:120px;overflow-y:auto"></div>
      </div>
    </div>

    <!-- FLOATING CHAT PANEL -->
    <div id="chat-panel" style="display:none;position:fixed;right:16px;top:80px;width:380px;bottom:16px;background:var(--panel);border:1px solid var(--border);border-radius:12px;box-shadow:0 8px 32px rgba(0,0,0,0.4);z-index:900;display:none;flex-direction:column">
      <div style="padding:12px 16px;border-bottom:1px solid var(--border);display:flex;justify-content:space-between;align-items:center">
        <select id="chat-agent-select" onchange="trocarAgente()" style="font-size:12px;padding:3px 6px;background:var(--bg);border:1px solid var(--border);border-radius:4px;color:var(--accent);font-weight:600">
          <option value="temas">Temas</option>
          <option value="titulos">Títulos</option>
        </select>
        <div style="display:flex;gap:6px;align-items:center">
          <button class="btn btn-secondary btn-sm" style="font-size:10px;padding:2px 8px" onclick="toggleInstrucoes()">Instruções</button>
          <button class="btn btn-danger btn-sm" style="font-size:10px;padding:2px 6px" onclick="limparChatHistorico()">Limpar</button>
          <button style="background:none;border:none;color:var(--text-sec);cursor:pointer;font-size:18px" onclick="toggleChat()">&times;</button>
        </div>
      </div>
      <div id="chat-instrucoes-panel" style="display:none;padding:8px 12px;border-bottom:1px solid var(--border);background:var(--bg)">
        <div style="font-size:11px;color:var(--text-sec);margin-bottom:4px">CLAUDE.md do agente (instruções persistentes)</div>
        <textarea id="chat-instrucoes" rows="6" style="font-size:11px;padding:6px 8px;font-family:monospace"></textarea>
        <button class="btn btn-primary btn-sm" style="font-size:10px;margin-top:4px" onclick="salvarInstrucoes()">Salvar Instruções</button>
      </div>
      <div id="chat-messages" style="flex:1;overflow-y:auto;padding:12px;display:flex;flex-direction:column;gap:8px"></div>
      <div style="padding:8px 12px;border-top:1px solid var(--border);display:flex;gap:8px">
        <textarea id="chat-input" rows="2" placeholder="Digite sua mensagem..." style="flex:1;font-size:12px;padding:6px 8px;resize:none" onkeydown="if(event.key==='Enter'&&!event.shiftKey){event.preventDefault();enviarChat()}"></textarea>
        <button class="btn btn-primary btn-sm" onclick="enviarChat()" id="chat-send-btn" style="align-self:flex-end">Enviar</button>
      </div>
    </div>
    <button id="chat-toggle-btn" onclick="toggleChat()" style="display:none;position:fixed;right:16px;bottom:16px;width:50px;height:50px;border-radius:50%;background:var(--accent);border:none;color:#000;font-size:22px;cursor:pointer;box-shadow:0 4px 16px rgba(0,0,0,0.3);z-index:899" title="Abrir assistente">💬</button>
  </div>

  <!-- PAGE: ROTEIROS -->
  <div id="page-roteiros" class="page">
    <div class="page-header">
      <h2>Roteiros</h2>
      <button class="btn btn-primary" onclick="abrirEditorPipeline()">+ Nova Pipeline</button>
    </div>
    <div id="pipelines-grid" class="cards-grid"></div>
    <div id="pipelines-empty" class="empty-state" style="display:none">
      <svg viewBox="0 0 24 24" fill="none" stroke="currentColor" stroke-width="1.5"><path d="M14 2H6a2 2 0 00-2 2v16a2 2 0 002 2h12a2 2 0 002-2V8z"/><polyline points="14,2 14,8 20,8"/></svg>
      <h3>Nenhuma pipeline criada</h3>
      <p>Crie sua primeira pipeline de roteiro.</p>
    </div>

    <!-- PAINEL DE EXECUÇÃO -->
    <div id="exec-panel" style="display:none;margin-top:24px">
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:20px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
          <h3 id="exec-title" style="font-size:16px">Executando Pipeline</h3>
          <button class="btn btn-danger btn-sm" onclick="cancelarExecucao()">Cancelar</button>
        </div>
        <div id="exec-etapas"></div>
        <div id="exec-resultado" style="display:none;margin-top:16px">
          <label style="font-size:13px;color:var(--text-sec);margin-bottom:8px;display:block">Resultado Final</label>
          <textarea id="exec-resultado-text" readonly style="width:100%;height:200px;background:var(--bg);border:1px solid var(--border);border-radius:6px;color:var(--text);padding:12px;font-size:13px;font-family:inherit;resize:vertical"></textarea>
        </div>
      </div>
    </div>
  </div>

  <!-- PAGE: CONFIG -->
  <div id="page-config" class="page">
    <div class="page-header">
      <h2>Configurações</h2>
    </div>
    <div style="max-width:700px">
      <!-- CREDENCIAIS -->
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px">
          <h3 style="font-size:15px">Credenciais (API Keys)</h3>
          <button class="btn btn-primary btn-sm" onclick="novaCredencial()">+ Adicionar</button>
        </div>
        <div id="creds-list"></div>
        <div id="creds-empty" style="color:var(--text-sec);font-size:13px;text-align:center;padding:20px;display:none">Nenhuma credencial cadastrada</div>
      </div>

      <!-- SUPABASE / SHEETS -->
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px">
        <h3 style="font-size:15px;margin-bottom:16px">APIs & Sync</h3>
        <div class="form-group">
          <label>ai33.pro API Key (TTS)</label>
          <input type="password" id="cfg-ai33-key" placeholder="sk_...">
        </div>
        <hr style="border-color:var(--border);margin:16px 0">
        <div class="form-row">
          <div class="form-group">
            <label>Supabase URL</label>
            <input type="text" id="cfg-supabase-url" placeholder="https://xxx.supabase.co">
          </div>
          <div class="form-group">
            <label>Supabase Service Key</label>
            <input type="password" id="cfg-supabase-key" placeholder="eyJ...">
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Google Sheets ID</label>
            <input type="text" id="cfg-sheets-id" placeholder="1BxiM...">
          </div>
          <div class="form-group">
            <label>Sheets API Key</label>
            <input type="password" id="cfg-sheets-api-key" placeholder="AIza...">
          </div>
        </div>
        <div class="form-group">
          <label>Nome da Aba (Sheet)</label>
          <input type="text" id="cfg-sheets-tab" value="Temas" placeholder="Temas">
        </div>
        <button class="btn btn-primary btn-sm" onclick="salvarConfig()">Salvar Sync</button>
      </div>
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:20px;margin-bottom:16px">
        <h3 style="font-size:15px;margin-bottom:16px">Link Tracker</h3>
        <div class="form-row">
          <div class="form-group">
            <label>URL do Tracker</label>
            <input type="text" id="cfg-tracker-url" placeholder="https://track.seudominio.com">
          </div>
          <div class="form-group">
            <label>Credenciais (user:pass)</label>
            <input type="password" id="cfg-tracker-auth" placeholder="admin:senha123">
          </div>
        </div>
        <div class="form-group">
          <label>Template do Comentário</label>
          <textarea id="cfg-comment-template" rows="3" placeholder="&#128279; {{link}}&#10;&#10;Texto fixo..." style="font-size:12px"></textarea>
          <div style="font-size:10px;color:var(--text-sec);margin-top:2px">Variáveis: {{link}}, {{titulo}}, {{canal}}, {{data}}</div>
        </div>
        <button class="btn btn-primary btn-sm" onclick="salvarConfig()">Salvar Tracker</button>
      </div>
    </div>
  </div>

</div>

<!-- MODAL: TEMPLATE EDITOR -->
<div class="modal-overlay" id="modal-editor">
  <div class="modal">
    <div class="modal-header">
      <h3 id="editor-title">Novo Template</h3>
      <button class="modal-close" onclick="fecharEditor()">&times;</button>
    </div>
    <div class="modal-body">
      <div class="tabs">
        <button class="tab active" onclick="showEditorTab('geral',this)">Geral</button>
        <button class="tab" onclick="showEditorTab('fundo',this)">Fundo</button>
        <button class="tab" onclick="showEditorTab('overlay',this)">Overlay</button>
        <button class="tab" onclick="showEditorTab('ajustes',this)">Ajustes</button>
        <button class="tab" onclick="showEditorTab('audio',this)">Áudio</button>
        <button class="tab" onclick="showEditorTab('legenda',this)">Legenda</button>
        <button class="tab" onclick="showEditorTab('regras',this)">Regras</button>
        <button class="tab" onclick="showEditorTab('saida',this)">Saída</button>
      </div>

      <!-- TAB: GERAL -->
      <div class="tab-content active" id="tab-geral">
        <div class="form-row">
          <div class="form-group">
            <label>Nome do Template</label>
            <input type="text" id="ed-nome" placeholder="Ex: Whispers from Arcturus - EN">
          </div>
          <div class="form-group">
            <label>Tag (identificador curto)</label>
            <input type="text" id="ed-tag" placeholder="Ex: EN, CO1, DE">
          </div>
        </div>
        <div class="form-row-3">
          <div class="form-group">
            <label>Idioma</label>
            <select id="ed-idioma">
              <option value="en">Inglês (EN)</option>
              <option value="de">Alemão (DE)</option>
              <option value="pt">Português (PT)</option>
              <option value="es">Espanhol (ES)</option>
            </select>
          </div>
          <div class="form-group">
            <label>Resolução</label>
            <select id="ed-resolucao">
              <option value="1920x1080">1920x1080 (1080p)</option>
              <option value="2560x1440">2560x1440 (1440p)</option>
              <option value="3840x2160">3840x2160 (4K)</option>
            </select>
          </div>
          <div class="form-group">
            <label>FPS</label>
            <select id="ed-fps">
              <option value="24">24</option>
              <option value="30" selected>30</option>
              <option value="60">60</option>
            </select>
          </div>
        </div>
      </div>

      <!-- TAB: FUNDO -->
      <div class="tab-content" id="tab-fundo">
        <div class="form-group">
          <label>Tipo de Fundo</label>
          <select id="ed-tipo-fundo" onchange="toggleFundoOpcoes()">
            <option value="imagens">Imagens (JPEG/PNG)</option>
            <option value="videos">Vídeos (MP4)</option>
          </select>
        </div>
        <div class="form-group" id="opcoes-video-loop" style="display:none">
          <div style="display:flex;align-items:center;gap:8px">
            <label><input type="checkbox" id="ed-video-loop" checked> Loop de vídeos</label>
            <span style="position:relative;cursor:help" onmouseenter="this.querySelector('.tooltip-box').style.display='block'" onmouseleave="this.querySelector('.tooltip-box').style.display='none'">
              <span style="display:inline-flex;align-items:center;justify-content:center;width:18px;height:18px;border-radius:50%;background:var(--border);color:var(--text-sec);font-size:11px;font-weight:700">!</span>
              <div class="tooltip-box" style="display:none;position:absolute;left:24px;top:-10px;background:var(--panel);border:1px solid var(--border);border-radius:6px;padding:10px 12px;width:280px;z-index:100;box-shadow:0 4px 12px rgba(0,0,0,0.4);font-size:11px;line-height:1.5">
                <div style="font-weight:600;margin-bottom:4px;color:var(--accent)">Marcado (Loop)</div>
                <div style="color:var(--text-sec)">Repete os vídeos randomizados até cobrir toda a duração do áudio. Cada produção gera uma ordem diferente.</div>
                <div style="font-weight:600;margin:8px 0 4px;color:var(--warn)">Desmarcado (Sem Loop)</div>
                <div style="color:var(--text-sec)">Roda os vídeos uma vez na ordem e congela no último frame quando acabar.</div>
              </div>
            </span>
          </div>
        </div>
        <div class="form-group">
          <label>Pasta de Imagens/Vídeos</label>
          <div class="input-with-btn">
            <input type="text" id="ed-pasta-imagens" placeholder="F:/Canal Dark/Midias/...">
            <button class="btn btn-secondary btn-sm" onclick="abrirBrowser('ed-pasta-imagens','folder')">Buscar</button>
          </div>
        </div>
        <div id="opcoes-imagens">
          <div class="form-row">
            <div class="form-group">
              <label>Duração por Imagem (segundos)</label>
              <input type="number" id="ed-duracao-imagem" value="10" min="3" max="30">
            </div>
            <div class="form-group">
              <label>Zoom Ratio (Ken Burns)</label>
              <div class="slider-group">
                <input type="range" id="ed-zoom-ratio" min="1.00" max="1.15" step="0.01" value="1.04" oninput="document.getElementById('zoom-val').textContent=this.value">
                <span class="slider-val" id="zoom-val">1.04</span>
              </div>
            </div>
          </div>
          <div class="form-group">
            <label><input type="checkbox" id="ed-efeito-zoom" checked> Ativar efeitos de movimento</label>
            <div style="font-size:11px;color:var(--text-sec);margin-top:4px">Alterna automaticamente entre zoom in, zoom out, pan esquerda e pan direita — todos suaves e sem vibração</div>
          </div>
          <div class="kenburns-preview" id="kenburns-preview" style="display:none">
            <img id="kenburns-img" src="" alt="Ken Burns Preview">
            <span class="kb-label">Ken Burns Preview</span>
          </div>
          <button class="btn btn-secondary btn-sm" onclick="previewKenBurns()" style="margin-top:8px" id="btn-kenburns-preview">Visualizar Ken Burns</button>
        </div>
      </div>

      <!-- TAB: OVERLAY -->
      <div class="tab-content" id="tab-overlay">
        <div id="overlays-list"></div>
        <button class="btn btn-secondary btn-sm" onclick="adicionarOverlay()" style="margin-top:12px">+ Adicionar Overlay</button>
        <div class="overlay-preview-area" id="overlay-preview" style="display:none">
          <img id="overlay-bg-img" src="" alt="Background" style="z-index:1">
          <img id="overlay-fg-img" src="" alt="Overlay" style="z-index:2;opacity:0.3">
          <span class="ov-label">Overlay Preview</span>
        </div>
        <button class="btn btn-secondary btn-sm" onclick="previewOverlay()" style="margin-top:8px" id="btn-overlay-preview">Visualizar Overlay</button>

        <div style="margin-top:24px;padding-top:16px;border-top:1px solid var(--border)">
          <h4 style="font-size:14px;margin-bottom:12px">Moldura (Frame Overlay)</h4>
          <div class="form-group">
            <label>Arquivo da Moldura</label>
            <div class="input-with-btn">
              <input type="text" id="ed-moldura-arquivo" placeholder="Caminho da imagem (PNG ou fundo verde)...">
              <button class="btn btn-secondary btn-sm" onclick="abrirBrowser('ed-moldura-arquivo','file')">Buscar</button>
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label>Tipo</label>
              <select id="ed-moldura-tipo">
                <option value="chromakey">Chroma Key (fundo verde)</option>
                <option value="alpha">PNG com transparência</option>
              </select>
            </div>
            <div class="form-group">
              <label>Opacidade</label>
              <div class="slider-group">
                <input type="range" id="ed-moldura-opacidade" min="0.1" max="1.0" step="0.05" value="1.0" oninput="document.getElementById('moldura-op-val').textContent=Math.round(this.value*100)+'%'">
                <span class="slider-val" id="moldura-op-val">100%</span>
              </div>
            </div>
          </div>
        </div>

        <div style="margin-top:24px;padding-top:16px;border-top:1px solid var(--border)">
          <h4 style="font-size:14px;margin-bottom:12px">CTA (Call to Action)</h4>
          <div class="form-group">
            <label>Arquivo CTA (vídeo com fundo verde)</label>
            <div class="input-with-btn">
              <input type="text" id="ed-cta-arquivo" placeholder="Caminho do vídeo CTA...">
              <button class="btn btn-secondary btn-sm" onclick="abrirBrowser('ed-cta-arquivo','file')">Buscar</button>
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label>Primeira aparição (segundos)</label>
              <input type="number" id="ed-cta-inicio" value="30" min="0">
            </div>
            <div class="form-group">
              <label>Duração na tela (segundos)</label>
              <input type="number" id="ed-cta-duracao" value="8" min="1" max="30">
            </div>
          </div>
          <div class="form-row">
            <div class="form-group">
              <label>Intervalo de repetição (segundos)</label>
              <input type="number" id="ed-cta-intervalo" value="300" min="30">
              <div style="font-size:11px;color:var(--text-sec);margin-top:2px">300 = a cada 5 min, 600 = a cada 10 min</div>
            </div>
            <div class="form-group">
              <label>Escala (% do vídeo)</label>
              <div class="slider-group">
                <input type="range" id="ed-cta-escala" min="0.10" max="0.50" step="0.05" value="0.25" oninput="document.getElementById('cta-escala-val').textContent=Math.round(this.value*100)+'%';previewCTA()">
                <span class="slider-val" id="cta-escala-val">25%</span>
              </div>
            </div>
          </div>
          <div class="form-group">
            <label>Posição</label>
            <select id="ed-cta-posicao" onchange="previewCTA()">
              <option value="bottom-right">Inferior Direita</option>
              <option value="bottom-center">Inferior Centro</option>
              <option value="bottom-left">Inferior Esquerda</option>
              <option value="top-right">Superior Direita</option>
              <option value="top-center">Superior Centro</option>
              <option value="top-left">Superior Esquerda</option>
              <option value="center">Centro</option>
            </select>
          </div>
          <button class="btn btn-secondary btn-sm" onclick="previewCTA()" style="margin-top:8px">Visualizar CTA</button>
          <div id="cta-preview" style="display:none;margin-top:12px;position:relative;aspect-ratio:16/9;background:#222;border-radius:8px;overflow:hidden;border:1px solid var(--border)">
            <img id="cta-preview-img" src="" style="position:absolute;object-fit:contain">
            <div style="position:absolute;inset:0;display:flex;align-items:center;justify-content:center;color:var(--text-sec);font-size:11px;z-index:0">Área do vídeo (16:9)</div>
          </div>
        </div>
      </div>

      <!-- TAB: AJUSTES -->
      <div class="tab-content" id="tab-ajustes">
        <div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:16px;padding:10px 14px;background:var(--bg);border:1px solid var(--border);border-radius:6px">
          <div style="display:flex;align-items:center;gap:10px">
            <input type="checkbox" id="ed-ajustes-random" style="width:16px;height:16px;accent-color:var(--accent)">
            <label for="ed-ajustes-random" style="margin:0;font-size:13px;color:var(--text);cursor:pointer">Randomizar ajustes a cada vídeo</label>
          </div>
          <span style="font-size:11px;color:var(--text-sec)">Variações sutis e coerentes automaticamente</span>
        </div>
        <p style="font-size:11px;color:var(--text-sec);margin-bottom:16px">Ajustes base (se randomizador ligado, servem como ponto central da variação)</p>

        <div style="display:grid;grid-template-columns:1fr 1fr;gap:12px 24px">
          <div class="form-group" style="margin:0">
            <label>Exposição</label>
            <div class="slider-group">
              <input type="range" id="ed-exposicao" min="-1.0" max="1.0" step="0.05" value="0" oninput="document.getElementById('exposicao-val').textContent=this.value">
              <span class="slider-val" id="exposicao-val">0</span>
            </div>
          </div>
          <div class="form-group" style="margin:0">
            <label>Contraste</label>
            <div class="slider-group">
              <input type="range" id="ed-contraste" min="0.5" max="2.0" step="0.05" value="1.0" oninput="document.getElementById('contraste-val').textContent=this.value">
              <span class="slider-val" id="contraste-val">1.0</span>
            </div>
          </div>
          <div class="form-group" style="margin:0">
            <label>Realces</label>
            <div class="slider-group">
              <input type="range" id="ed-realces" min="-1.0" max="1.0" step="0.05" value="0" oninput="document.getElementById('realces-val').textContent=this.value">
              <span class="slider-val" id="realces-val">0</span>
            </div>
          </div>
          <div class="form-group" style="margin:0">
            <label>Sombras</label>
            <div class="slider-group">
              <input type="range" id="ed-sombras" min="-1.0" max="1.0" step="0.05" value="0" oninput="document.getElementById('sombras-val').textContent=this.value">
              <span class="slider-val" id="sombras-val">0</span>
            </div>
          </div>
          <div class="form-group" style="margin:0">
            <label>Brancos</label>
            <div class="slider-group">
              <input type="range" id="ed-brancos" min="-1.0" max="1.0" step="0.05" value="0" oninput="document.getElementById('brancos-val').textContent=this.value">
              <span class="slider-val" id="brancos-val">0</span>
            </div>
          </div>
          <div class="form-group" style="margin:0">
            <label>Pretos</label>
            <div class="slider-group">
              <input type="range" id="ed-pretos" min="-1.0" max="1.0" step="0.05" value="0" oninput="document.getElementById('pretos-val').textContent=this.value">
              <span class="slider-val" id="pretos-val">0</span>
            </div>
          </div>
          <div class="form-group" style="margin:0">
            <label>Temperatura</label>
            <div class="slider-group">
              <input type="range" id="ed-temperatura" min="-1.0" max="1.0" step="0.05" value="0" oninput="document.getElementById('temperatura-val').textContent=this.value">
              <span class="slider-val" id="temperatura-val">0</span>
            </div>
          </div>
          <div class="form-group" style="margin:0">
            <label>Tonalidade</label>
            <div class="slider-group">
              <input type="range" id="ed-tonalidade" min="-1.0" max="1.0" step="0.05" value="0" oninput="document.getElementById('tonalidade-val').textContent=this.value">
              <span class="slider-val" id="tonalidade-val">0</span>
            </div>
          </div>
          <div class="form-group" style="margin:0">
            <label>Saturação</label>
            <div class="slider-group">
              <input type="range" id="ed-saturacao" min="0" max="3.0" step="0.05" value="1.0" oninput="document.getElementById('saturacao-val').textContent=this.value">
              <span class="slider-val" id="saturacao-val">1.0</span>
            </div>
          </div>
          <div class="form-group" style="margin:0">
            <label>Brilho</label>
            <div class="slider-group">
              <input type="range" id="ed-brilho" min="-0.3" max="0.3" step="0.01" value="0" oninput="document.getElementById('brilho-val').textContent=this.value">
              <span class="slider-val" id="brilho-val">0</span>
            </div>
          </div>
        </div>
        <div class="form-group" style="margin-top:16px">
          <label>Vinheta (escurecimento das bordas)</label>
          <div class="slider-group">
            <input type="range" id="ed-vinheta" min="0" max="1.0" step="0.05" value="0" oninput="document.getElementById('vinheta-val').textContent=this.value">
            <span class="slider-val" id="vinheta-val">0</span>
          </div>
        </div>
        <button class="btn btn-secondary btn-sm" onclick="resetAjustes()" style="margin-top:8px">Resetar tudo</button>
      </div>

      <!-- TAB: AUDIO -->
      <div class="tab-content" id="tab-audio">
        <div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;margin-bottom:16px">
          <div style="font-size:12px;font-weight:600;margin-bottom:8px;color:var(--info)">Voz de Narração (TTS)</div>
          <div class="form-row">
            <div class="form-group" style="margin:0">
              <label>Provedor</label>
              <select id="ed-voz-provider" onchange="filtrarVozesTemplate()" style="font-size:12px">
                <option value="all">Todos</option>
                <option value="minimax_clone">Minimax (Clonadas)</option>
                <option value="minimax">Minimax (Banco)</option>
                <option value="elevenlabs">ElevenLabs</option>
              </select>
            </div>
            <div class="form-group" style="margin:0">
              <label>Voz</label>
              <select id="ed-voz-id" style="font-size:12px">
                <option value="">Nenhuma (manual)</option>
              </select>
            </div>
          </div>
          <div class="form-row" style="margin-top:8px">
            <div class="form-group" style="margin:0">
              <label>Velocidade</label>
              <div class="slider-group">
                <input type="range" id="ed-voz-speed" min="0.5" max="2.0" step="0.05" value="1.0" oninput="document.getElementById('voz-speed-val').textContent=this.value+'x'">
                <span class="slider-val" id="voz-speed-val">1.0x</span>
              </div>
            </div>
            <div class="form-group" style="margin:0">
              <label>Tom (pitch)</label>
              <div class="slider-group">
                <input type="range" id="ed-voz-pitch" min="-6" max="6" step="1" value="0" oninput="document.getElementById('voz-pitch-val').textContent=this.value">
                <span class="slider-val" id="voz-pitch-val">0</span>
              </div>
            </div>
          </div>
          <div style="font-size:10px;color:var(--text-sec);margin-top:4px">Voz e ajustes usados na narração automática</div>
        </div>
        <div class="form-group">
          <label>Trilha Sonora</label>
          <div class="input-with-btn">
            <input type="text" id="ed-trilha" placeholder="F:/Canal Dark/Midias/Trilhas/...">
            <button class="btn btn-secondary btn-sm" onclick="abrirBrowser('ed-trilha','file')">Buscar</button>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Volume da Trilha</label>
            <div class="slider-group">
              <input type="range" id="ed-trilha-vol" min="0" max="0.5" step="0.01" value="0.15" oninput="document.getElementById('trilha-vol-val').textContent=Math.round(this.value*100)+'%'">
              <span class="slider-val" id="trilha-vol-val">15%</span>
            </div>
          </div>
          <div class="form-group">
            <label>Volume da Narração</label>
            <div class="slider-group">
              <input type="range" id="ed-narracao-vol" min="0.5" max="1.5" step="0.05" value="1.0" oninput="document.getElementById('narracao-vol-val').textContent=Math.round(this.value*100)+'%'">
              <span class="slider-val" id="narracao-vol-val">100%</span>
            </div>
          </div>
        </div>
        <div class="audio-preview-row">
          <button class="btn btn-secondary btn-sm" id="btn-audio-preview" onclick="previewAudio()">&#9654; Preview (15s)</button>
          <span id="audio-preview-status"></span>
        </div>
        <audio id="audio-preview-player" style="display:none"></audio>
      </div>

      <!-- TAB: LEGENDA -->
      <div class="tab-content" id="tab-legenda">
        <div class="form-group">
          <label>Estilo de Legenda</label>
          <select id="ed-estilo-legenda" onchange="syncPresetToControls()">
            <option value="1">1 - Branco bold + outline preto</option>
            <option value="2">2 - Branco bold MAIÚSCULO + outline preto</option>
            <option value="3">3 - Amarelo bold + outline preto</option>
            <option value="4">4 - Branco sem outline (leve)</option>
            <option value="5">5 - Amarelo sem outline</option>
          </select>
        </div>
        <div style="display:flex;justify-content:flex-end;margin-bottom:4px">
          <button class="btn btn-secondary btn-sm" onclick="togglePreviewBg()" style="font-size:11px;padding:3px 8px" id="btn-preview-bg">Fundo: Escuro</button>
        </div>
        <div class="preset-preview" id="legenda-preview" style="background:#111;aspect-ratio:16/9;min-height:180px;border-radius:8px;position:relative;overflow:hidden;align-items:flex-end;padding-bottom:24px" data-bg="dark">
          <div id="preview-text" style="font-family:Arial;font-size:24px;font-weight:bold;color:white;text-shadow:2px 2px 0 #000,-2px -2px 0 #000,2px -2px 0 #000,-2px 2px 0 #000;text-align:center;line-height:1.4;padding:0 20px">The universe is sending you<br>a powerful message today</div>
        </div>
        <div class="form-row" style="margin-top:16px">
          <div class="form-group">
            <label>Fonte</label>
            <select id="ed-legenda-fonte" onchange="previewEstilo()"></select>
          </div>
          <div class="form-group">
            <label>Tamanho</label>
            <input type="number" id="ed-legenda-tamanho" value="24" min="10" max="80" onchange="previewEstilo()">
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Cor do Texto</label>
            <div class="color-row">
              <input type="color" id="ed-legenda-cor" value="#FFFFFF" onchange="previewEstilo()" oninput="previewEstilo()">
              <span style="font-size:12px;color:var(--text-sec)" id="legenda-cor-hex">#FFFFFF</span>
            </div>
          </div>
          <div class="form-group">
            <label>Cor do Outline</label>
            <div class="color-row">
              <input type="color" id="ed-legenda-cor-outline" value="#000000" onchange="previewEstilo()" oninput="previewEstilo()">
              <span style="font-size:12px;color:var(--text-sec)" id="legenda-cor-outline-hex">#000000</span>
            </div>
          </div>
        </div>
        <div class="form-group">
          <label>Espessura do Outline</label>
          <div class="slider-group">
            <input type="range" id="ed-legenda-outline-espessura" min="0" max="5" step="1" value="2" oninput="document.getElementById('outline-esp-val').textContent=this.value;previewEstilo()">
            <span class="slider-val" id="outline-esp-val">2</span>
          </div>
        </div>
        <div class="form-group">
          <label>Sombra (Drop Shadow)</label>
          <div class="slider-group">
            <input type="range" id="ed-legenda-sombra" min="0" max="5" step="1" value="0" oninput="document.getElementById('sombra-leg-val').textContent=this.value;previewEstilo()">
            <span class="slider-val" id="sombra-leg-val">0</span>
          </div>
        </div>
        <div style="display:flex;gap:24px;margin-bottom:16px">
          <div class="checkbox-row">
            <input type="checkbox" id="ed-legenda-maiuscula" onchange="previewEstilo()">
            <label for="ed-legenda-maiuscula">MAIUSCULA</label>
          </div>
          <div class="checkbox-row">
            <input type="checkbox" id="ed-legenda-bold" checked onchange="previewEstilo()">
            <label for="ed-legenda-bold">Bold</label>
          </div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Máximo de linhas</label>
            <select id="ed-legenda-max-linhas" onchange="previewEstilo()">
              <option value="1">1 linha</option>
              <option value="2" selected>2 linhas</option>
            </select>
          </div>
          <div class="form-group">
            <label>Caracteres por linha</label>
            <input type="number" id="ed-legenda-max-chars" value="42" min="20" max="80" onchange="previewEstilo()">
          </div>
        </div>
        <div class="form-group" style="margin-top:16px">
          <label>Posição na Tela</label>
          <select id="ed-legenda-posicao" onchange="togglePosicaoCustom();previewEstilo()">
            <option value="bottom">Embaixo (padrão)</option>
            <option value="center">Centro</option>
            <option value="top">Topo</option>
            <option value="custom">Customizada (X, Y)</option>
          </select>
        </div>
        <div id="posicao-custom" style="display:none">
          <div class="form-row">
            <div class="form-group">
              <label>Posição X (horizontal)</label>
              <div class="slider-group">
                <input type="range" id="ed-legenda-x" min="0" max="100" value="50" oninput="document.getElementById('legenda-x-val').textContent=this.value+'%';previewEstilo()">
                <span class="slider-val" id="legenda-x-val">50%</span>
              </div>
            </div>
            <div class="form-group">
              <label>Posição Y (vertical)</label>
              <div class="slider-group">
                <input type="range" id="ed-legenda-y" min="5" max="95" value="85" oninput="document.getElementById('legenda-y-val').textContent=this.value+'%';previewEstilo()">
                <span class="slider-val" id="legenda-y-val">85%</span>
              </div>
            </div>
          </div>
        </div>
      </div>

      <!-- TAB: REGRAS -->
      <div class="tab-content" id="tab-regras">
        <div class="form-group">
          <label>Substituições de Palavras</label>
          <textarea id="ed-regras-subs" rows="8" placeholder="Uma por linha no formato:  errado=correto&#10;&#10;Exemplo:&#10;star seat=Starseed&#10;pleadian=Pleiadian&#10;arcturian=Arcturian" style="font-family:monospace;font-size:12px"></textarea>
          <div style="font-size:11px;color:var(--text-sec);margin-top:4px">Corrige erros comuns do Whisper. Case-insensitive. Formato: <strong>errado=correto</strong></div>
        </div>
        <div class="form-row">
          <div class="form-group">
            <label>Max caracteres por linha</label>
            <input type="number" id="ed-regras-max-chars" value="42" min="20" max="80">
          </div>
          <div class="form-group">
            <label>Max linhas por bloco</label>
            <input type="number" id="ed-regras-max-linhas" value="2" min="1" max="3">
          </div>
        </div>
        <div class="form-group" style="display:flex;gap:24px;flex-wrap:wrap">
          <label><input type="checkbox" id="ed-regras-hesitacoes" checked> Remover hesitações (uh, um, ah...)</label>
          <label><input type="checkbox" id="ed-regras-capitalizar" checked> Capitalizar início de frases</label>
        </div>
        <div class="form-group">
          <label>Palavras a Remover (uma por linha)</label>
          <textarea id="ed-regras-remover" rows="3" placeholder="Palavras que serão removidas do texto&#10;Exemplo:&#10;you know&#10;I mean" style="font-family:monospace;font-size:12px"></textarea>
        </div>
      </div>

      <!-- TAB: SAÍDA -->
      <div class="tab-content" id="tab-saida">
        <div class="form-group">
          <label>Pasta de Saída</label>
          <div class="input-with-btn">
            <input type="text" id="ed-pasta-saida" placeholder="F:/Canal Dark/Exports/">
            <button class="btn btn-secondary btn-sm" onclick="abrirBrowser('ed-pasta-saida','folder')">Buscar</button>
          </div>
        </div>
        <div class="form-group">
          <label>Formato do Nome de Saída</label>
          <input type="text" id="ed-formato-nome" value="{tag}_{data}_{sequencia}" placeholder="{tag}_{data}_{sequencia}">
          <div style="font-size:11px;color:var(--text-sec);margin-top:4px">Variáveis: {tag}, {nome}, {data}, {sequencia}, {idioma}</div>
        </div>
        <div class="form-group">
          <label>Mínimo de caracteres do roteiro</label>
          <input type="number" id="ed-min-roteiro-chars" value="15000" min="0" max="100000">
          <div style="font-size:10px;color:var(--text-sec);margin-top:2px">Se o roteiro gerado tiver menos que esse valor, será refeito automaticamente (0 = sem verificação)</div>
        </div>
        <div class="form-group">
          <label>Proxy (para upload YouTube)</label>
          <input type="text" id="ed-proxy" placeholder="http://user:pass@ip:port ou socks5://ip:port">
          <div style="font-size:10px;color:var(--text-sec);margin-top:2px">Cada canal pode ter proxy diferente para mascarar IP</div>
        </div>
        <div class="form-group">
          <label>URL de Destino (para Link Tracker)</label>
          <input type="text" id="ed-link-destino" placeholder="https://seusite.com/bio ou https://hotmart.com/...">
          <div style="font-size:10px;color:var(--text-sec);margin-top:2px">URL para onde o link rastreável redireciona (bio link, Hotmart, etc)</div>
        </div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="fecharEditor()">Cancelar</button>
      <button class="btn btn-primary" onclick="salvarTemplate()">Salvar Template</button>
    </div>
  </div>
</div>

<!-- MODAL: PIPELINE EDITOR -->
<div class="modal-overlay" id="modal-pipeline">
  <div class="modal" style="max-width:800px;max-height:90vh;overflow-y:auto">
    <div class="modal-header">
      <h3 id="pipeline-editor-title">Nova Pipeline</h3>
      <button class="modal-close" onclick="fecharEditorPipeline()">&times;</button>
    </div>
    <div class="modal-body">
      <div class="form-row">
        <div class="form-group">
          <label>Nome da Pipeline</label>
          <input type="text" id="pip-nome" placeholder="Ex: Whispers from Arcturus">
        </div>
        <div class="form-group" style="max-width:100px">
          <label>Tag</label>
          <input type="text" id="pip-tag" placeholder="EN" style="text-transform:uppercase">
        </div>
        <div class="form-group" style="max-width:120px">
          <label>Idioma</label>
          <select id="pip-idioma">
            <option value="en">Inglês</option>
            <option value="de">Alemão</option>
            <option value="pt">Português</option>
            <option value="es">Espanhol</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>Etapas (Agentes)</label>
        <div id="pip-etapas-list"></div>
        <button class="btn btn-secondary btn-sm" onclick="adicionarEtapaPipeline()" style="margin-top:8px">+ Adicionar Etapa</button>
      </div>
      <!-- REFERÊNCIA DE VARIÁVEIS -->
      <div style="margin-top:16px">
        <button class="btn btn-secondary btn-sm" onclick="this.nextElementSibling.style.display=this.nextElementSibling.style.display==='none'?'block':'none'" style="font-size:11px">Variáveis disponíveis nos prompts</button>
        <div style="display:none;margin-top:8px;background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;font-size:11px;line-height:1.8">
          <div style="font-weight:600;margin-bottom:6px;color:var(--accent)">Dados do Tema (vem da célula da aba Temas)</div>
          <code style="color:var(--accent)">{{tema}}</code> — Tema/gancho central do vídeo<br>
          <code style="color:var(--accent)">{{titulo}}</code> — Título do vídeo (YouTube)<br>
          <code style="color:var(--accent)">{{thumb}}</code> — Texto da thumbnail<br>
          <code style="color:var(--accent)">{{canal}}</code> — Nome da coluna (Christian, Arcturian, etc)<br>
          <code style="color:var(--accent)">{{data}}</code> — Data da linha (DD/MM/YYYY)<br>
          <div style="font-weight:600;margin:8px 0 6px;color:var(--info)">Fluxo entre etapas</div>
          <code style="color:var(--info)">{{entrada}}</code> — Input original (= tema)<br>
          <code style="color:var(--info)">{{saida_anterior}}</code> — Output da etapa anterior<br>
          <code style="color:var(--info)">{{saida_etapa_1}}</code>, <code style="color:var(--info)">{{saida_etapa_2}}</code>, ... — Output de uma etapa específica<br>
          <code style="color:var(--info)">{{roteiro_atual}}</code> — Último output completo<br>
          <div style="font-weight:600;margin:8px 0 6px;color:var(--text-sec)">Exemplo de uso no Prompt</div>
          <div style="background:var(--panel);padding:8px;border-radius:4px;font-family:monospace;font-size:10px;color:var(--text-sec)">
            Adapte o seguinte tema para o formato {{canal}}:<br><br>
            Tema: {{tema}}<br><br>
            Use o roteiro base abaixo como referência:<br>
            {{saida_anterior}}
          </div>
        </div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="fecharEditorPipeline()">Cancelar</button>
      <button class="btn btn-primary" onclick="salvarPipeline()">Salvar Pipeline</button>
    </div>
  </div>
</div>

<!-- MODAL: EXECUTAR PIPELINE -->
<div class="modal-overlay" id="modal-exec-input">
  <div class="modal" style="max-width:500px">
    <div class="modal-header">
      <h3>Executar Pipeline</h3>
      <button class="modal-close" onclick="document.getElementById('modal-exec-input').classList.remove('active')">&times;</button>
    </div>
    <div class="modal-body">
      <div class="form-row" style="margin-bottom:12px">
        <div class="form-group" style="margin:0">
          <label>Data</label>
          <select id="exec-data" onchange="atualizarExecPreview()" style="font-size:12px">
            <option value="">Selecione...</option>
          </select>
        </div>
        <div class="form-group" style="margin:0">
          <label>Canal</label>
          <select id="exec-canal" onchange="atualizarExecPreview()" style="font-size:12px">
            <option value="">Selecione...</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label>Tema (puxado da célula)</label>
        <textarea id="exec-entrada" rows="3" style="font-size:12px;color:var(--accent)"></textarea>
        <div style="font-size:10px;color:var(--text-sec);margin-top:2px">Editável — ou preencha manualmente se não tiver no grid</div>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-primary" onclick="executarPipeline()">Gerar Roteiro</button>
    </div>
  </div>
</div>

<!-- MODAL: INPUT DE TESTE -->
<div class="modal-overlay" id="modal-test-input">
  <div class="modal" style="max-width:450px">
    <div class="modal-header">
      <h3>Entrada de Teste</h3>
      <button class="modal-close" onclick="document.getElementById('modal-test-input').classList.remove('active')">&times;</button>
    </div>
    <div class="modal-body">
      <div class="form-row" style="margin-bottom:8px">
        <div class="form-group" style="margin:0">
          <label style="font-size:11px">Data</label>
          <select id="test-input-data" onchange="atualizarTestInput()" style="font-size:12px">
            <option value="">--</option>
          </select>
        </div>
        <div class="form-group" style="margin:0">
          <label style="font-size:11px">Canal</label>
          <select id="test-input-canal" onchange="atualizarTestInput()" style="font-size:12px">
            <option value="">--</option>
          </select>
        </div>
      </div>
      <div class="form-group">
        <label style="font-size:11px">Tema / Entrada</label>
        <textarea id="test-input-texto" rows="4" placeholder="Puxe de uma célula acima ou digite manualmente..." style="font-size:12px"></textarea>
      </div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="document.getElementById('modal-test-input').classList.remove('active');if(_testInputResolve){_testInputResolve(null);_testInputResolve=null;}">Cancelar</button>
      <button class="btn btn-primary" onclick="_resolverTestInput()">Usar</button>
    </div>
  </div>
</div>

<!-- MODAL: FILE BROWSER -->
<div class="modal-overlay" id="modal-browser">
  <div class="modal" style="max-width:550px">
    <div class="modal-header">
      <h3>Selecionar</h3>
      <button class="modal-close" onclick="fecharBrowser()">&times;</button>
    </div>
    <div class="modal-body">
      <div class="file-breadcrumb" id="browser-breadcrumb"></div>
      <div class="file-list" id="browser-list"></div>
    </div>
    <div class="modal-footer">
      <button class="btn btn-secondary" onclick="fecharBrowser()">Cancelar</button>
      <button class="btn btn-primary" id="browser-select-btn" onclick="selecionarPath()">Selecionar esta pasta</button>
    </div>
  </div>
</div>

<script>
// === STATE ===
let templates = [];
let editandoId = null;
let browserTarget = null;
let browserMode = 'folder';
let browserPath = '';
let batchInterval = null;

// === NAVIGATION ===
function showPage(page) {
  document.querySelectorAll('.page').forEach(p => p.classList.remove('active'));
  document.querySelectorAll('.sidebar-nav a').forEach(a => a.classList.remove('active'));
  document.getElementById('page-' + page).classList.add('active');
  document.querySelector('[data-page="' + page + '"]').classList.add('active');
  if (page === 'templates') carregarTemplates();
  if (page === 'batch') carregarBatch();
  if (page === 'rules') carregarRegras();
  if (page === 'historico') carregarHistorico();
  if (page === 'narracao') { carregarNarracao().then(function(){ renderBatchCards(); }); }
  if (page === 'temas') { carregarPipelines(); carregarTemas().then(function(){ atualizarLoteDataSelect(); }); document.getElementById('chat-toggle-btn').style.display = 'block'; }
  else { document.getElementById('chat-toggle-btn').style.display = 'none'; document.getElementById('chat-panel').style.display = 'none'; }
  if (page === 'roteiros') carregarPipelines();
  if (page === 'config') carregarConfig();
  if (page === 'thumbnail') { carregarThumbPage(); }
  if (page === 'monitor') { refreshMonitor(); startMonitorPolling(); }
  else { stopMonitorPolling(); }
}

// === TEMPLATES ===
async function carregarTemplates() {
  const res = await fetch('/api/templates');
  templates = await res.json();
  renderTemplates();
}

function renderTemplates() {
  const grid = document.getElementById('templates-grid');
  const empty = document.getElementById('templates-empty');
  if (!templates.length) { grid.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  grid.innerHTML = templates.map(t => `
    <div class="card" onclick="editarTemplate('${t.id}')">
      <div class="card-header">
        <span class="card-title">${t.nome || 'Sem nome'}</span>
        <span class="card-tag">${t.tag || t.id}</span>
      </div>
      <div class="card-info">
        Idioma: ${(t.idioma||'en').toUpperCase()}<br>
        Fundo: ${t.tipo_fundo || 'imagens'} | Legenda: Estilo ${t.estilo_legenda || 1}<br>
        Resolução: ${Array.isArray(t.resolucao) ? t.resolucao.join('x') : t.resolucao || '1920x1080'}
      </div>
      <div class="card-actions">
        <button class="btn btn-secondary btn-sm" onclick="event.stopPropagation();duplicarTemplate('${t.id}')">Duplicar</button>
        <button class="btn btn-danger btn-sm" onclick="event.stopPropagation();deletarTemplate('${t.id}')">Excluir</button>
      </div>
    </div>
  `).join('');
}

async function abrirEditor(id) {
  editandoId = id || null;
  document.getElementById('editor-title').textContent = id ? 'Editar Template' : 'Novo Template';

  await carregarFontes();

  // Reset tabs
  showEditorTab('geral');

  if (id) {
    const t = templates.find(x => x.id === id);
    if (!t) return;
    document.getElementById('ed-nome').value = t.nome || '';
    document.getElementById('ed-tag').value = t.tag || '';
    document.getElementById('ed-idioma').value = t.idioma || 'en';
    const res = Array.isArray(t.resolucao) ? t.resolucao.join('x') : t.resolucao || '1920x1080';
    document.getElementById('ed-resolucao').value = res;
    document.getElementById('ed-fps').value = t.fps || 30;
    document.getElementById('ed-tipo-fundo').value = t.tipo_fundo || 'imagens';
    document.getElementById('ed-video-loop').checked = t.video_loop !== false;
    document.getElementById('ed-pasta-imagens').value = t.pasta_imagens || '';
    document.getElementById('ed-duracao-imagem').value = t.duracao_por_imagem || 10;
    document.getElementById('ed-zoom-ratio').value = t.zoom_ratio || 1.04;
    document.getElementById('zoom-val').textContent = t.zoom_ratio || 1.04;
    document.getElementById('ed-efeito-zoom').checked = t.efeito_zoom !== false;
    var aj = t.ajustes || {};
    document.getElementById('ed-ajustes-random').checked = !!aj.randomizar;
    _setAjuste('exposicao', aj.exposicao || 0);
    _setAjuste('contraste', aj.contraste || t.ajuste_contraste || 1.0);
    _setAjuste('realces', aj.realces || 0);
    _setAjuste('sombras', aj.sombras || 0);
    _setAjuste('brancos', aj.brancos || 0);
    _setAjuste('pretos', aj.pretos || 0);
    _setAjuste('temperatura', aj.temperatura || 0);
    _setAjuste('tonalidade', aj.tonalidade || 0);
    _setAjuste('saturacao', aj.saturacao || 1.0);
    _setAjuste('brilho', aj.brilho || t.ajuste_brilho || 0);
    _setAjuste('vinheta', aj.vinheta || 0);
    // Voz
    var voz = t.narracao_voz || {};
    if (voz.provider) {
      document.getElementById('ed-voz-provider').value = voz.provider;
    }
    filtrarVozesTemplate();
    if (voz.voice_id) {
      setTimeout(function(){ document.getElementById('ed-voz-id').value = voz.voice_id; }, 200);
    }
    document.getElementById('ed-voz-speed').value = voz.speed || 1.0;
    document.getElementById('voz-speed-val').textContent = (voz.speed || 1.0) + 'x';
    document.getElementById('ed-voz-pitch').value = voz.pitch || 0;
    document.getElementById('voz-pitch-val').textContent = voz.pitch || 0;
    document.getElementById('ed-trilha').value = t.trilha_sonora || '';
    document.getElementById('ed-trilha-vol').value = t.trilha_volume || 0.15;
    document.getElementById('trilha-vol-val').textContent = Math.round((t.trilha_volume||0.15)*100)+'%';
    document.getElementById('ed-narracao-vol').value = t.narracao_volume || 1.0;
    document.getElementById('narracao-vol-val').textContent = Math.round((t.narracao_volume||1.0)*100)+'%';
    document.getElementById('ed-estilo-legenda').value = t.estilo_legenda || 1;
    const lc = t.legenda_config || {};
    document.getElementById('ed-legenda-fonte').value = lc.fonte || 'Arial';
    document.getElementById('ed-legenda-tamanho').value = lc.tamanho || 24;
    document.getElementById('ed-legenda-max-linhas').value = lc.max_linhas || 2;
    document.getElementById('ed-legenda-max-chars').value = lc.max_chars || 42;
    document.getElementById('ed-legenda-cor').value = lc.cor || '#FFFFFF';
    document.getElementById('ed-legenda-cor-outline').value = lc.cor_outline || '#000000';
    document.getElementById('ed-legenda-outline-espessura').value = lc.outline_espessura != null ? lc.outline_espessura : 2;
    document.getElementById('outline-esp-val').textContent = lc.outline_espessura != null ? lc.outline_espessura : 2;
    document.getElementById('ed-legenda-sombra').value = lc.sombra || 0;
    document.getElementById('sombra-leg-val').textContent = lc.sombra || 0;
    document.getElementById('ed-legenda-maiuscula').checked = !!lc.maiuscula;
    document.getElementById('ed-legenda-bold').checked = lc.bold !== false;
    document.getElementById('ed-legenda-posicao').value = lc.posicao || 'bottom';
    document.getElementById('ed-legenda-x').value = lc.posicao_x || 50;
    document.getElementById('legenda-x-val').textContent = (lc.posicao_x || 50) + '%';
    document.getElementById('ed-legenda-y').value = lc.posicao_y || 85;
    document.getElementById('legenda-y-val').textContent = (lc.posicao_y || 85) + '%';
    togglePosicaoCustom();
    document.getElementById('ed-pasta-saida').value = t.pasta_saida || '';
    document.getElementById('ed-formato-nome').value = t.formato_nome_saida || '{tag}_{data}_{sequencia}';
    document.getElementById('ed-min-roteiro-chars').value = t.min_roteiro_chars || 15000;
    document.getElementById('ed-proxy').value = t.proxy || '';
    document.getElementById('ed-link-destino').value = t.link_destino || '';

    // Regras
    var regras = t.regras || {};
    var subs = regras.substituicoes || {};
    document.getElementById('ed-regras-subs').value = Object.entries(subs).map(function(e){ return e[0]+'='+e[1]; }).join('\\n');
    document.getElementById('ed-regras-max-chars').value = regras.max_chars_linha || 42;
    document.getElementById('ed-regras-max-linhas').value = regras.max_linhas || 2;
    document.getElementById('ed-regras-hesitacoes').checked = regras.remover_hesitacoes !== false;
    document.getElementById('ed-regras-capitalizar').checked = regras.capitalizar_inicio !== false;
    document.getElementById('ed-regras-remover').value = (regras.palavras_remover || []).join('\\n');

    // Overlays
    renderOverlays(t.overlays || []);

    // CTA
    var cta = t.cta || {};
    // Moldura
    var moldura = t.moldura || {};
    document.getElementById('ed-moldura-arquivo').value = moldura.arquivo || '';
    document.getElementById('ed-moldura-tipo').value = moldura.tipo || 'chromakey';
    document.getElementById('ed-moldura-opacidade').value = moldura.opacidade || 1.0;
    document.getElementById('moldura-op-val').textContent = Math.round((moldura.opacidade || 1.0) * 100) + '%';

    document.getElementById('ed-cta-arquivo').value = cta.arquivo || '';
    document.getElementById('ed-cta-inicio').value = cta.inicio || 30;
    document.getElementById('ed-cta-duracao').value = cta.duracao || 8;
    document.getElementById('ed-cta-intervalo').value = cta.intervalo || 300;
    document.getElementById('ed-cta-escala').value = cta.escala || 0.25;
    document.getElementById('cta-escala-val').textContent = Math.round((cta.escala || 0.25) * 100) + '%';
    document.getElementById('ed-cta-posicao').value = cta.posicao || 'bottom-right';
  } else {
    // Limpar form
    document.querySelectorAll('.modal-body input[type=text]').forEach(i => i.value = '');
    document.getElementById('ed-nome').value = '';
    document.getElementById('ed-duracao-imagem').value = 10;
    document.getElementById('ed-zoom-ratio').value = 1.04;
    document.getElementById('zoom-val').textContent = '1.04';
    document.getElementById('ed-efeito-zoom').checked = true;
    resetAjustes();
    document.getElementById('ed-voz-provider').value = 'all';
    document.getElementById('ed-voz-id').innerHTML = '<option value="">Nenhuma (manual)</option>';
    document.getElementById('ed-voz-speed').value = 1.0;
    document.getElementById('voz-speed-val').textContent = '1.0x';
    document.getElementById('ed-voz-pitch').value = 0;
    document.getElementById('voz-pitch-val').textContent = '0';
    document.getElementById('ed-trilha-vol').value = 0.15;
    document.getElementById('trilha-vol-val').textContent = '15%';
    document.getElementById('ed-narracao-vol').value = 1.0;
    document.getElementById('narracao-vol-val').textContent = '100%';
    document.getElementById('ed-estilo-legenda').value = 1;
    document.getElementById('ed-legenda-fonte').value = 'Arial';
    document.getElementById('ed-legenda-tamanho').value = 24;
    document.getElementById('ed-legenda-max-linhas').value = 2;
    document.getElementById('ed-legenda-max-chars').value = 42;
    document.getElementById('ed-legenda-cor').value = '#FFFFFF';
    document.getElementById('ed-legenda-cor-outline').value = '#000000';
    document.getElementById('ed-legenda-outline-espessura').value = 2;
    document.getElementById('outline-esp-val').textContent = '2';
    document.getElementById('ed-legenda-sombra').value = 0;
    document.getElementById('sombra-leg-val').textContent = '0';
    document.getElementById('ed-legenda-maiuscula').checked = false;
    document.getElementById('ed-legenda-bold').checked = true;
    document.getElementById('ed-legenda-posicao').value = 'bottom';
    document.getElementById('ed-legenda-x').value = 50;
    document.getElementById('legenda-x-val').textContent = '50%';
    document.getElementById('ed-legenda-y').value = 85;
    document.getElementById('legenda-y-val').textContent = '85%';
    togglePosicaoCustom();
    document.getElementById('ed-formato-nome').value = '{tag}_{data}_{sequencia}';
    document.getElementById('ed-min-roteiro-chars').value = 15000;
    document.getElementById('ed-proxy').value = '';
    document.getElementById('ed-link-destino').value = '';
    document.getElementById('ed-regras-subs').value = '';
    document.getElementById('ed-regras-max-chars').value = 42;
    document.getElementById('ed-regras-max-linhas').value = 2;
    document.getElementById('ed-regras-hesitacoes').checked = true;
    document.getElementById('ed-regras-capitalizar').checked = true;
    document.getElementById('ed-regras-remover').value = '';
    renderOverlays([]);
    document.getElementById('ed-moldura-arquivo').value = '';
    document.getElementById('ed-moldura-tipo').value = 'chromakey';
    document.getElementById('ed-moldura-opacidade').value = 1.0;
    document.getElementById('moldura-op-val').textContent = '100%';
    document.getElementById('ed-cta-arquivo').value = '';
    document.getElementById('ed-cta-inicio').value = 30;
    document.getElementById('ed-cta-duracao').value = 8;
    document.getElementById('ed-cta-intervalo').value = 300;
    document.getElementById('ed-cta-escala').value = 0.25;
    document.getElementById('cta-escala-val').textContent = '25%';
    document.getElementById('ed-cta-posicao').value = 'bottom-right';
  }

  previewEstilo();
  toggleFundoOpcoes();
  document.getElementById('modal-editor').classList.add('active');
}

function editarTemplate(id) { abrirEditor(id); }
function fecharEditor() { document.getElementById('modal-editor').classList.remove('active'); }

function showEditorTab(tab, el) {
  document.querySelectorAll('#modal-editor .tab-content').forEach(t => t.classList.remove('active'));
  document.querySelectorAll('#modal-editor .tab').forEach(t => t.classList.remove('active'));
  document.getElementById('tab-' + tab).classList.add('active');
  if (el) {
    el.classList.add('active');
  } else {
    document.querySelector('#modal-editor .tab').classList.add('active');
  }
}

function toggleFundoOpcoes() {
  const tipo = document.getElementById('ed-tipo-fundo').value;
  document.getElementById('opcoes-imagens').style.display = tipo === 'imagens' ? 'block' : 'none';
  document.getElementById('opcoes-video-loop').style.display = tipo === 'videos' ? 'block' : 'none';
}

// Overlays dinâmicos
function _ovHTML(i, caminho, opacidade) {
  const div = document.createElement('div');
  div.style.cssText = 'display:flex;gap:8px;align-items:flex-end;margin-bottom:12px';
  div.dataset.ov = i;
  div.innerHTML = '<div class="form-group" style="flex:1;margin:0">'
    + '<label>Overlay ' + (i+1) + '</label>'
    + '<div class="input-with-btn">'
    + '<input type="text" class="ov-path" value="' + (caminho||'') + '" placeholder="Caminho do overlay...">'
    + '<button class="btn btn-secondary btn-sm" type="button">Buscar</button>'
    + '</div></div>'
    + '<div class="form-group" style="width:120px;margin:0">'
    + '<label>Opacidade</label>'
    + '<input type="number" class="ov-opac" value="' + (opacidade||0.3) + '" min="0" max="1" step="0.05">'
    + '</div>'
    + '<button class="btn btn-danger btn-sm" type="button" style="margin-bottom:1px">X</button>';
  div.querySelector('.btn-secondary').addEventListener('click', function(){ abrirBrowser(div.querySelector('.ov-path'),'file'); });
  div.querySelector('.btn-danger').addEventListener('click', function(){ div.remove(); });
  return div;
}

function renderOverlays(overlays) {
  const container = document.getElementById('overlays-list');
  container.innerHTML = '';
  (overlays||[]).forEach(function(ov, i) {
    container.appendChild(_ovHTML(i, ov.caminho, ov.opacidade));
  });
}

function adicionarOverlay() {
  const container = document.getElementById('overlays-list');
  container.appendChild(_ovHTML(container.children.length, '', 0.3));
}

function coletarOverlays() {
  const items = document.querySelectorAll('#overlays-list > div');
  return Array.from(items).map(div => ({
    caminho: div.querySelector('.ov-path').value,
    opacidade: parseFloat(div.querySelector('.ov-opac').value) || 0.3
  })).filter(o => o.caminho);
}

function _coletarRegras() {
  var subsText = document.getElementById('ed-regras-subs').value.trim();
  var subs = {};
  if (subsText) {
    subsText.split('\\n').forEach(function(line) {
      var parts = line.split('=');
      if (parts.length >= 2) {
        var key = parts[0].trim();
        var val = parts.slice(1).join('=').trim();
        if (key && val) subs[key] = val;
      }
    });
  }
  var removerText = document.getElementById('ed-regras-remover').value.trim();
  var remover = removerText ? removerText.split('\\n').map(function(l){ return l.trim(); }).filter(Boolean) : [];
  return {
    substituicoes: subs,
    max_chars_linha: parseInt(document.getElementById('ed-regras-max-chars').value) || 42,
    max_linhas: parseInt(document.getElementById('ed-regras-max-linhas').value) || 2,
    remover_hesitacoes: document.getElementById('ed-regras-hesitacoes').checked,
    capitalizar_inicio: document.getElementById('ed-regras-capitalizar').checked,
    palavras_remover: remover,
  };
}

async function salvarTemplate() {
  const res_str = document.getElementById('ed-resolucao').value;
  const res_parts = res_str.split('x').map(Number);

  const dados = {
    id: editandoId || document.getElementById('ed-tag').value.toLowerCase().replace(/[^a-z0-9]/g,'-') || undefined,
    nome: document.getElementById('ed-nome').value,
    tag: document.getElementById('ed-tag').value,
    idioma: document.getElementById('ed-idioma').value,
    resolucao: res_parts,
    fps: parseInt(document.getElementById('ed-fps').value),
    pasta_imagens: document.getElementById('ed-pasta-imagens').value,
    tipo_fundo: document.getElementById('ed-tipo-fundo').value,
    video_loop: document.getElementById('ed-video-loop').checked,
    duracao_por_imagem: parseInt(document.getElementById('ed-duracao-imagem').value),
    efeito_zoom: document.getElementById('ed-efeito-zoom').checked,
    zoom_ratio: parseFloat(document.getElementById('ed-zoom-ratio').value),
    overlays: coletarOverlays(),
    ajustes: {
      randomizar: document.getElementById('ed-ajustes-random').checked,
      exposicao: parseFloat(document.getElementById('ed-exposicao').value),
      contraste: parseFloat(document.getElementById('ed-contraste').value),
      realces: parseFloat(document.getElementById('ed-realces').value),
      sombras: parseFloat(document.getElementById('ed-sombras').value),
      brancos: parseFloat(document.getElementById('ed-brancos').value),
      pretos: parseFloat(document.getElementById('ed-pretos').value),
      temperatura: parseFloat(document.getElementById('ed-temperatura').value),
      tonalidade: parseFloat(document.getElementById('ed-tonalidade').value),
      saturacao: parseFloat(document.getElementById('ed-saturacao').value),
      brilho: parseFloat(document.getElementById('ed-brilho').value),
      vinheta: parseFloat(document.getElementById('ed-vinheta').value),
    },
    narracao_voz: {
      voice_id: document.getElementById('ed-voz-id').value,
      provider: document.getElementById('ed-voz-provider').value,
      speed: parseFloat(document.getElementById('ed-voz-speed').value) || 1.0,
      pitch: parseInt(document.getElementById('ed-voz-pitch').value) || 0,
    },
    trilha_sonora: document.getElementById('ed-trilha').value,
    trilha_volume: parseFloat(document.getElementById('ed-trilha-vol').value),
    narracao_volume: parseFloat(document.getElementById('ed-narracao-vol').value),
    estilo_legenda: parseInt(document.getElementById('ed-estilo-legenda').value),
    legenda_config: {
      fonte: document.getElementById('ed-legenda-fonte').value,
      tamanho: parseInt(document.getElementById('ed-legenda-tamanho').value),
      max_linhas: parseInt(document.getElementById('ed-legenda-max-linhas').value),
      max_chars: parseInt(document.getElementById('ed-legenda-max-chars').value),
      cor: document.getElementById('ed-legenda-cor').value,
      cor_outline: document.getElementById('ed-legenda-cor-outline').value,
      cor_primaria: hexToAss(document.getElementById('ed-legenda-cor').value),
      cor_outline_ass: hexToAss(document.getElementById('ed-legenda-cor-outline').value),
      outline_espessura: parseInt(document.getElementById('ed-legenda-outline-espessura').value),
      sombra: parseInt(document.getElementById('ed-legenda-sombra').value),
      maiuscula: document.getElementById('ed-legenda-maiuscula').checked,
      bold: document.getElementById('ed-legenda-bold').checked,
      posicao: document.getElementById('ed-legenda-posicao').value,
      posicao_x: parseInt(document.getElementById('ed-legenda-x').value),
      posicao_y: parseInt(document.getElementById('ed-legenda-y').value),
    },
    pasta_saida: document.getElementById('ed-pasta-saida').value,
    formato_nome_saida: document.getElementById('ed-formato-nome').value,
    min_roteiro_chars: parseInt(document.getElementById('ed-min-roteiro-chars').value) || 15000,
    proxy: document.getElementById('ed-proxy').value,
    link_destino: document.getElementById('ed-link-destino').value,
    moldura: {
      arquivo: document.getElementById('ed-moldura-arquivo').value,
      tipo: document.getElementById('ed-moldura-tipo').value,
      opacidade: parseFloat(document.getElementById('ed-moldura-opacidade').value) || 1.0,
    },
    cta: {
      arquivo: document.getElementById('ed-cta-arquivo').value,
      inicio: parseInt(document.getElementById('ed-cta-inicio').value) || 30,
      duracao: parseInt(document.getElementById('ed-cta-duracao').value) || 8,
      intervalo: parseInt(document.getElementById('ed-cta-intervalo').value) || 300,
      escala: parseFloat(document.getElementById('ed-cta-escala').value) || 0.25,
      posicao: document.getElementById('ed-cta-posicao').value,
    },
    regras: _coletarRegras(),
  };

  const url = editandoId ? `/api/templates/${editandoId}` : '/api/templates';
  const method = editandoId ? 'PUT' : 'POST';
  const res = await fetch(url, { method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(dados) });

  if (res.ok) {
    toast('Template salvo com sucesso!', 'success');
    fecharEditor();
    carregarTemplates();
  } else {
    const err = await res.json();
    toast('Erro: ' + (err.detail || 'Falha ao salvar'), 'error');
  }
}

async function deletarTemplate(id) {
  if (!confirm('Tem certeza que deseja excluir este template?')) return;
  const res = await fetch(`/api/templates/${id}`, { method:'DELETE' });
  if (res.ok) { toast('Template excluído', 'success'); carregarTemplates(); }
}

async function duplicarTemplate(id) {
  const t = templates.find(x => x.id === id);
  if (!t) return;
  const novoId = t.tag.toLowerCase().replace(/[^a-z0-9]/g,'-') + '-' + Math.random().toString(36).substring(2,6);
  const novo = {...t, id: novoId, nome: t.nome + ' (cópia)', tag: t.tag + '2'};
  const res = await fetch('/api/templates', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(novo) });
  if (res.ok) { toast('Template duplicado', 'success'); carregarTemplates(); }
}

// === LEGENDA PREVIEW ===
let fontesCarregadas = false;
async function carregarFontes() {
  if (fontesCarregadas) return;
  const res = await fetch('/api/fonts');
  const fontes = await res.json();
  const sel = document.getElementById('ed-legenda-fonte');
  sel.innerHTML = fontes.map(function(f) {
    return '<option value="' + f + '" style="font-family:' + f + '">' + f + '</option>';
  }).join('');
  fontesCarregadas = true;
}

var _ctaImagemCarregada = false;

async function previewCTA() {
  var ctaPath = document.getElementById('ed-cta-arquivo').value;
  var escala = parseFloat(document.getElementById('ed-cta-escala').value) || 0.25;
  var posicao = document.getElementById('ed-cta-posicao').value;
  var previewEl = document.getElementById('cta-preview');
  var imgEl = document.getElementById('cta-preview-img');

  // Carregar imagem só se ainda não tiver ou se o path mudou
  if (!_ctaImagemCarregada && ctaPath) {
    try {
      var res = await fetch('/api/preview/frame?path=' + encodeURIComponent(ctaPath));
      if (!res.ok) { toast('Falha ao extrair frame do CTA', 'error'); return; }
      var data = await res.json();
      imgEl.src = 'data:' + data.mime + ';base64,' + data.base64;
      _ctaImagemCarregada = true;
    } catch(e) {
      toast('Erro: ' + e.message, 'error');
      return;
    }
  }
  if (!ctaPath || !_ctaImagemCarregada) return;

  previewEl.style.display = 'block';
  imgEl.style.width = (escala * 100) + '%';
  imgEl.style.height = 'auto';
  imgEl.style.zIndex = '1';
  imgEl.style.mixBlendMode = 'screen';

  // Reset posição
  imgEl.style.top = 'auto'; imgEl.style.bottom = 'auto';
  imgEl.style.left = 'auto'; imgEl.style.right = 'auto';
  imgEl.style.transform = 'none';

  if (posicao === 'bottom-right') { imgEl.style.bottom = '8%'; imgEl.style.right = '3%'; }
  else if (posicao === 'bottom-center') { imgEl.style.bottom = '8%'; imgEl.style.left = '50%'; imgEl.style.transform = 'translateX(-50%)'; }
  else if (posicao === 'bottom-left') { imgEl.style.bottom = '8%'; imgEl.style.left = '3%'; }
  else if (posicao === 'top-right') { imgEl.style.top = '3%'; imgEl.style.right = '3%'; }
  else if (posicao === 'top-center') { imgEl.style.top = '3%'; imgEl.style.left = '50%'; imgEl.style.transform = 'translateX(-50%)'; }
  else if (posicao === 'top-left') { imgEl.style.top = '3%'; imgEl.style.left = '3%'; }
  else if (posicao === 'center') { imgEl.style.top = '50%'; imgEl.style.left = '50%'; imgEl.style.transform = 'translate(-50%,-50%)'; }
}

function togglePreviewBg() {
  var el = document.getElementById('legenda-preview');
  var btn = document.getElementById('btn-preview-bg');
  var bg = el.dataset.bg;
  if (bg === 'dark') {
    el.style.background = '#eee';
    el.dataset.bg = 'light';
    btn.textContent = 'Fundo: Claro';
  } else if (bg === 'light') {
    el.style.background = 'linear-gradient(135deg, #2a5298, #1e3c72, #0f2027)';
    el.dataset.bg = 'gradient';
    btn.textContent = 'Fundo: Imagem';
  } else {
    el.style.background = '#111';
    el.dataset.bg = 'dark';
    btn.textContent = 'Fundo: Escuro';
  }
}

function previewEstilo() {
  var estilo = parseInt(document.getElementById('ed-estilo-legenda').value);
  var el = document.getElementById('preview-text');
  var fonte = document.getElementById('ed-legenda-fonte').value || 'Arial';
  var tamanho = parseInt(document.getElementById('ed-legenda-tamanho').value) || 24;
  var maxLinhas = parseInt(document.getElementById('ed-legenda-max-linhas').value) || 2;

  // Preset values as starting point
  var presets = {
    1: { color:'#FFFFFF', outline:'#000000', outlineW:2, weight:true, upper:false },
    2: { color:'#FFFFFF', outline:'#000000', outlineW:3, weight:true, upper:true },
    3: { color:'#FFD700', outline:'#000000', outlineW:2, weight:true, upper:false },
    4: { color:'#FFFFFF', outline:'#000000', outlineW:0, weight:false, upper:false },
    5: { color:'#FFD700', outline:'#000000', outlineW:0, weight:false, upper:false },
  };
  var p = presets[estilo] || presets[1];

  // Custom overrides from the new controls
  var corEl = document.getElementById('ed-legenda-cor');
  var corOutEl = document.getElementById('ed-legenda-cor-outline');
  var outEspEl = document.getElementById('ed-legenda-outline-espessura');
  var maiuscEl = document.getElementById('ed-legenda-maiuscula');
  var boldEl = document.getElementById('ed-legenda-bold');

  // If the controls haven't been manually changed from preset defaults, sync them
  var cor = corEl.value || p.color;
  var corOutline = corOutEl.value || p.outline;
  var outEsp = parseInt(outEspEl.value);
  var maiusc = maiuscEl.checked;
  var bold = boldEl.checked;

  // Update hex display labels
  document.getElementById('legenda-cor-hex').textContent = cor.toUpperCase();
  document.getElementById('legenda-cor-outline-hex').textContent = corOutline.toUpperCase();

  // Build text-shadow from outline + drop shadow
  var shadow = 'none';
  var parts = [];
  var dropSombra = parseInt(document.getElementById('ed-legenda-sombra').value) || 0;
  if (outEsp > 0) {
    var px = outEsp;
    parts.push(px + 'px ' + px + 'px 0 ' + corOutline);
    parts.push('-' + px + 'px -' + px + 'px 0 ' + corOutline);
    parts.push(px + 'px -' + px + 'px 0 ' + corOutline);
    parts.push('-' + px + 'px ' + px + 'px 0 ' + corOutline);
    if (outEsp >= 3) {
      parts.push('0 ' + px + 'px 0 ' + corOutline);
      parts.push('0 -' + px + 'px 0 ' + corOutline);
      parts.push(px + 'px 0 0 ' + corOutline);
      parts.push('-' + px + 'px 0 0 ' + corOutline);
    }
  }
  if (dropSombra > 0) {
    var ds = dropSombra * 2;
    parts.push(ds + 'px ' + ds + 'px ' + ds + 'px rgba(0,0,0,0.8)');
  }
  if (parts.length) shadow = parts.join(',');

  var text;
  if (maxLinhas === 1) {
    text = 'The universe is sending you';
  } else {
    text = 'The universe is sending you<br>a powerful message today';
  }
  if (maiusc) text = text.toUpperCase();

  // Posição
  var posicao = document.getElementById('ed-legenda-posicao').value;
  var container = document.getElementById('legenda-preview');

  el.innerHTML = text;
  el.style.fontFamily = fonte;
  el.style.fontSize = tamanho + 'px';
  el.style.color = cor;
  el.style.textShadow = shadow;
  el.style.fontWeight = bold ? 'bold' : 'normal';
  el.style.textTransform = maiusc ? 'uppercase' : 'none';

  // Reset positioning
  el.style.position = 'absolute';
  el.style.left = '50%';
  el.style.transform = 'translateX(-50%)';
  el.style.width = '90%';

  if (posicao === 'bottom') {
    el.style.bottom = '8%';
    el.style.top = 'auto';
  } else if (posicao === 'center') {
    el.style.top = '50%';
    el.style.bottom = 'auto';
    el.style.transform = 'translate(-50%, -50%)';
  } else if (posicao === 'top') {
    el.style.top = '8%';
    el.style.bottom = 'auto';
  } else if (posicao === 'custom') {
    var xPct = parseInt(document.getElementById('ed-legenda-x').value);
    var yPct = parseInt(document.getElementById('ed-legenda-y').value);
    el.style.left = xPct + '%';
    el.style.top = yPct + '%';
    el.style.bottom = 'auto';
    el.style.transform = 'translate(-50%, -50%)';
  }
}

// === AJUSTES HELPERS ===
function _setAjuste(nome, valor) {
  var el = document.getElementById('ed-' + nome);
  var valEl = document.getElementById(nome + '-val');
  if (el) el.value = valor;
  if (valEl) valEl.textContent = valor;
}

function resetAjustes() {
  document.getElementById('ed-ajustes-random').checked = false;
  _setAjuste('exposicao', 0);
  _setAjuste('contraste', 1.0);
  _setAjuste('realces', 0);
  _setAjuste('sombras', 0);
  _setAjuste('brancos', 0);
  _setAjuste('pretos', 0);
  _setAjuste('temperatura', 0);
  _setAjuste('tonalidade', 0);
  _setAjuste('saturacao', 1.0);
  _setAjuste('brilho', 0);
  _setAjuste('vinheta', 0);
}

function togglePosicaoCustom() {
  var posicao = document.getElementById('ed-legenda-posicao').value;
  document.getElementById('posicao-custom').style.display = posicao === 'custom' ? 'block' : 'none';
}

// Sync preset selector to custom controls
function syncPresetToControls() {
  var estilo = parseInt(document.getElementById('ed-estilo-legenda').value);
  var presets = {
    1: { color:'#FFFFFF', outline:'#000000', outlineW:2, weight:true, upper:false },
    2: { color:'#FFFFFF', outline:'#000000', outlineW:3, weight:true, upper:true },
    3: { color:'#FFD700', outline:'#000000', outlineW:2, weight:true, upper:false },
    4: { color:'#FFFFFF', outline:'#000000', outlineW:0, weight:false, upper:false },
    5: { color:'#FFD700', outline:'#000000', outlineW:0, weight:false, upper:false },
  };
  var p = presets[estilo] || presets[1];
  document.getElementById('ed-legenda-cor').value = p.color;
  document.getElementById('ed-legenda-cor-outline').value = p.outline;
  document.getElementById('ed-legenda-outline-espessura').value = p.outlineW;
  document.getElementById('outline-esp-val').textContent = p.outlineW;
  document.getElementById('ed-legenda-maiuscula').checked = p.upper;
  document.getElementById('ed-legenda-bold').checked = p.weight;
  previewEstilo();
}

// === HELPER: HEX to ASS COLOR ===
function hexToAss(hex) {
  // Convert #RRGGBB to &H00BBGGRR (ASS/SSA format)
  hex = hex.replace('#','');
  var r = hex.substring(0,2);
  var g = hex.substring(2,4);
  var b = hex.substring(4,6);
  return '&H00' + b.toUpperCase() + g.toUpperCase() + r.toUpperCase();
}

// === KEN BURNS PREVIEW ===
async function previewKenBurns() {
  var pasta = document.getElementById('ed-pasta-imagens').value;
  if (!pasta) { toast('Selecione uma pasta de imagens primeiro', 'error'); return; }
  var zoomRatio = parseFloat(document.getElementById('ed-zoom-ratio').value) || 1.04;
  var duracao = parseInt(document.getElementById('ed-duracao-imagem').value) || 10;
  var previewEl = document.getElementById('kenburns-preview');
  var imgEl = document.getElementById('kenburns-img');

  try {
    var res = await fetch('/api/preview/image?path=' + encodeURIComponent(pasta));
    if (!res.ok) { toast('Falha ao carregar imagem de preview', 'error'); return; }
    var data = await res.json();
    imgEl.src = 'data:' + data.mime + ';base64,' + data.base64;
    previewEl.style.display = 'block';
    // Apply Ken Burns animation
    imgEl.style.animation = 'none';
    imgEl.offsetHeight; // trigger reflow
    previewEl.style.setProperty('--kb-zoom', zoomRatio);
    imgEl.style.animation = 'kenburns ' + duracao + 's ease-in-out infinite alternate';
  } catch(e) {
    toast('Erro ao gerar preview: ' + e.message, 'error');
  }
}

// === OVERLAY PREVIEW ===
async function previewOverlay() {
  var overlayItems = document.querySelectorAll('#overlays-list > div');
  if (!overlayItems.length) { toast('Adicione um overlay primeiro', 'error'); return; }
  var firstOv = overlayItems[0];
  var ovPath = firstOv.querySelector('.ov-path').value;
  var ovOpac = parseFloat(firstOv.querySelector('.ov-opac').value) || 0.3;
  if (!ovPath) { toast('Preencha o caminho do overlay', 'error'); return; }

  var pasta = document.getElementById('ed-pasta-imagens').value;
  var previewEl = document.getElementById('overlay-preview');
  var bgImg = document.getElementById('overlay-bg-img');
  var fgImg = document.getElementById('overlay-fg-img');

  try {
    // Get overlay first frame
    var ovRes = await fetch('/api/preview/frame?path=' + encodeURIComponent(ovPath));
    if (!ovRes.ok) { toast('Falha ao extrair frame do overlay', 'error'); return; }
    var ovData = await ovRes.json();
    fgImg.src = 'data:' + ovData.mime + ';base64,' + ovData.base64;
    fgImg.style.opacity = ovOpac;

    // Get background image if available
    if (pasta) {
      var bgRes = await fetch('/api/preview/image?path=' + encodeURIComponent(pasta));
      if (bgRes.ok) {
        var bgData = await bgRes.json();
        bgImg.src = 'data:' + bgData.mime + ';base64,' + bgData.base64;
        bgImg.style.display = 'block';
      }
    } else {
      bgImg.style.display = 'none';
    }
    previewEl.style.display = 'flex';
  } catch(e) {
    toast('Erro ao gerar preview: ' + e.message, 'error');
  }
}

// === AUDIO PREVIEW ===
var _audioPreviewCurrent = null;
async function previewAudio() {
  var trilha = document.getElementById('ed-trilha').value;
  if (!trilha) { toast('Selecione uma trilha sonora primeiro', 'error'); return; }
  var volume = parseFloat(document.getElementById('ed-trilha-vol').value) || 0.15;
  var statusEl = document.getElementById('audio-preview-status');
  var player = document.getElementById('audio-preview-player');
  var btn = document.getElementById('btn-audio-preview');

  // If playing, stop
  if (_audioPreviewCurrent) {
    player.pause();
    player.src = '';
    _audioPreviewCurrent = null;
    btn.innerHTML = '&#9654; Preview (15s)';
    statusEl.textContent = '';
    return;
  }

  statusEl.textContent = 'Gerando preview...';
  btn.disabled = true;
  try {
    var res = await fetch('/api/preview/audio', {
      method: 'POST',
      headers: {'Content-Type':'application/json'},
      body: JSON.stringify({trilha: trilha, volume: volume})
    });
    if (!res.ok) { toast('Falha ao gerar preview', 'error'); statusEl.textContent = ''; btn.disabled = false; return; }
    var data = await res.json();
    player.src = '/api/preview/audio/play?file=' + encodeURIComponent(data.file);
    player.play();
    _audioPreviewCurrent = data.file;
    btn.innerHTML = '&#9724; Parar';
    btn.disabled = false;
    statusEl.textContent = 'Reproduzindo...';
    player.onended = function() {
      _audioPreviewCurrent = null;
      btn.innerHTML = '&#9654; Preview (15s)';
      statusEl.textContent = '';
    };
  } catch(e) {
    toast('Erro: ' + e.message, 'error');
    statusEl.textContent = '';
    btn.disabled = false;
  }
}

// === FILE BROWSER ===
var _ultimasPastas = JSON.parse(localStorage.getItem('ultimasPastas') || '{}');

function _getBrowserKey(target) {
  if (!target) return '_default';
  return target.id || target.className || '_default';
}

function abrirBrowser(target, mode) {
  if (typeof target === 'string') target = document.getElementById(target);
  browserTarget = target;
  browserMode = mode || 'folder';
  document.getElementById('browser-select-btn').textContent = mode === 'folder' ? 'Selecionar esta pasta' : 'Selecionar';
  // Começar na última pasta deste campo específico
  var key = _getBrowserKey(target);
  var inicio = _ultimasPastas[key] || _ultimasPastas['_default'] || '';
  carregarBrowserPath(inicio);
  document.getElementById('modal-browser').classList.add('active');
}

function fecharBrowser() { document.getElementById('modal-browser').classList.remove('active'); }

async function carregarBrowserPath(path) {
  browserPath = path;
  const res = await fetch('/api/browse?path=' + encodeURIComponent(path));
  const items = await res.json();

  // Breadcrumb
  const bc = document.getElementById('browser-breadcrumb');
  if (!path) {
    bc.innerHTML = '<span onclick="carregarBrowserPath(String.raw``)">Drives</span>';
  } else {
    const parts = path.replace(/\\\\/g,'/').split('/').filter(Boolean);
    let html = '<span onclick="carregarBrowserPath(String.raw``)">Drives</span>';
    let acum = '';
    parts.forEach(p => {
      acum += p + '/';
      const safe = acum.replace(/"/g,'&quot;');
      html += ' / <span onclick="carregarBrowserPath(&quot;' + safe + '&quot;)">' + p + '</span>';
    });
    bc.innerHTML = html;
  }

  // List
  const list = document.getElementById('browser-list');
  list.innerHTML = items.map(item => {
    const safePath = item.path.replace(/"/g,'&quot;');
    if (item.type === 'drive' || item.type === 'folder') {
      return '<div class="file-item folder" onclick="carregarBrowserPath(&quot;' + safePath + '&quot;)">&#128193; ' + item.name + '</div>';
    }
    if (browserMode === 'file') {
      return '<div class="file-item file" onclick="selecionarArquivo(&quot;' + safePath + '&quot;)">' + item.name + '</div>';
    }
    return '<div class="file-item file">' + item.name + '</div>';
  }).join('');
}

function _salvarUltimaPasta(path) {
  var pasta = path.replace(/\\\\/g, '/');
  var lastSlash = pasta.lastIndexOf('/');
  if (lastSlash > 0 && pasta.includes('.')) pasta = pasta.substring(0, lastSlash + 1);
  var key = _getBrowserKey(browserTarget);
  _ultimasPastas[key] = pasta;
  _ultimasPastas['_default'] = pasta;
  localStorage.setItem('ultimasPastas', JSON.stringify(_ultimasPastas));
}

function selecionarPath() {
  if (browserTarget && browserPath) {
    browserTarget.value = browserPath.replace(/\\\\/g, '/');
    _salvarUltimaPasta(browserPath);
    fecharBrowser();
  }
}

function selecionarArquivo(path) {
  if (browserTarget) {
    browserTarget.value = path.replace(/\\\\/g, '/');
    _salvarUltimaPasta(path);
    fecharBrowser();
  }
}

// === BATCH PRODUCTION ===
var _batchMp3Values = JSON.parse(localStorage.getItem('batchMp3Values') || '{}');

var _batchDragIdx = -1;
function _batchDragStart(e) { _batchDragIdx = parseInt(e.target.closest('tr').dataset.bidx); e.dataTransfer.effectAllowed = 'move'; }
function _batchDrop(e) {
  var targetIdx = parseInt(e.target.closest('tr').dataset.bidx);
  if (_batchDragIdx < 0 || _batchDragIdx === targetIdx) return;
  var t = templates.splice(_batchDragIdx, 1)[0];
  templates.splice(targetIdx, 0, t);
  localStorage.setItem('batchOrdem', JSON.stringify(templates.map(function(x){ return x.id; })));
  _batchDragIdx = -1;
  carregarBatch();
}

async function carregarBatch() {
  // Salvar valores atuais antes de reconstruir
  document.querySelectorAll('.batch-mp3').forEach(function(inp) {
    if (inp.value.trim()) _batchMp3Values[inp.dataset.tid] = inp.value.trim();
  });
  localStorage.setItem('batchMp3Values', JSON.stringify(_batchMp3Values));

  const res = await fetch('/api/templates');
  templates = await res.json();
  const tbody = document.getElementById('batch-tbody');
  const empty = document.getElementById('batch-empty');

  if (!templates.length) { tbody.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';

  // Ordenar pela ordem salva
  var batchOrdem = JSON.parse(localStorage.getItem('batchOrdem') || '[]');
  if (batchOrdem.length) {
    templates.sort(function(a, b) {
      var ia = batchOrdem.indexOf(a.id), ib = batchOrdem.indexOf(b.id);
      if (ia < 0) ia = 999; if (ib < 0) ib = 999;
      return ia - ib;
    });
  }

  tbody.innerHTML = templates.map((t, i) =>
    '<tr data-tid="' + t.id + '" data-bidx="' + i + '" draggable="true" ondragstart="_batchDragStart(event)" ondragover="event.preventDefault()" ondrop="_batchDrop(event)" style="cursor:grab">'
    + '<td>' + (i+1) + '</td>'
    + '<td><strong>' + (t.tag||t.id) + '</strong><br><span style="color:var(--text-sec);font-size:12px">' + (t.nome||'') + '</span></td>'
    + '<td><div class="input-with-btn">'
    + '<input type="text" class="batch-mp3" data-tid="' + t.id + '" placeholder="Caminho do MP3..." style="font-size:12px">'
    + '<button class="btn btn-secondary btn-sm batch-browse-btn" type="button">...</button>'
    + '<button class="btn btn-danger btn-sm batch-clear-btn" type="button" style="font-size:10px;padding:2px 6px" title="Remover áudio">X</button>'
    + '</div></td>'
    + '<td><button class="btn btn-primary btn-sm batch-play-btn" data-tid="' + t.id + '" type="button" title="Produzir este template">&#9654;</button></td>'
    + '<td><span class="badge badge-waiting" id="badge-' + t.id + '">Aguardando</span></td>'
    + '<td><div class="progress-circle" id="prog-' + t.id + '"><svg viewBox="0 0 52 52"><circle class="bg" cx="26" cy="26" r="22"/><circle class="fg" cx="26" cy="26" r="22" stroke-dasharray="138.23" stroke-dashoffset="138.23"/></svg><span class="pct">0%</span></div></td>'
    + '<td><span class="timer" id="timer-' + t.id + '" style="font-size:13px;color:var(--text-sec);font-family:monospace">--:--</span></td>'
    + '</tr>'
  ).join('');
  // Bind browse buttons
  tbody.querySelectorAll('.batch-browse-btn').forEach(function(btn){
    btn.addEventListener('click', function(){ abrirBrowser(btn.parentElement.querySelector('input'),'file'); });
  });
  // Bind play individual buttons
  tbody.querySelectorAll('.batch-play-btn').forEach(function(btn){
    btn.addEventListener('click', function(){ iniciarIndividual(btn.dataset.tid); });
  });
  // Bind clear individual buttons
  tbody.querySelectorAll('.batch-clear-btn').forEach(function(btn){
    btn.addEventListener('click', function(){
      var inp = btn.parentElement.querySelector('input');
      if (inp) {
        inp.value = '';
        delete _batchMp3Values[inp.dataset.tid];
        localStorage.setItem('batchMp3Values', JSON.stringify(_batchMp3Values));
      }
    });
  });

  // Restaurar caminhos MP3 salvos
  document.querySelectorAll('.batch-mp3').forEach(function(inp) {
    if (_batchMp3Values[inp.dataset.tid]) inp.value = _batchMp3Values[inp.dataset.tid];
  });

  // Auto-save quando input muda
  document.querySelectorAll('.batch-mp3').forEach(function(inp) {
    inp.addEventListener('change', function() {
      _batchMp3Values[this.dataset.tid] = this.value.trim();
      localStorage.setItem('batchMp3Values', JSON.stringify(_batchMp3Values));
    });
  });

  // Verificar se batch ativo
  checkBatchStatus();
}

function limparTodosAudios() {
  document.querySelectorAll('.batch-mp3').forEach(function(inp) { inp.value = ''; });
  _batchMp3Values = {};
  localStorage.setItem('batchMp3Values', JSON.stringify({}));
  toast('Caminhos removidos', 'success');
}

async function iniciarIndividual(tid) {
  var mp3Input = document.querySelector('.batch-mp3[data-tid="' + tid + '"]');
  if (!mp3Input || !mp3Input.value.trim()) {
    toast('Selecione o MP3 de narração para este template', 'error');
    return;
  }
  var jobs = [{ template_id: tid, mp3: mp3Input.value.trim() }];
  var res = await fetch('/api/batch', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ jobs: jobs })
  });
  if (res.ok) {
    toast('Produção individual iniciada!', 'success');
    document.getElementById('btn-batch-start').style.display = 'none';
    document.getElementById('btn-batch-cancel').style.display = 'inline-flex';
    document.getElementById('batch-progress-global').style.display = 'block';
    batchInterval = setInterval(checkBatchStatus, 2000);
  } else {
    var err = await res.json();
    toast('Erro: ' + (err.detail || 'Falha ao iniciar'), 'error');
  }
}

async function iniciarBatch() {
  const inputs = document.querySelectorAll('.batch-mp3');
  const jobs = [];
  inputs.forEach(inp => {
    if (inp.value.trim()) {
      jobs.push({ template_id: inp.dataset.tid, mp3: inp.value.trim() });
    }
  });

  if (!jobs.length) { toast('Selecione pelo menos um MP3 de narração', 'error'); return; }

  const res = await fetch('/api/batch', {
    method: 'POST',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify({ jobs })
  });

  if (res.ok) {
    toast('Produção iniciada!', 'success');
    document.getElementById('btn-batch-start').style.display = 'none';
    document.getElementById('btn-batch-cancel').style.display = 'inline-flex';
    document.getElementById('batch-progress-global').style.display = 'block';
    batchInterval = setInterval(checkBatchStatus, 2000);
  } else {
    const err = await res.json();
    toast('Erro: ' + (err.detail || 'Falha ao iniciar'), 'error');
  }
}

async function cancelarBatch() {
  await fetch('/api/batch/cancel', { method:'POST' });
  toast('Cancelando produção...', 'error');
}

function fmtTimer(seconds) {
  if (!seconds || seconds < 0) return '--:--';
  const h = Math.floor(seconds / 3600);
  const m = Math.floor((seconds % 3600) / 60);
  const s = Math.floor(seconds % 60);
  if (h > 0) return h.toString().padStart(2,'0') + ':' + m.toString().padStart(2,'0') + ':' + s.toString().padStart(2,'0');
  return m.toString().padStart(2,'0') + ':' + s.toString().padStart(2,'0');
}

async function checkBatchStatus() {
  var res, status;
  try {
    res = await fetch('/api/batch/status', { signal: AbortSignal.timeout(10000) });
    status = await res.json();
  } catch(e) {
    // Servidor ocupado — ignorar e tentar de novo no próximo ciclo
    console.warn('Status fetch falhou, retentando...', e.message);
    return;
  }

  if (!status.ativo && !status.jobs.length) return;

  const statusMap = {
    'aguardando': { badge:'badge-waiting', text:'Aguardando' },
    'transcrevendo': { badge:'badge-transcribing', text:'Transcrevendo' },
    'corrigindo': { badge:'badge-fixing', text:'Corrigindo' },
    'montando': { badge:'badge-encoding', text:'Montando' },
    'concluido': { badge:'badge-done', text:'Concluído' },
    'erro': { badge:'badge-error', text:'Erro' },
    'cancelado': { badge:'badge-cancelled', text:'Cancelado' },
  };

  const now = Date.now() / 1000;
  let totalProg = 0;
  status.jobs.forEach(job => {
    const badge = document.getElementById('badge-' + job.template_id);
    const prog = document.getElementById('prog-' + job.template_id);
    const timerEl = document.getElementById('timer-' + job.template_id);
    if (badge) {
      const s = statusMap[job.status] || statusMap['aguardando'];
      badge.className = 'badge ' + s.badge;
      if (job.status === 'erro') {
        badge.textContent = 'Erro';
        badge.title = job.erro;
      } else if (job.etapa && job.status !== 'aguardando' && job.status !== 'concluido') {
        badge.textContent = job.etapa;
      } else {
        badge.textContent = s.text;
      }
    }
    if (prog) {
      const pct = job.progresso || 0;
      const circumference = 138.23;
      const offset = circumference - (pct / 100) * circumference;
      const fg = prog.querySelector('.fg');
      const pctEl = prog.querySelector('.pct');
      if (fg) fg.setAttribute('stroke-dashoffset', offset);
      if (pctEl) pctEl.textContent = Math.round(pct) + '%';
    }
    if (timerEl) {
      if (job.inicio && job.fim) {
        timerEl.textContent = fmtTimer(job.fim - job.inicio);
        timerEl.style.color = 'var(--accent)';
      } else if (job.inicio && !job.fim) {
        timerEl.textContent = fmtTimer(now - job.inicio);
        timerEl.style.color = 'var(--info)';
      } else {
        timerEl.textContent = '--:--';
        timerEl.style.color = 'var(--text-sec)';
      }
    }
    totalProg += job.progresso || (job.status === 'concluido' ? 100 : 0);
  });

  // Global progress
  const globalPct = status.jobs.length ? Math.round(totalProg / status.jobs.length) : 0;
  document.getElementById('batch-progress-fill').style.width = globalPct + '%';
  document.getElementById('batch-progress-text').textContent = globalPct + '%';

  // Timer total
  if (status.inicio) {
    const inicioTs = new Date(status.inicio).getTime() / 1000;
    const elapsed = (status.ativo ? now : Math.max(...status.jobs.filter(j=>j.fim).map(j=>j.fim))) - inicioTs;
    document.getElementById('batch-timer-total').textContent = fmtTimer(elapsed);
  }

  if (!status.ativo) {
    clearInterval(batchInterval);
    document.getElementById('btn-batch-start').style.display = 'inline-flex';
    document.getElementById('btn-batch-cancel').style.display = 'none';
    const concluidos = status.jobs.filter(j => j.status === 'concluido').length;
    toast(`Produção finalizada: ${concluidos}/${status.jobs.length} vídeos`, concluidos === status.jobs.length ? 'success' : 'error');
  }
}

// === RULES ===
async function carregarRegras() {
  const idiomas = ['en','de','pt','es'];
  const tabs = document.getElementById('rules-tabs');
  const content = document.getElementById('rules-content');

  tabs.innerHTML = idiomas.map((id, i) => `
    <button class="tab ${i===0?'active':''}" onclick="showRulesIdioma('${id}',this)">${id.toUpperCase()}</button>
  `).join('');

  showRulesIdioma('en', tabs.querySelector('.tab'));
}

async function showRulesIdioma(idioma, tabEl) {
  document.querySelectorAll('#rules-tabs .tab').forEach(t => t.classList.remove('active'));
  if (tabEl) tabEl.classList.add('active');

  const res = await fetch(`/api/rules/${idioma}`);
  const regras = await res.json();
  const content = document.getElementById('rules-content');

  let html = '';
  for (const [key, rules] of Object.entries(regras)) {
    const titulo = key === '_global' ? `Regras Globais (${idioma.toUpperCase()})` : `Template: ${key}`;
    const subs = rules.substituicoes || {};
    html += `
      <div style="background:var(--panel);border:1px solid var(--border);border-radius:8px;padding:16px;margin-bottom:16px">
        <h4 style="margin-bottom:12px;font-size:14px">${titulo}</h4>
        <div class="form-row">
          <div class="form-group">
            <label>Max caracteres/linha</label>
            <input type="number" value="${rules.max_chars_linha||42}" data-idioma="${idioma}" data-key="${key}" data-field="max_chars_linha" onchange="atualizarRegra(this)">
          </div>
          <div class="form-group" style="display:flex;gap:16px;align-items:flex-end">
            <label><input type="checkbox" ${rules.remover_hesitacoes?'checked':''} data-idioma="${idioma}" data-key="${key}" data-field="remover_hesitacoes" onchange="atualizarRegra(this)"> Remover hesitações</label>
            <label><input type="checkbox" ${rules.capitalizar_inicio?'checked':''} data-idioma="${idioma}" data-key="${key}" data-field="capitalizar_inicio" onchange="atualizarRegra(this)"> Capitalizar início</label>
          </div>
        </div>
        <div class="form-group">
          <label>Substituições (uma por linha: errado=correto)</label>
          <textarea data-idioma="${idioma}" data-key="${key}" data-field="substituicoes" onchange="atualizarRegra(this)" rows="4" placeholder="star seat=Starseed&#10;pleadian=Pleiadian">${Object.entries(subs).map(([k,v])=>k+'='+v).join('\\n')}</textarea>
        </div>
      </div>
    `;
  }
  content.innerHTML = html;
}

async function atualizarRegra(el) {
  const idioma = el.dataset.idioma;
  const key = el.dataset.key;
  const field = el.dataset.field;

  // Buscar regras atuais
  const res = await fetch(`/api/rules/${idioma}`);
  const todas = await res.json();
  const regras = todas[key] || {};

  if (field === 'substituicoes') {
    const subs = {};
    el.value.split('\\n').forEach(line => {
      const [k,...rest] = line.split('=');
      if (k && rest.length) subs[k.trim()] = rest.join('=').trim();
    });
    regras.substituicoes = subs;
  } else if (el.type === 'checkbox') {
    regras[field] = el.checked;
  } else {
    regras[field] = parseInt(el.value) || el.value;
  }

  await fetch(`/api/rules/${idioma}/${key}`, {
    method: 'PUT',
    headers: {'Content-Type':'application/json'},
    body: JSON.stringify(regras)
  });
  toast('Regra atualizada', 'success');
}

// === TOAST ===
function toast(msg, type) {
  const el = document.createElement('div');
  el.className = 'toast toast-' + (type||'success');
  el.textContent = msg;
  document.body.appendChild(el);
  setTimeout(() => el.remove(), 3000);
}

// === HISTÓRICO ===
async function carregarHistorico() {
  var res = await fetch('/api/historico');
  var historico = await res.json();
  var tbody = document.getElementById('historico-tbody');
  var empty = document.getElementById('historico-empty');

  if (!historico.length) { tbody.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';

  tbody.innerHTML = historico.map(function(h) {
    var data = new Date(h.data);
    var dataStr = data.toLocaleDateString('pt-BR') + ' ' + data.toLocaleTimeString('pt-BR', {hour:'2-digit',minute:'2-digit'});
    var tempoStr = h.duracao_producao ? fmtTimer(h.duracao_producao) : '--:--';
    var statusClass = h.status === 'concluido' ? 'badge-done' : 'badge-error';
    var statusText = h.status === 'concluido' ? 'OK' : 'Erro';
    var outputName = h.output ? h.output.replace(/\\\\/g,'/').split('/').pop() : '-';
    var outputDir = h.output ? h.output.replace(/\\\\/g,'/').split('/').slice(0,-1).join('/') : '';

    return '<tr>'
      + '<td style="font-size:12px;color:var(--text-sec)">' + dataStr + '</td>'
      + '<td><span class="card-tag">' + (h.tag||'') + '</span></td>'
      + '<td style="font-size:12px">' + (h.nome||'') + '</td>'
      + '<td style="font-size:11px;color:var(--text-sec)" title="' + (h.output||'') + '">' + outputName + '<br><span style="font-size:10px;opacity:0.6">' + outputDir + '</span></td>'
      + '<td style="font-family:monospace;font-size:12px;color:var(--accent)">' + tempoStr + '</td>'
      + '<td><span class="badge ' + statusClass + '" title="' + (h.erro||'') + '">' + statusText + '</span></td>'
      + '</tr>';
  }).join('');
}

async function limparHistorico() {
  if (!confirm('Tem certeza que deseja limpar todo o histórico?')) return;
  await fetch('/api/historico', { method: 'DELETE' });
  toast('Histórico limpo', 'success');
  carregarHistorico();
}

// === PIPELINES (ROTEIROS) ===
var pipelines = [];
var editandoPipelineId = null;
var execInterval = null;
var _execPipelineId = null;

async function carregarPipelines() {
  var res = await fetch('/api/pipelines');
  pipelines = await res.json();
  renderPipelines();
}

function renderPipelines() {
  var grid = document.getElementById('pipelines-grid');
  var empty = document.getElementById('pipelines-empty');
  if (!pipelines.length) { grid.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  grid.innerHTML = pipelines.map(function(p) {
    var nEtapas = (p.etapas || []).length;
    var modelos = (p.etapas || []).map(function(e){ return e.modelo || 'claude'; });
    var modelosUniq = modelos.filter(function(v,i,a){ return a.indexOf(v)===i; }).join(', ');
    return '<div class="card" onclick="editarPipeline(\\'' + p.id + '\\')">'
      + '<div class="card-header">'
      + '<span class="card-title">' + (p.nome || 'Sem nome') + '</span>'
      + '<span class="card-tag">' + (p.tag || p.idioma || 'en').toUpperCase() + '</span>'
      + '</div>'
      + '<div class="card-info">'
      + nEtapas + ' etapa(s) | Modelos: ' + modelosUniq + '</div>'
      + '<div class="card-actions">'
      + '<button class="btn btn-primary btn-sm" onclick="event.stopPropagation();abrirExecucao(\\'' + p.id + '\\')">Gerar Roteiro</button>'
      + '<button class="btn btn-secondary btn-sm" onclick="event.stopPropagation();duplicarPipeline(\\'' + p.id + '\\')">Duplicar</button>'
      + '<button class="btn btn-danger btn-sm" onclick="event.stopPropagation();deletarPipeline(\\'' + p.id + '\\')">Excluir</button>'
      + '</div></div>';
  }).join('');
}

function _etapaHTML(i, etapa) {
  etapa = etapa || {};
  var tipo = etapa.tipo || 'llm';
  var tipoColors = { llm: 'var(--accent)', code: 'var(--warn)', texto: 'var(--info)' };
  var tipoLabels = { llm: 'LLM', code: 'Code', texto: 'Texto Fixo' };

  var div = document.createElement('div');
  div.style.cssText = 'background:var(--bg);border:1px solid var(--border);border-left:3px solid ' + (tipoColors[tipo]||'var(--border)') + ';border-radius:6px;padding:12px;margin-bottom:8px';
  div.dataset.idx = i;

  // Header com nome + tipo + collapse + test + X
  var header = '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px" class="pip-et-header">'
    + '<div style="display:flex;align-items:center;gap:8px">'
    + '<button type="button" class="pip-et-toggle" style="background:none;border:none;color:var(--text-sec);cursor:pointer;font-size:14px;padding:0 4px" onclick="toggleEtapa(this)">&#9660;</button>'
    + '<strong style="font-size:13px">Etapa ' + (i+1) + '</strong>'
    + '<select class="pip-et-tipo" style="font-size:10px;padding:2px 6px;background:var(--panel);border:1px solid var(--border);border-radius:4px;color:' + (tipoColors[tipo]||'var(--text)') + ';font-weight:600" onchange="mudarTipoEtapa(this)">'
    + '<option value="llm"' + (tipo==='llm'?' selected':'') + '>LLM</option>'
    + '<option value="code"' + (tipo==='code'?' selected':'') + '>Code</option>'
    + '<option value="texto"' + (tipo==='texto'?' selected':'') + '>Texto Fixo</option>'
    + '</select>'
    + '<span class="pip-et-nome-preview" style="font-size:11px;color:var(--text-sec);display:none"></span>'
    + '</div>'
    + '<div style="display:flex;gap:4px">'
    + '<button type="button" class="btn btn-secondary btn-sm" style="font-size:10px;padding:2px 8px" onclick="testarEtapa(this,false)">Testar</button>'
    + '<button type="button" class="btn btn-secondary btn-sm" style="font-size:10px;padding:2px 8px;color:var(--accent)" onclick="testarEtapa(this,true)">Testar até aqui</button>'
    + '<button class="btn btn-danger btn-sm" type="button" style="padding:2px 8px">X</button>'
    + '</div></div>';

  // Nome
  var nome = '<div class="form-group" style="margin:0 0 8px"><label style="font-size:11px">Nome</label>'
    + '<input type="text" class="pip-et-nome" value="' + (etapa.nome||'').replace(/"/g,'&quot;') + '" placeholder="HOOKER, CLOSER, COUNTER..." style="font-size:12px"></div>';

  // Campos LLM
  var llmFields = '<div class="pip-fields-llm"' + (tipo!=='llm'?' style="display:none"':'') + '>'
    + '<div class="form-row" style="margin-bottom:8px">'
    + '<div class="form-group" style="margin:0"><label style="font-size:11px">Credencial</label>'
    + '<select class="pip-et-cred" style="font-size:12px" onchange="atualizarModelosEtapa(this)">'
    + '<option value="">Selecione...</option>'
    + credenciais.map(function(c){ return '<option value="' + c.id + '"' + (etapa.credencial===c.id?' selected':'') + '>' + (c.nome||c.id) + ' (' + (c.provedor||'').toUpperCase() + ')</option>'; }).join('')
    + '</select></div>'
    + '<div class="form-group" style="margin:0"><label style="font-size:11px">Modelo</label>'
    + '<select class="pip-et-modelo" style="font-size:12px">'
    + _opcoesModelo(etapa.credencial, etapa.modelo)
    + '</select></div></div>'
    + '</div>';

  // Campo Code
  var codeFields = '<div class="pip-fields-code"' + (tipo!=='code'?' style="display:none"':'') + '>'
    + '<div style="font-size:10px;color:var(--warn);margin-bottom:4px;padding:4px 8px;background:rgba(210,153,34,0.1);border-radius:4px">'
    + 'Python. Variáveis: <code>saida_anterior</code>, <code>entrada</code>, <code>roteiro_atual</code>, <code>variaveis</code> (dict). Defina <code>resultado</code> com o output.'
    + '</div></div>';

  // Campo Texto Fixo
  var textoFields = '<div class="pip-fields-texto"' + (tipo!=='texto'?' style="display:none"':'') + '>'
    + '<div style="font-size:10px;color:var(--info);margin-bottom:4px;padding:4px 8px;background:rgba(88,166,255,0.1);border-radius:4px">'
    + 'Texto estático. Use variáveis {{saida_anterior}}, {{roteiro_atual}}, etc. Será inserido sem chamar LLM.'
    + '</div></div>';

  // Prompt (usado por todos os tipos)
  var promptLabel = { llm: 'Prompt (User Message)', code: 'Código Python', texto: 'Texto' };
  var promptPlaceholder = {
    llm: '{{entrada}} {{saida_anterior}} {{roteiro_atual}}',
    code: '# Contar caracteres\\nresultado = f"Caracteres: {len(saida_anterior)}\\\\n\\\\n{saida_anterior}"',
    texto: 'Texto fixo aqui... Use {{saida_anterior}} para incluir output anterior',
  };
  var prompt = '<div class="form-group" style="margin:0"><label style="font-size:11px" class="pip-prompt-label">' + (promptLabel[tipo]||'Prompt') + '</label>'
    + '<textarea class="pip-et-prompt" rows="4" placeholder="' + (promptPlaceholder[tipo]||'') + '" style="font-size:12px;font-family:' + (tipo==='code'?'monospace':'inherit') + '">' + (etapa.prompt||'').replace(/</g,'&lt;') + '</textarea>'
    + '</div>';

  // System Message (só pra LLM, aparece depois do prompt como no n8n)
  var systemField = '<div class="pip-fields-llm-system"' + (tipo!=='llm'?' style="display:none"':'') + '>'
    + '<div class="form-group" style="margin:8px 0 0"><label style="font-size:11px">System Message</label>'
    + '<textarea class="pip-et-system" rows="3" placeholder="Papel e instruções do agente..." style="font-size:12px">' + (etapa.system_message||'').replace(/</g,'&lt;') + '</textarea></div>'
    + '</div>';

  // Área de resultado do teste
  var testArea = '<div class="pip-et-test-result" style="display:none;margin-top:8px">'
    + '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:4px">'
    + '<label style="font-size:11px;font-weight:600;color:var(--accent)">Resultado do Teste</label>'
    + '<div style="display:flex;gap:4px">'
    + '<span class="pip-et-test-info" style="font-size:10px;color:var(--text-sec)"></span>'
    + '<button type="button" class="btn btn-secondary btn-sm" style="font-size:9px;padding:1px 5px" onclick="copiarTexto(this)" data-text="">Copiar</button>'
    + '</div></div>'
    + '<textarea class="pip-et-test-output" readonly rows="5" style="font-size:11px;background:var(--panel);border:1px solid var(--border);border-radius:4px;color:var(--text);padding:8px;width:100%;resize:vertical"></textarea>'
    + '</div>';

  div.innerHTML = header + '<div class="pip-et-body">' + nome + llmFields + codeFields + textoFields + prompt + systemField + testArea + '</div>';
  div.querySelector('.btn-danger').addEventListener('click', function(){ div.remove(); });
  return div;
}

function toggleEtapa(btn) {
  var div = btn.closest('div[data-idx]');
  var body = div.querySelector('.pip-et-body');
  var preview = div.querySelector('.pip-et-nome-preview');
  var nome = div.querySelector('.pip-et-nome');
  if (body.style.display === 'none') {
    body.style.display = '';
    btn.innerHTML = '&#9660;';
    if (preview) preview.style.display = 'none';
  } else {
    body.style.display = 'none';
    btn.innerHTML = '&#9654;';
    if (preview && nome) { preview.textContent = nome.value || ''; preview.style.display = 'inline'; }
  }
}

// === INPUT DE TESTE (modal) ===
var _testInputResolve = null;

async function pedirTestInput() {
  // Garantir que dados de Temas estejam carregados
  if (!temasData || !temasData.linhas || !temasData.linhas.length) {
    try {
      var r = await fetch('/api/temas');
      var raw = await r.json();
      if (raw && raw.colunas) temasData = raw;
    } catch(e) {}
  }
  return new Promise(function(resolve) {
    _testInputResolve = resolve;
    var rows = (temasData && temasData.linhas) || [];
    var cols = (temasData && temasData.colunas) || [];
    document.getElementById('test-input-data').innerHTML = '<option value="">--</option>'
      + rows.map(function(r, i){ return '<option value="' + i + '">' + r.data + '</option>'; }).join('');
    document.getElementById('test-input-canal').innerHTML = '<option value="">--</option>'
      + cols.map(function(c, i){ return '<option value="' + i + '">' + c.nome + '</option>'; }).join('');
    document.getElementById('test-input-texto').value = '';
    document.getElementById('modal-test-input').classList.add('active');
  });
}

function atualizarTestInput() {
  var ri = document.getElementById('test-input-data').value;
  var ci = document.getElementById('test-input-canal').value;
  if (ri === '' || ci === '') return;
  var key = ri + '_' + ci;
  var cel = (temasData.celulas || {})[key] || {};
  if (cel.tema) document.getElementById('test-input-texto').value = cel.tema;
}

function _resolverTestInput() {
  var val = document.getElementById('test-input-texto').value.trim();
  document.getElementById('modal-test-input').classList.remove('active');
  if (_testInputResolve) _testInputResolve(val || null);
  _testInputResolve = null;
}

function _coletarEtapaDe(div) {
  var tipo = div.querySelector('.pip-et-tipo') ? div.querySelector('.pip-et-tipo').value : 'llm';
  var etapa = {
    tipo: tipo,
    nome: div.querySelector('.pip-et-nome') ? div.querySelector('.pip-et-nome').value : '',
    prompt: div.querySelector('.pip-et-prompt') ? div.querySelector('.pip-et-prompt').value : '',
  };
  if (tipo === 'llm') {
    etapa.credencial = div.querySelector('.pip-et-cred') ? div.querySelector('.pip-et-cred').value : '';
    etapa.modelo = div.querySelector('.pip-et-modelo') ? div.querySelector('.pip-et-modelo').value : '';
    etapa.system_message = div.querySelector('.pip-et-system') ? div.querySelector('.pip-et-system').value : '';
  }
  return etapa;
}

async function testarEtapa(btn, ateAqui) {
  var div = btn.closest('div[data-idx]');
  var idx = parseInt(div.dataset.idx);
  var resultArea = div.querySelector('.pip-et-test-result');
  var outputEl = div.querySelector('.pip-et-test-output');
  var infoEl = div.querySelector('.pip-et-test-info');
  var copyBtn = resultArea.querySelector('[data-text]');

  var testInput = await pedirTestInput();
  if (!testInput) return;

  btn.disabled = true;
  btn.textContent = '...';
  resultArea.style.display = 'block';

  if (ateAqui) {
    // Rodar todas as etapas de 0 até idx
    var allDivs = document.querySelectorAll('#pip-etapas-list > div');
    var etapas = [];
    for (var i = 0; i <= idx && i < allDivs.length; i++) {
      etapas.push(_coletarEtapaDe(allDivs[i]));
    }
    outputEl.value = 'Rodando ' + etapas.length + ' etapas...';

    var startTime = Date.now();
    try {
      var res = await fetch('/api/pipelines/testar-cadeia', {
        method: 'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ etapas: etapas, entrada: testInput })
      });
      var data = await res.json();
      var elapsed = ((Date.now() - startTime) / 1000).toFixed(1);

      if (data.ok) {
        // Mostrar resultado de cada etapa no respectivo div
        (data.resultados || []).forEach(function(r, i) {
          if (i < allDivs.length) {
            var ra = allDivs[i].querySelector('.pip-et-test-result');
            var oe = allDivs[i].querySelector('.pip-et-test-output');
            var ie = allDivs[i].querySelector('.pip-et-test-info');
            var cb = ra.querySelector('[data-text]');
            ra.style.display = 'block';
            oe.value = r.resultado || '(vazio)';
            ie.textContent = (r.resultado||'').length + ' chars | ' + (r.status||'');
            if (cb) cb.dataset.text = r.resultado || '';
          }
        });
        infoEl.textContent = (data.resultados[idx]||{resultado:''}).resultado.length + ' chars | ' + elapsed + 's total';
      } else {
        outputEl.value = 'ERRO: ' + (data.erro || 'Falha');
        infoEl.textContent = elapsed + 's';
      }
    } catch(e) {
      outputEl.value = 'ERRO: ' + e.message;
    }
  } else {
    // Rodar só esta etapa
    var etapa = _coletarEtapaDe(div);
    outputEl.value = 'Processando...';
    var startTime = Date.now();
    try {
      var res = await fetch('/api/pipelines/testar-etapa', {
        method: 'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({ etapa: etapa, entrada: testInput })
      });
      var data = await res.json();
      var elapsed = ((Date.now() - startTime) / 1000).toFixed(1);
      if (data.ok) {
        outputEl.value = data.resultado || '(vazio)';
        infoEl.textContent = data.resultado.length + ' chars | ' + elapsed + 's';
        if (copyBtn) copyBtn.dataset.text = data.resultado;
      } else {
        outputEl.value = 'ERRO: ' + (data.erro || 'Falha');
        infoEl.textContent = elapsed + 's';
      }
    } catch(e) { outputEl.value = 'ERRO: ' + e.message; }
  }
  btn.disabled = false;
  btn.textContent = ateAqui ? 'Testar até aqui' : 'Testar';
}

function mudarTipoEtapa(sel) {
  var div = sel.closest('div[data-idx]');
  var tipo = sel.value;
  var colors = { llm: 'var(--accent)', code: 'var(--warn)', texto: 'var(--info)' };
  div.style.borderLeftColor = colors[tipo] || 'var(--border)';
  sel.style.color = colors[tipo] || 'var(--text)';
  // Toggle campos
  var llm = div.querySelector('.pip-fields-llm');
  var llmSys = div.querySelector('.pip-fields-llm-system');
  var code = div.querySelector('.pip-fields-code');
  var texto = div.querySelector('.pip-fields-texto');
  if (llm) llm.style.display = tipo === 'llm' ? '' : 'none';
  if (llmSys) llmSys.style.display = tipo === 'llm' ? '' : 'none';
  if (code) code.style.display = tipo === 'code' ? '' : 'none';
  if (texto) texto.style.display = tipo === 'texto' ? '' : 'none';
  // Atualizar label e font do prompt
  var label = div.querySelector('.pip-prompt-label');
  var textarea = div.querySelector('.pip-et-prompt');
  var labels = { llm: 'Prompt (User Message)', code: 'Código Python', texto: 'Texto' };
  if (label) label.textContent = labels[tipo] || 'Prompt';
  if (textarea) textarea.style.fontFamily = tipo === 'code' ? 'monospace' : 'inherit';
}

function _opcoesModelo(credId, modeloSelecionado) {
  var cred = credenciais.find(function(c){ return c.id === credId; });
  if (!cred || !cred.modelos || !cred.modelos.length) return '<option value="">Selecione credencial primeiro</option>';
  return cred.modelos.map(function(m) {
    return '<option value="' + m + '"' + (m===modeloSelecionado?' selected':'') + '>' + m + '</option>';
  }).join('');
}

function atualizarModelosEtapa(selectCred) {
  var credId = selectCred.value;
  var modeloSelect = selectCred.closest('div[data-idx]').querySelector('.pip-et-modelo');
  modeloSelect.innerHTML = _opcoesModelo(credId, '');
}

function adicionarEtapaPipeline() {
  var list = document.getElementById('pip-etapas-list');
  list.appendChild(_etapaHTML(list.children.length, {}));
}

async function abrirEditorPipeline(id) {
  // Carregar credenciais atualizadas
  if (!credenciais.length) {
    var r = await fetch('/api/credenciais');
    credenciais = await r.json();
  }
  editandoPipelineId = id || null;
  document.getElementById('pipeline-editor-title').textContent = id ? 'Editar Pipeline' : 'Nova Pipeline';
  var list = document.getElementById('pip-etapas-list');
  list.innerHTML = '';

  if (id) {
    var p = pipelines.find(function(x){ return x.id === id; });
    if (!p) return;
    document.getElementById('pip-nome').value = p.nome || '';
    document.getElementById('pip-tag').value = p.tag || '';
    document.getElementById('pip-idioma').value = p.idioma || 'en';
    (p.etapas || []).forEach(function(et, i) { list.appendChild(_etapaHTML(i, et)); });
  } else {
    document.getElementById('pip-nome').value = '';
    document.getElementById('pip-tag').value = '';
    document.getElementById('pip-idioma').value = 'en';
  }
  document.getElementById('modal-pipeline').classList.add('active');
}

function editarPipeline(id) { abrirEditorPipeline(id); }
function fecharEditorPipeline() { document.getElementById('modal-pipeline').classList.remove('active'); }

function _coletarEtapas() {
  var items = document.querySelectorAll('#pip-etapas-list > div');
  return Array.from(items).map(function(div) {
    var tipo = div.querySelector('.pip-et-tipo') ? div.querySelector('.pip-et-tipo').value : 'llm';
    var etapa = {
      tipo: tipo,
      nome: div.querySelector('.pip-et-nome').value.trim(),
      prompt: div.querySelector('.pip-et-prompt').value,
    };
    if (tipo === 'llm') {
      etapa.credencial = div.querySelector('.pip-et-cred') ? div.querySelector('.pip-et-cred').value : '';
      etapa.modelo = div.querySelector('.pip-et-modelo') ? div.querySelector('.pip-et-modelo').value : '';
      etapa.system_message = div.querySelector('.pip-et-system') ? div.querySelector('.pip-et-system').value : '';
    }
    return etapa;
  });
}

async function salvarPipeline() {
  var dados = {
    id: editandoPipelineId || document.getElementById('pip-nome').value.toLowerCase().replace(/[^a-z0-9]/g,'-') || undefined,
    nome: document.getElementById('pip-nome').value,
    tag: document.getElementById('pip-tag').value.toUpperCase(),
    idioma: document.getElementById('pip-idioma').value,
    etapas: _coletarEtapas(),
  };
  var url = editandoPipelineId ? '/api/pipelines/' + editandoPipelineId : '/api/pipelines';
  var method = editandoPipelineId ? 'PUT' : 'POST';
  var res = await fetch(url, { method: method, headers:{'Content-Type':'application/json'}, body:JSON.stringify(dados) });
  if (res.ok) { toast('Pipeline salva!', 'success'); fecharEditorPipeline(); carregarPipelines(); }
  else { var err = await res.json(); toast('Erro: ' + (err.detail||'Falha'), 'error'); }
}

async function deletarPipeline(id) {
  if (!confirm('Excluir esta pipeline?')) return;
  await fetch('/api/pipelines/' + id, { method:'DELETE' });
  toast('Pipeline excluída', 'success');
  carregarPipelines();
}

async function duplicarPipeline(id) {
  var p = pipelines.find(function(x){ return x.id === id; });
  if (!p) return;
  var novo = JSON.parse(JSON.stringify(p));
  novo.id = (p.tag || p.id).toLowerCase().replace(/[^a-z0-9]/g,'-') + '-' + Math.random().toString(36).substring(2,6);
  novo.nome = p.nome + ' (cópia)';
  novo.tag = (p.tag || '') + '2';
  await fetch('/api/pipelines', { method:'POST', headers:{'Content-Type':'application/json'}, body:JSON.stringify(novo) });
  toast('Pipeline duplicada', 'success');
  carregarPipelines();
}

function abrirExecucao(id) {
  _execPipelineId = id;
  document.getElementById('exec-entrada').value = '';

  // Popular dropdowns com dados do grid de Temas
  var rows = (temasData && temasData.linhas) || [];
  var cols = (temasData && temasData.colunas) || [];
  document.getElementById('exec-data').innerHTML = '<option value="">Selecione...</option>'
    + rows.map(function(r, i){ return '<option value="' + i + '">' + r.data + '</option>'; }).join('');
  document.getElementById('exec-canal').innerHTML = '<option value="">Selecione...</option>'
    + cols.map(function(c, i){ return '<option value="' + i + '">' + c.nome + '</option>'; }).join('');

  document.getElementById('modal-exec-input').classList.add('active');
}

function atualizarExecPreview() {
  var ri = document.getElementById('exec-data').value;
  var ci = document.getElementById('exec-canal').value;
  if (ri === '' || ci === '') return;
  var key = ri + '_' + ci;
  var cel = (temasData.celulas || {})[key] || {};
  if (cel.tema) {
    document.getElementById('exec-entrada').value = cel.tema;
  }
}

async function executarPipeline() {
  var entrada = document.getElementById('exec-entrada').value.trim();
  if (!entrada) { toast('Informe o tema/entrada', 'error'); return; }
  var ciVal = document.getElementById('exec-canal').value;
  var riVal = document.getElementById('exec-data').value;
  var canal = ciVal !== '' && temasData.colunas[ciVal] ? temasData.colunas[ciVal].nome : '';
  var data = riVal !== '' && temasData.linhas[riVal] ? temasData.linhas[riVal].data : '';
  document.getElementById('modal-exec-input').classList.remove('active');

  var res = await fetch('/api/pipelines/' + _execPipelineId + '/executar', {
    method: 'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ entrada: entrada, tema: entrada, canal: canal, data: data })
  });
  if (!res.ok) { var err = await res.json(); toast('Erro: ' + (err.detail||'Falha'), 'error'); return; }

  document.getElementById('exec-panel').style.display = 'block';
  var p = pipelines.find(function(x){ return x.id === _execPipelineId; });
  document.getElementById('exec-title').textContent = 'Executando: ' + (p ? p.nome : _execPipelineId);
  document.getElementById('exec-resultado').style.display = 'none';
  execInterval = setInterval(checkExecucao, 2000);
  checkExecucao();
}

async function checkExecucao() {
  var res = await fetch('/api/pipelines/execucao');
  var status = await res.json();
  if (!status.etapas || !status.etapas.length) return;

  var html = '';
  status.etapas.forEach(function(et, i) {
    var badge = 'badge-waiting';
    var text = 'Aguardando';
    if (et.status === 'processando') { badge = 'badge-transcribing'; text = 'Processando...'; }
    else if (et.status === 'concluido') { badge = 'badge-done'; text = 'Concluído'; }
    else if (et.status === 'erro') { badge = 'badge-error'; text = 'Erro'; }
    else if (et.status === 'cancelado') { badge = 'badge-cancelled'; text = 'Cancelado'; }

    var tempo = '';
    if (et.inicio && et.fim) tempo = fmtTimer(et.fim - et.inicio);
    else if (et.inicio) tempo = fmtTimer(Date.now()/1000 - et.inicio);

    html += '<div style="display:flex;align-items:center;gap:12px;padding:8px 0;border-bottom:1px solid var(--border)">'
      + '<span style="font-size:13px;min-width:120px;font-weight:500">' + (et.nome || 'Etapa '+(i+1)) + '</span>'
      + '<span style="font-size:11px;color:var(--text-sec)">' + (et.modelo||'') + '</span>'
      + '<span class="badge ' + badge + '" style="font-size:11px">' + text + '</span>'
      + '<span style="font-size:12px;color:var(--text-sec);font-family:monospace;margin-left:auto">' + tempo + '</span>';

    if (et.status === 'concluido' && et.resultado) {
      html += '<button class="btn btn-secondary btn-sm" style="font-size:11px" onclick="toggleExecPreview(this)">Ver</button>';
    }
    if (et.status === 'erro' && et.erro) {
      html += '<span style="font-size:11px;color:var(--danger)" title="' + (et.erro||'').replace(/"/g,'&quot;') + '">!</span>';
    }
    html += '</div>';
    if (et.status === 'concluido' && et.resultado) {
      html += '<div class="exec-preview" style="display:none;padding:8px 0 8px 12px;max-height:200px;overflow-y:auto">'
        + '<pre style="font-size:12px;color:var(--text-sec);white-space:pre-wrap;margin:0">' + et.resultado.substring(0,500).replace(/</g,'&lt;') + (et.resultado.length>500?'...':'') + '</pre></div>';
    }
  });

  document.getElementById('exec-etapas').innerHTML = html;

  if (!status.ativo) {
    clearInterval(execInterval);
    if (status.resultado_final) {
      document.getElementById('exec-resultado').style.display = 'block';
      document.getElementById('exec-resultado-text').value = status.resultado_final;
    }
    toast('Roteiro finalizado!', 'success');
  }
}

function toggleExecPreview(btn) {
  var preview = btn.closest('div').nextElementSibling;
  if (preview && preview.classList.contains('exec-preview')) {
    preview.style.display = preview.style.display === 'none' ? 'block' : 'none';
  }
}

async function cancelarExecucao() {
  await fetch('/api/pipelines/execucao/cancelar', { method:'POST' });
  toast('Cancelando...', 'error');
}

// === CREDENCIAIS ===
var credenciais = [];

async function carregarCredenciais() {
  var res = await fetch('/api/credenciais');
  credenciais = await res.json();
  renderCredenciais();
}

function renderCredenciais() {
  var list = document.getElementById('creds-list');
  var empty = document.getElementById('creds-empty');
  if (!credenciais.length) { list.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  list.innerHTML = credenciais.map(function(c) {
    var statusBadge = c.status === 'ok'
      ? '<span class="badge badge-done" style="font-size:10px">OK</span>'
      : '<span class="badge badge-error" style="font-size:10px">Erro</span>';
    var nModelos = (c.modelos || []).length;
    return '<div style="display:flex;align-items:center;gap:12px;padding:10px;background:var(--bg);border:1px solid var(--border);border-radius:6px;margin-bottom:6px">'
      + '<div style="flex:1">'
      + '<strong style="font-size:13px">' + (c.nome || c.id) + '</strong>'
      + '<span style="font-size:11px;color:var(--text-sec);margin-left:8px">' + (c.provedor||'').toUpperCase() + ' | ' + (c.api_key_masked||'') + ' | ' + nModelos + ' modelos</span>'
      + '</div>'
      + statusBadge
      + '<button class="btn btn-secondary btn-sm" style="font-size:11px" onclick="refreshCredencial(\\'' + c.id + '\\')">Refresh</button>'
      + '<button class="btn btn-danger btn-sm" style="font-size:11px" onclick="deletarCredencial(\\'' + c.id + '\\')">X</button>'
      + '</div>';
  }).join('');
}

async function novaCredencial() {
  var nome = prompt('Nome da credencial (ex: Claude Principal):');
  if (!nome) return;
  var provedor = prompt('Provedor (claude, gpt, gemini):');
  if (!provedor || !['claude','gpt','gemini'].includes(provedor)) { toast('Provedor inválido', 'error'); return; }
  var key = prompt('API Key:');
  if (!key) return;

  toast('Testando credencial...', 'success');
  var res = await fetch('/api/credenciais', {
    method: 'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ nome: nome, provedor: provedor, api_key: key })
  });
  var data = await res.json();
  if (data.status === 'ok') {
    toast('Credencial adicionada! ' + (data.modelos||[]).length + ' modelos encontrados', 'success');
  } else {
    toast('Credencial adicionada com erro: ' + (data.erro||''), 'error');
  }
  carregarCredenciais();
}

async function refreshCredencial(id) {
  toast('Atualizando modelos...', 'success');
  var res = await fetch('/api/credenciais/' + id + '/refresh', { method:'POST' });
  var data = await res.json();
  if (data.ok) toast(data.modelos.length + ' modelos disponíveis', 'success');
  else toast('Erro: ' + (data.erro||''), 'error');
  carregarCredenciais();
}

async function deletarCredencial(id) {
  if (!confirm('Excluir esta credencial?')) return;
  await fetch('/api/credenciais/' + id, { method:'DELETE' });
  toast('Credencial excluída', 'success');
  carregarCredenciais();
}

// === CONFIG (SYNC) ===
async function carregarConfig() {
  var res = await fetch('/api/config');
  var cfg = await res.json();
  document.getElementById('cfg-ai33-key').value = cfg.ai33_api_key || '';
  document.getElementById('cfg-supabase-url').value = cfg.supabase_url || '';
  document.getElementById('cfg-supabase-key').value = cfg.supabase_key || '';
  document.getElementById('cfg-sheets-id').value = cfg.sheets_id || '';
  document.getElementById('cfg-sheets-api-key').value = cfg.sheets_api_key || '';
  document.getElementById('cfg-sheets-tab').value = cfg.sheets_tab || 'Temas';
  document.getElementById('cfg-tracker-url').value = cfg.tracker_url || '';
  document.getElementById('cfg-tracker-auth').value = cfg.tracker_auth || '';
  document.getElementById('cfg-comment-template').value = cfg.comment_template || '';
  carregarCredenciais();
}

async function salvarConfig() {
  var dados = {
    ai33_api_key: document.getElementById('cfg-ai33-key').value,
    supabase_url: document.getElementById('cfg-supabase-url').value,
    supabase_key: document.getElementById('cfg-supabase-key').value,
    sheets_id: document.getElementById('cfg-sheets-id').value,
    sheets_api_key: document.getElementById('cfg-sheets-api-key').value,
    sheets_tab: document.getElementById('cfg-sheets-tab').value,
    tracker_url: document.getElementById('cfg-tracker-url').value,
    tracker_auth: document.getElementById('cfg-tracker-auth').value,
    comment_template: document.getElementById('cfg-comment-template').value,
  };
  var res = await fetch('/api/config', { method:'PUT', headers:{'Content-Type':'application/json'}, body:JSON.stringify(dados) });
  if (res.ok) toast('Configurações salvas!', 'success');
  else toast('Erro ao salvar', 'error');
}

// === NARRAÇÃO ===
var _vozes = [];
var _narrInterval = null;
var _narrHistorico = JSON.parse(localStorage.getItem('narrHistorico') || '[]');

async function carregarNarracao() {
  if (!templates.length) {
    var r = await fetch('/api/templates');
    templates = await r.json();
  }
  // Pasta de saída padrão
  var pasta = document.getElementById('narr-pasta-saida');
  if (!pasta.value) pasta.value = 'narracoes/';

  await carregarVozes();
  carregarCreditos();
  renderNarrHistorico();
  // Popular dropdowns de puxar roteiro
  _popularNarrPullDropdowns();
}

async function _popularNarrPullDropdowns() {
  if (!temasData || !temasData.linhas || !temasData.linhas.length) {
    try { var r = await fetch('/api/temas'); var raw = await r.json(); if (raw && raw.colunas) temasData = raw; } catch(e){}
  }
  var rows = (temasData && temasData.linhas) || [];
  var cols = (temasData && temasData.colunas) || [];
  document.getElementById('narr-pull-data').innerHTML = '<option value="">Data...</option>'
    + rows.map(function(r, i){ return '<option value="' + i + '">' + r.data + '</option>'; }).join('');
  document.getElementById('narr-pull-canal').innerHTML = '<option value="">Canal...</option>'
    + cols.map(function(c, i){ return '<option value="' + i + '">' + c.nome + '</option>'; }).join('');
}

function puxarRoteiroNarracao() {
  var ri = document.getElementById('narr-pull-data').value;
  var ci = document.getElementById('narr-pull-canal').value;
  if (ri === '' || ci === '') { toast('Selecione data e canal', 'error'); return; }
  var key = ri + '_' + ci;
  var cel = (temasData.celulas || {})[key] || {};
  if (cel.roteiro) {
    document.getElementById('narr-texto').value = cel.roteiro;
    document.getElementById('narr-char-count').textContent = cel.roteiro.length + ' chars';
    // Auto-preencher nome de saída
    var col = temasData.colunas[ci] || {};
    var row = temasData.linhas[ri] || {};
    var dateParts = (row.data || '').split('/');
    if (dateParts.length >= 2) {
      document.getElementById('narr-nome').value = (col.nome || '') + ' ' + dateParts[0] + '-' + dateParts[1];
    }
    toast('Roteiro puxado! ' + cel.roteiro.length + ' chars', 'success');
  } else {
    toast('Nenhum roteiro nessa célula', 'error');
  }
}

async function carregarCreditos() {
  try {
    var res = await fetch('/api/narration/credits');
    var data = await res.json();
    var el = document.getElementById('narr-credits');
    if (data.credits != null) el.textContent = Number(data.credits).toLocaleString();
    else { el.textContent = '--'; }
  } catch(e) {}
}

async function filtrarVozesTemplate() {
  // Carregar vozes se vazio (cache local ou API)
  if (!_vozes.length) {
    var cached = localStorage.getItem('vozesCache');
    if (cached) { try { _vozes = JSON.parse(cached); } catch(e){} }
    if (!_vozes.length) {
      try { var r = await fetch('/api/narration/voices'); _vozes = await r.json(); localStorage.setItem('vozesCache', JSON.stringify(_vozes)); } catch(e){}
    }
  }
  var provider = document.getElementById('ed-voz-provider').value;
  var sel = document.getElementById('ed-voz-id');
  var filtradas;
  if (provider === 'all') filtradas = _vozes;
  else filtradas = _vozes.filter(function(v){ return v.provider === provider; });
  sel.innerHTML = '<option value="">Nenhuma (manual)</option>' + filtradas.map(function(v) {
    return '<option value="' + v.voice_id + '" data-provider="' + v.provider + '">' + v.name + ' (' + v.provider + ')</option>';
  }).join('');
}

async function carregarVozes() {
  var sel = document.getElementById('narr-voice');
  // Carregar cache local primeiro (instantâneo)
  var cached = localStorage.getItem('vozesCache');
  if (cached && !_vozes.length) {
    try { _vozes = JSON.parse(cached); filtrarVozes(); } catch(e){}
  }
  // Buscar da API em background
  sel.innerHTML = _vozes.length ? sel.innerHTML : '<option value="">Carregando vozes...</option>';
  try {
    var res = await fetch('/api/narration/voices');
    _vozes = await res.json();
    localStorage.setItem('vozesCache', JSON.stringify(_vozes));
    sel.style.color = '';
    filtrarVozes();
  } catch(e) {
    if (!_vozes.length) sel.innerHTML = '<option value="">Erro ao carregar</option>';
  }
}

function filtrarVozes() {
  var provider = document.getElementById('narr-provider').value;
  var sel = document.getElementById('narr-voice');
  var filtradas;
  if (provider === 'all') filtradas = _vozes;
  else if (provider === 'elevenlabs_fav') filtradas = _vozes.filter(function(v){ return v.provider === 'elevenlabs' && v.bookmarked; });
  else filtradas = _vozes.filter(function(v){ return v.provider === provider; });
  if (!filtradas.length) { sel.innerHTML = '<option value="">Nenhuma</option>'; return; }
  sel.innerHTML = filtradas.map(function(v) {
    return '<option value="' + v.voice_id + '" data-provider="' + v.provider + '">' + v.name + ' (' + v.provider + ')</option>';
  }).join('');
}

async function gerarNarracao(preview) {
  var texto = document.getElementById('narr-texto').value.trim();
  if (!texto) { toast('Cole o roteiro', 'error'); return; }
  var voiceSel = document.getElementById('narr-voice');
  if (!voiceSel.value) { toast('Selecione uma voz', 'error'); return; }
  var provider = voiceSel.selectedOptions[0].dataset.provider || 'elevenlabs';
  var nome = document.getElementById('narr-nome').value || 'narracao';
  var pasta = document.getElementById('narr-pasta-saida').value || 'narracoes/';
  var vozNome = voiceSel.selectedOptions[0].textContent;

  document.getElementById('narr-gerar-btn').disabled = true;
  document.getElementById('narr-preview-btn').disabled = true;
  document.getElementById('narr-status-panel').style.display = 'block';
  document.getElementById('narr-progress-fill').style.width = '5%';
  document.getElementById('narr-status-badge').textContent = preview ? 'Preview...' : 'Enviando...';
  document.getElementById('narr-status-badge').className = 'badge badge-transcribing';

  var res = await fetch('/api/narration/generate', {
    method: 'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ provider: provider, voice_id: voiceSel.value, texto: texto, nome: nome, pasta: pasta, preview: !!preview })
  });
  var data = await res.json();
  if (!data.ok) {
    toast('Erro: ' + (data.erro||''), 'error');
    document.getElementById('narr-gerar-btn').disabled = false;
    document.getElementById('narr-status-panel').style.display = 'none';
    return;
  }

  // Adicionar ao histórico
  _narrHistorico.unshift({
    nome: nome, voz: vozNome, provider: provider, status: 'gerando',
    data: new Date().toLocaleString('pt-BR'), path: '', chars: texto.length,
  });
  localStorage.setItem('narrHistorico', JSON.stringify(_narrHistorico.slice(0, 50)));
  renderNarrHistorico();

  _narrInterval = setInterval(checkNarracao, 3000);
}

async function checkNarracao() {
  try {
    var res = await fetch('/api/narration/status', { signal: AbortSignal.timeout(10000) });
    var st = await res.json();
  } catch(e) { return; }

  document.getElementById('narr-progress-fill').style.width = (st.progresso||0) + '%';

  if (st.status === 'done') {
    clearInterval(_narrInterval);
    document.getElementById('narr-gerar-btn').disabled = false;
    document.getElementById('narr-preview-btn').disabled = false;
    document.getElementById('narr-status-badge').textContent = 'OK';
    document.getElementById('narr-status-badge').className = 'badge badge-done';
    // Auto-play se preview
    if (st.audio_url) new Audio(st.audio_url).play();
    setTimeout(function(){ document.getElementById('narr-status-panel').style.display = 'none'; }, 2000);
    // Atualizar histórico
    if (_narrHistorico.length && _narrHistorico[0].status === 'gerando') {
      _narrHistorico[0].status = 'ok';
      _narrHistorico[0].path = st.audio_local || '';
      _narrHistorico[0].url = st.audio_url || '';
      localStorage.setItem('narrHistorico', JSON.stringify(_narrHistorico));
    }
    renderNarrHistorico();
    toast('Narração gerada!', 'success');
    carregarCreditos();
  } else if (st.status === 'error') {
    clearInterval(_narrInterval);
    document.getElementById('narr-gerar-btn').disabled = false;
    document.getElementById('narr-preview-btn').disabled = false;
    document.getElementById('narr-status-badge').textContent = 'Erro';
    document.getElementById('narr-status-badge').className = 'badge badge-error';
    if (_narrHistorico.length && _narrHistorico[0].status === 'gerando') {
      _narrHistorico[0].status = 'erro';
      localStorage.setItem('narrHistorico', JSON.stringify(_narrHistorico));
    }
    renderNarrHistorico();
    toast('Erro: ' + (st.erro||''), 'error');
  }
}

function renderNarrHistorico() {
  var container = document.getElementById('narr-historico');
  var empty = document.getElementById('narr-hist-empty');
  if (!_narrHistorico.length) { container.innerHTML = ''; empty.style.display = 'block'; return; }
  empty.style.display = 'none';
  container.innerHTML = _narrHistorico.slice(0, 20).map(function(h, i) {
    var statusClass = h.status === 'ok' ? 'badge-done' : h.status === 'gerando' ? 'badge-transcribing' : 'badge-error';
    var statusText = h.status === 'ok' ? 'OK' : h.status === 'gerando' ? '...' : 'Erro';
    return '<div style="display:flex;align-items:center;gap:10px;padding:6px 10px;background:var(--panel);border:1px solid var(--border);border-radius:6px;font-size:11px">'
      + '<span style="color:var(--text-sec);min-width:110px">' + (h.data||'') + '</span>'
      + '<strong style="min-width:100px">' + (h.nome||'') + '</strong>'
      + '<span style="color:var(--text-sec);flex:1">' + (h.voz||'').substring(0,25) + '</span>'
      + '<span style="color:var(--text-sec)">' + (h.chars||0) + ' chars</span>'
      + '<span class="badge ' + statusClass + '" style="font-size:9px">' + statusText + '</span>'
      + (h.path ? '<button class="btn btn-secondary btn-sm" style="font-size:9px;padding:1px 5px" onclick="copiarTexto(this)" data-text="' + (h.path||'').replace(/"/g,'&quot;') + '">Path</button>' : '')
      + (h.url ? '<button class="btn btn-secondary btn-sm" style="font-size:9px;padding:1px 5px" onclick="playUrl(this)" data-url="' + (h.url||'') + '">Play</button>' : '')
      + '</div>';
  }).join('');
}

function copiarTexto(btn) { navigator.clipboard.writeText(btn.dataset.text); toast('Copiado!', 'success'); }
function playUrl(btn) { new Audio(btn.dataset.url).play(); }

// === NARRAÇÃO BATCH ===
var _narrBatchConfigs = [];

async function renderBatchCards() {
  var grid = document.getElementById('narr-batch-cards');
  grid.innerHTML = '<div style="text-align:center;padding:24px;color:var(--text-sec);font-size:13px"><span style="display:inline-block;width:20px;height:20px;border:2px solid var(--accent);border-top-color:transparent;border-radius:50%;animation:spin 0.8s linear infinite;vertical-align:middle;margin-right:8px"></span>Carregando templates e vozes...</div>';

  if (!templates.length) {
    var r = await fetch('/api/templates');
    templates = await r.json();
  }
  if (!_vozes.length) await carregarVozes();

  // Carregar configs salvas
  var saved = JSON.parse(localStorage.getItem('narrBatchConfigs') || '{}');

  var grid = document.getElementById('narr-batch-cards');
  // Ordenar templates pela ordem salva
  var ordem = JSON.parse(localStorage.getItem('narrBatchOrdem') || '[]');
  if (ordem.length) {
    templates.sort(function(a, b) {
      var ia = ordem.indexOf(a.id), ib = ordem.indexOf(b.id);
      if (ia < 0) ia = 999;
      if (ib < 0) ib = 999;
      return ia - ib;
    });
  }

  grid.innerHTML = templates.map(function(t, idx) {
    var cfg = saved[t.id] || {};
    // Voz: template sempre ganha
    var tmplVoz = (t.narracao_voz || {});
    var vozId = tmplVoz.voice_id || '';
    var provider = cfg.provider || 'minimax_clone';
    return '<div class="card" style="cursor:grab" draggable="true" data-nb-idx="' + idx + '" data-nb-tid="' + t.id + '" ondragstart="nbDragStart(event)" ondragover="event.preventDefault()" ondrop="nbDrop(event)">'
      + '<div class="card-header">'
      + '<span class="card-title">' + (t.tag||t.id) + '</span>'
      + '<span class="card-tag">' + (t.tag||t.idioma||'en').toUpperCase() + '</span>'
      + '</div>'
      + '<div style="font-size:11px;color:var(--text-sec);margin-bottom:8px">' + (t.nome||'') + '</div>'
      + '<div style="font-size:10px;margin-bottom:6px">'
      + '<span style="color:var(--text-sec)">Voz: </span>'
      + (function(){ var v = _vozes.find(function(x){ return x.voice_id === vozId; }); return v ? '<span style="color:var(--info)">' + v.name + '</span>' : '<span style="color:var(--danger)">Não configurada (edite o template)</span>'; })()
      + '<input type="hidden" class="nb-voz" data-tid="' + t.id + '" value="' + vozId + '" data-provider="' + (tmplVoz.provider||'') + '">'
      + '</div>'
      + '<div class="form-group" style="margin:0 0 6px">'
      + '<label style="font-size:10px">Roteiro</label>'
      + '<textarea class="nb-texto" data-tid="' + t.id + '" rows="3" placeholder="Cole o roteiro aqui..." style="font-size:11px;padding:4px 6px" oninput="this.nextElementSibling.textContent=this.value.length+\\' chars\\'">' + (cfg.texto || '').replace(/</g,'&lt;') + '</textarea>'
      + '<span style="font-size:9px;color:var(--text-sec)">' + (cfg.texto ? cfg.texto.length + ' chars' : '0 chars') + '</span>'
      + '</div>'
      + '<div style="display:flex;gap:4px;align-items:center">'
      + '<label style="font-size:10px;color:var(--text-sec)"><input type="checkbox" class="nb-ativo" data-tid="' + t.id + '" ' + (cfg.ativo !== false ? 'checked' : '') + '> Ativo</label>'
      + '<span class="nb-timer" data-tid="' + t.id + '" style="font-size:10px;color:var(--text-sec);font-family:monospace;margin-left:auto">--:--</span>'
      + '<button class="btn btn-danger btn-sm nb-skip" data-tid="' + t.id + '" style="font-size:9px;padding:1px 6px;display:none" onclick="skipNarrJob(this)">Pular</button>'
      + '<span class="badge badge-waiting nb-status" data-tid="' + t.id + '" style="font-size:9px">Aguardando</span>'
      + '</div></div>';
  }).join('');

  // Set date default
  var dateInput = document.getElementById('narr-batch-data');
  if (!dateInput.value) {
    var hoje = new Date();
    dateInput.value = hoje.toISOString().slice(0,10);
  }
}

function _salvarBatchConfigs() {
  var saved = {};
  document.querySelectorAll('.nb-voz').forEach(function(el) {
    var tid = el.dataset.tid;
    var textoEl = document.querySelector('.nb-texto[data-tid="' + tid + '"]');
    saved[tid] = {
      voice_id: el.value,
      provider: el.dataset.provider || '',
      ativo: document.querySelector('.nb-ativo[data-tid="' + tid + '"]').checked,
      texto: textoEl ? textoEl.value : '',
    };
  });
  localStorage.setItem('narrBatchConfigs', JSON.stringify(saved));
}

async function puxarRoteirosBatch() {
  var dateVal = document.getElementById('narr-batch-data').value;
  if (!dateVal) { toast('Selecione a data primeiro', 'error'); return; }

  // Garantir dados de Temas carregados
  if (!temasData || !temasData.linhas || !temasData.linhas.length) {
    try { var r = await fetch('/api/temas'); var raw = await r.json(); if (raw && raw.colunas) temasData = raw; } catch(e){}
  }

  // Encontrar a linha pela data
  var dateParts = dateVal.split('-');
  var dataFormatada = dateParts[2] + '/' + dateParts[1] + '/' + dateParts[0];
  var ri = -1;
  (temasData.linhas || []).forEach(function(row, i) {
    if (row.data === dataFormatada) ri = i;
  });
  if (ri < 0) { toast('Data ' + dataFormatada + ' não encontrada no grid de Temas', 'error'); return; }

  var count = 0;
  templates.forEach(function(t) {
    var textarea = document.querySelector('.nb-texto[data-tid="' + t.id + '"]');
    if (!textarea) return;

    // Encontrar coluna que tem esse template associado
    (temasData.colunas || []).forEach(function(col, ci) {
      if (col.template_id === t.id) {
        var key = ri + '_' + ci;
        var cel = (temasData.celulas || {})[key] || {};
        if (cel.roteiro) {
          textarea.value = cel.roteiro;
          var charSpan = textarea.nextElementSibling;
          if (charSpan) charSpan.textContent = cel.roteiro.length + ' chars';
          count++;
        }
      }
    });
  });

  toast(count + ' roteiros puxados do grid de Temas', count > 0 ? 'success' : 'error');
}

var _nbDragIdx = -1;
function nbDragStart(e) { _nbDragIdx = parseInt(e.target.closest('.card').dataset.nbIdx); e.dataTransfer.effectAllowed = 'move'; }
function nbDrop(e) {
  var targetIdx = parseInt(e.target.closest('.card').dataset.nbIdx);
  if (_nbDragIdx < 0 || _nbDragIdx === targetIdx) return;
  var t = templates.splice(_nbDragIdx, 1)[0];
  templates.splice(targetIdx, 0, t);
  localStorage.setItem('narrBatchOrdem', JSON.stringify(templates.map(function(x){ return x.id; })));
  renderBatchCards();
}

var _batchNarrRunning = false;
var _batchNarrCancelled = false;
var _narrSkipJobs = {};

function cancelarBatchNarracao() {
  _batchNarrCancelled = true;
  toast('Cancelando após job atual...', 'error');
}

function skipNarrJob(btn) {
  _narrSkipJobs[btn.dataset.tid] = true;
  btn.textContent = 'Pulando...';
  btn.disabled = true;
}

async function iniciarBatchNarracao() {
  if (_batchNarrRunning) { toast('Batch já em andamento', 'error'); return; }
  _batchNarrRunning = true;
  _batchNarrCancelled = false;
  _narrSkipJobs = {};
  _salvarBatchConfigs();
  var dateVal = document.getElementById('narr-batch-data').value;
  if (!dateVal) { toast('Selecione a data', 'error'); return; }
  var dateParts = dateVal.split('-');
  var dataFormatada = dateParts[2] + '-' + dateParts[1];
  var pasta = document.getElementById('narr-batch-pasta').value || 'narracoes/';

  var jobs = [];
  templates.forEach(function(t) {
    var ativo = document.querySelector('.nb-ativo[data-tid="' + t.id + '"]');
    if (!ativo || !ativo.checked) return;
    var texto = document.querySelector('.nb-texto[data-tid="' + t.id + '"]');
    var vozEl = document.querySelector('.nb-voz[data-tid="' + t.id + '"]');
    if (!texto || !texto.value.trim()) return;
    if (!vozEl || !vozEl.value) { toast(t.tag + ': sem voz configurada', 'error'); return; }
    var tmplVozCfg = (t.narracao_voz || {});
    jobs.push({
      template_id: t.id,
      tag: t.tag || t.id,
      texto: texto.value.trim(),
      chars: texto.value.trim().length,
      voice_id: vozEl.value,
      provider: vozEl.dataset ? (vozEl.dataset.provider || 'elevenlabs') : 'elevenlabs',
      speed: tmplVozCfg.speed || 1.0,
      pitch: tmplVozCfg.pitch || 0,
      nome: (t.tag || t.id) + ' ' + dataFormatada,
      pasta: pasta,
    });
  });

  if (!jobs.length) { toast('Nenhum template ativo com roteiro', 'error'); return; }

  document.getElementById('narr-batch-status').style.display = 'block';
  var log = document.getElementById('narr-batch-log');
  log.innerHTML = '<div style="font-weight:600;margin-bottom:4px">Iniciando lote: ' + jobs.length + ' narrações | Data: ' + dataFormatada + '</div>';
  var totalCreditsUsed = 0;
  var batchStartTime = Date.now();

  // Timer total que atualiza a cada segundo
  document.getElementById('btn-narr-batch-start').style.display = 'none';
  document.getElementById('btn-narr-batch-cancel').style.display = 'inline-flex';

  var _batchTimerInterval = setInterval(function() {
    var elapsed = Math.floor((Date.now() - batchStartTime) / 1000);
    var m = Math.floor(elapsed / 60), s = elapsed % 60;
    document.getElementById('narr-batch-timer-total').textContent = (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
  }, 1000);

  for (var i = 0; i < jobs.length; i++) {
    // Checar cancelamento
    if (_batchNarrCancelled) {
      log.innerHTML += '<div style="color:var(--danger);font-weight:600">Cancelado pelo usuário</div>';
      break;
    }
    var job = jobs[i];

    // Checar skip individual
    if (_narrSkipJobs[job.template_id]) {
      log.innerHTML += '<div style="color:var(--text-sec)">' + job.tag + ': Pulado pelo usuário</div>';
      var skipBadge = document.querySelector('.nb-status[data-tid="' + job.template_id + '"]');
      if (skipBadge) { skipBadge.textContent = 'Pulado'; skipBadge.className = 'badge badge-cancelled nb-status'; }
      continue;
    }

    var badge = document.querySelector('.nb-status[data-tid="' + job.template_id + '"]');
    var timerEl = document.querySelector('.nb-timer[data-tid="' + job.template_id + '"]');
    var skipBtn = document.querySelector('.nb-skip[data-tid="' + job.template_id + '"]');
    if (badge) { badge.textContent = 'Gerando...'; badge.className = 'badge badge-transcribing nb-status'; }
    if (skipBtn) skipBtn.style.display = 'inline-flex';
    document.getElementById('narr-batch-count').textContent = (i+1) + '/' + jobs.length;
    document.getElementById('narr-batch-fill').style.width = ((i) / jobs.length * 100) + '%';
    var jobStart = Date.now();

    // Timer individual que atualiza a cada segundo
    var _jobTimerInterval = setInterval((function(el, start) {
      return function() {
        var elapsed = Math.floor((Date.now() - start) / 1000);
        var m = Math.floor(elapsed / 60), s = elapsed % 60;
        if (el) el.textContent = (m < 10 ? '0' : '') + m + ':' + (s < 10 ? '0' : '') + s;
      };
    })(timerEl, jobStart), 1000);
    // Verificar se MP3 já existe (na subpasta por data)
    var mp3Nome = job.nome + '.mp3';
    var narrDateParts = dataFormatada.split('-');
    var narrSubpasta = pasta + '/2026-' + narrDateParts[1] + '-' + narrDateParts[0];
    var mp3Existe = false;
    try {
      var chkRes = await fetch('/api/browse?path=' + encodeURIComponent(narrSubpasta));
      var chkFiles = await chkRes.json();
      mp3Existe = chkFiles.some(function(f){ return f.name === mp3Nome; });
    } catch(e) {}
    if (!mp3Existe) {
      // Também checar na pasta raiz (retrocompatibilidade)
      try {
        var chkRes2 = await fetch('/api/browse?path=' + encodeURIComponent(pasta));
        var chkFiles2 = await chkRes2.json();
        mp3Existe = chkFiles2.some(function(f){ return f.name === mp3Nome; });
      } catch(e) {}
    }

    if (mp3Existe) {
      log.innerHTML += '<div style="color:var(--text-sec)">' + job.tag + ': existe (' + mp3Nome + ') - pulando</div>';
      if (badge) { badge.textContent = 'Existe'; badge.className = 'badge badge-done nb-status'; }
      clearInterval(_jobTimerInterval);
      if (timerEl) { timerEl.textContent = 'OK'; timerEl.style.color = 'var(--accent)'; }
      continue;
    }

    log.innerHTML += '<div style="color:var(--text-sec)">' + job.tag + ': Enviando (' + job.chars + ' chars)...</div>';
    log.scrollTop = 99999;

    // Esperar narração anterior terminar + delay
    var _waitNarr = 0;
    while (_waitNarr < 30) {
      try { var _ns = await (await fetch('/api/narration/status')).json(); if (!_ns.ativo) break; } catch(e){}
      await new Promise(function(r){ setTimeout(r, 2000); });
      _waitNarr++;
    }
    await new Promise(function(r){ setTimeout(r, 1000); });

    try {
      var res = await fetch('/api/narration/generate', {
        method: 'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify(job)
      });
      var data = await res.json();
      if (!data.ok) {
        log.innerHTML += '<div style="color:var(--danger)">' + job.tag + ': ERRO - ' + (data.erro||'') + '</div>';
        if (badge) { badge.textContent = 'Erro'; badge.className = 'badge badge-error nb-status'; }
        continue;
      }

      var done = false;
      while (!done) {
        await new Promise(function(r){ setTimeout(r, 3000); });
        try {
          var stRes = await fetch('/api/narration/status', { signal: AbortSignal.timeout(10000) });
          var st = await stRes.json();
        } catch(e) { continue; }
        if (st.status === 'done') {
          done = true;
          var jobTime = ((Date.now() - jobStart) / 1000).toFixed(1);
          var creditsUsed = st.credit_cost || 0;
          totalCreditsUsed += creditsUsed;
          log.innerHTML += '<div style="color:var(--accent)">' + job.tag + ': OK | ' + jobTime + 's | ' + (creditsUsed > 0 ? creditsUsed + ' créditos' : '') + ' | ' + (st.audio_local||'').split('/').pop() + '</div>';
          if (badge) { badge.textContent = 'OK'; badge.className = 'badge badge-done nb-status'; }
        } else if (st.status === 'error') {
          done = true;
          log.innerHTML += '<div style="color:var(--danger)">' + job.tag + ': ERRO - ' + (st.erro||'') + '</div>';
          if (badge) { badge.textContent = 'Erro'; badge.className = 'badge badge-error nb-status'; }
        }
      }
    } catch(e) {
      log.innerHTML += '<div style="color:var(--danger)">' + job.tag + ': ' + e.message + '</div>';
    }
    clearInterval(_jobTimerInterval);
    // Mostrar tempo final do job
    if (timerEl) {
      var jobElapsed = Math.floor((Date.now() - jobStart) / 1000);
      var jm = Math.floor(jobElapsed / 60), js = jobElapsed % 60;
      timerEl.textContent = (jm < 10 ? '0' : '') + jm + ':' + (js < 10 ? '0' : '') + js;
      timerEl.style.color = 'var(--accent)';
    }
  }

  clearInterval(_batchTimerInterval);
  document.getElementById('narr-batch-fill').style.width = '100%';
  document.getElementById('narr-batch-count').textContent = jobs.length + '/' + jobs.length;
  var totalTime = ((Date.now() - batchStartTime) / 1000).toFixed(0);
  log.innerHTML += '<div style="font-weight:600;margin-top:6px;padding-top:6px;border-top:1px solid var(--border)">Concluído | Tempo total: ' + totalTime + 's | Créditos gastos: ' + totalCreditsUsed.toLocaleString() + '</div>';
  log.scrollTop = 99999;
  toast(_batchNarrCancelled ? 'Lote cancelado' : 'Lote concluído!', _batchNarrCancelled ? 'error' : 'success');
  carregarCreditos();
  _batchNarrRunning = false;
  document.getElementById('btn-narr-batch-start').style.display = 'inline-flex';
  document.getElementById('btn-narr-batch-cancel').style.display = 'none';
  document.querySelectorAll('.nb-skip').forEach(function(b){ b.style.display = 'none'; });
}

// === TEMAS (GRID) ===
var temasData = { colunas: [], linhas: [], celulas: {} };
var _celulaEditando = null; // {row, col}

async function carregarTemas() {
  var res = await fetch('/api/temas');
  var raw = await res.json();
  // temas é um objeto com colunas, linhas, celulas
  if (raw && raw.colunas) {
    temasData = raw;
  } else if (Array.isArray(raw) && !raw.length) {
    temasData = { colunas: [], linhas: [], celulas: {} };
  }
  renderTemasGrid();
}

function renderTemasGrid() {
  var thead = document.getElementById('temas-thead');
  var tbody = document.getElementById('temas-tbody');
  var empty = document.getElementById('temas-empty');
  var cols = temasData.colunas || [];
  var rows = temasData.linhas || [];

  if (!cols.length && !rows.length) {
    thead.innerHTML = '';
    tbody.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  // Header
  var th = '<tr><th style="min-width:100px">Data</th>';
  cols.forEach(function(col, ci) {
    th += '<th draggable="true" data-col="' + ci + '" ondragstart="dragColStart(event)" ondragover="event.preventDefault()" ondrop="dropCol(event)">'
      + '<div ondblclick="editarColuna(' + ci + ')" style="cursor:pointer">' + col.nome
      + '<div style="font-size:8px;font-weight:400;color:var(--text-sec);margin-top:2px;line-height:1.3">'
      + (col.pipeline_id ? '<span style="color:var(--accent)">R</span> ' : '')
      + (col.voice_id ? '<span style="color:var(--info)">N</span> ' : '')
      + (col.template_id ? '<span style="color:var(--warn)">V</span>' : '')
      + (!col.pipeline_id && !col.voice_id && !col.template_id ? '<span style="opacity:0.4">duplo-clique p/ configurar</span>' : '')
      + '</div></div>'
      + '<div class="col-actions">'
      + '<button style="background:none;border:none;color:var(--text-sec);cursor:pointer;font-size:10px" onclick="removerColuna(' + ci + ')">X</button>'
      + '</div></th>';
  });
  th += '<th style="min-width:40px;border:none;background:none"></th></tr>';
  thead.innerHTML = th;

  // Rows
  var html = '';
  rows.forEach(function(row, ri) {
    var collapsed = row.collapsed ? ' style="display:none"' : '';
    var collapseIcon = row.collapsed ? '&#9654;' : '&#9660;';
    html += '<tr draggable="true" data-row="' + ri + '" ondragstart="dragRowStart(event)" ondragover="event.preventDefault()" ondrop="dropRow(event)"' + (row.collapsed ? ' class="row-collapsed"' : '') + '>';
    html += '<td>'
      + '<div style="display:flex;align-items:center;gap:4px">'
      + '<button style="background:none;border:none;color:var(--text-sec);cursor:pointer;font-size:10px;padding:0" onclick="toggleRowCollapse(' + ri + ')">' + collapseIcon + '</button>'
      + '<span ondblclick="renomearLinha(' + ri + ')" style="font-size:11px">' + row.data + '</span>'
      + '</div>'
      + '</td>';

    cols.forEach(function(col, ci) {
      var key = ri + '_' + ci;
      var cel = (temasData.celulas || {})[key] || {};
      var tema = cel.tema || '';
      var titulo = cel.titulo || '';
      var thumb = cel.thumb || '';
      // Status de produção da célula
      var cellStatus = 'empty';
      if (cel.done) cellStatus = 'done-' + (cel.done_type || 'manual');
      else if (cel.roteiro || cel.tem_roteiro) cellStatus = 'roteiro';
      else if (cel.titulo) cellStatus = 'titulo';
      else if (cel.tema) cellStatus = 'tema';

      var statusColors = {'empty':'var(--border)','tema':'var(--warn)','titulo':'var(--info)','roteiro':'#9b59b6','done-manual':'var(--accent)','done-auto':'var(--accent)'};
      var statusTitles = {'empty':'Vazio','tema':'Tema preenchido','titulo':'Título definido','roteiro':'Roteiro gerado','done-manual':'Concluído (manual)','done-auto':'Concluído (automático)'};
      var dotStyle = 'width:8px;height:8px;border-radius:50%;background:' + (statusColors[cellStatus]||'var(--border)');
      if (cellStatus === 'done-auto') dotStyle += ';border:2px solid #fff';

      html += '<td><div class="tema-cell" onclick="editarCelula(' + ri + ',' + ci + ')"' + (row.collapsed ? ' style="min-height:20px;padding:4px"' : '') + '>';
      if (row.collapsed) {
        // Modo colapsado: só bolinha de status
        html += '<div style="display:flex;justify-content:center"><div style="' + dotStyle + '" title="' + (statusTitles[cellStatus]||'') + '"></div></div>';
      } else if (tema || titulo) {
        if (tema) html += '<div style="font-size:10px;color:var(--accent);margin-bottom:2px;font-style:italic">' + tema.substring(0, 60).replace(/</g,'&lt;') + '</div>';
        if (titulo) html += '<div class="tc-titulo">' + titulo.substring(0, 60).replace(/</g,'&lt;') + '</div>';
        if (thumb) html += '<div class="tc-thumb">' + thumb.substring(0, 40).replace(/</g,'&lt;') + '</div>';
        html += '<div style="position:absolute;top:4px;right:4px;' + dotStyle + '" title="' + (statusTitles[cellStatus]||'') + '"></div>';
      } else {
        html += '<div class="tc-empty">Clique para adicionar</div>';
      }
      html += '</div></td>';
    });

    html += '<td style="border:none;background:none;padding:4px">'
      + '<button style="background:none;border:none;color:var(--danger);cursor:pointer;font-size:14px;opacity:0.3" onclick="removerLinha(' + ri + ')" onmouseover="this.style.opacity=1" onmouseout="this.style.opacity=0.3">X</button>'
      + '</td></tr>';
  });
  tbody.innerHTML = html;
}

function adicionarColunaTemas() {
  var nome = prompt('Nome da coluna (canal):');
  if (!nome) return;
  if (!temasData.colunas) temasData.colunas = [];
  temasData.colunas.push({ nome: nome, pipeline_id: '' });
  salvarTemasLocal();
  renderTemasGrid();
  // Abrir config da coluna
  editarColuna(temasData.colunas.length - 1);
}

function adicionarLinhaTemas() {
  var hoje = new Date();
  var rows = temasData.linhas || [];
  // Próxima data = última + 1 dia, ou hoje
  var ultima = rows.length ? rows[rows.length-1].data : '';
  var novaData;
  if (ultima) {
    var parts = ultima.split('/');
    var d = new Date(parts[2], parts[1]-1, parseInt(parts[0])+1);
    novaData = d.toLocaleDateString('pt-BR', {day:'2-digit',month:'2-digit',year:'numeric'});
  } else {
    novaData = hoje.toLocaleDateString('pt-BR', {day:'2-digit',month:'2-digit',year:'numeric'});
  }
  var data = prompt('Data (DD/MM/YYYY):', novaData);
  if (!data) return;
  if (!temasData.linhas) temasData.linhas = [];
  temasData.linhas.push({ data: data, status: 'pendente' });
  salvarTemasLocal();
  renderTemasGrid();
}

function renomearColuna(ci) { editarColuna(ci); }

function editarColuna(ci) {
  var col = temasData.colunas[ci];
  if (!col) return;

  var pipOpts = '<option value="">Nenhuma</option>' + pipelines.map(function(p) {
    return '<option value="' + p.id + '"' + (col.pipeline_id === p.id ? ' selected' : '') + '>' + p.nome + '</option>';
  }).join('');

  var tmplOpts = '<option value="">Nenhum</option>' + templates.map(function(t) {
    return '<option value="' + t.id + '"' + (col.template_id === t.id ? ' selected' : '') + '>' + (t.tag||t.id) + ' - ' + (t.nome||'') + '</option>';
  }).join('');

  var vozOpts = '<option value="">Nenhuma</option>' + _vozes.map(function(v) {
    return '<option value="' + v.voice_id + '" data-provider="' + v.provider + '"' + (col.voice_id === v.voice_id ? ' selected' : '') + '>' + v.name + ' (' + v.provider + ')</option>';
  }).join('');

  var div = document.createElement('div');
  div.className = 'modal-overlay active';
  div.id = 'modal-col-edit';
  div.innerHTML = '<div class="modal" style="max-width:450px">'
    + '<div class="modal-header"><h3>Configurar Coluna</h3>'
    + '<button class="modal-close" onclick="document.getElementById(\\'modal-col-edit\\').remove()">&times;</button></div>'
    + '<div class="modal-body">'
    + '<div class="form-group"><label>Nome da Coluna</label>'
    + '<input type="text" id="col-edit-nome" value="' + (col.nome || '').replace(/"/g,'&quot;') + '"></div>'
    + '<div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;margin-bottom:12px">'
    + '<div style="font-size:12px;font-weight:600;margin-bottom:8px;color:var(--accent)">Roteiro</div>'
    + '<div class="form-group" style="margin:0"><label>Pipeline</label>'
    + '<select id="col-edit-pipeline">' + pipOpts + '</select></div></div>'
    + '<div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px;margin-bottom:12px">'
    + '<div style="font-size:12px;font-weight:600;margin-bottom:8px;color:var(--info)">Narração</div>'
    + '<div class="form-group" style="margin:0 0 8px"><label>Voz</label>'
    + '<select id="col-edit-voz">' + vozOpts + '</select></div></div>'
    + '<div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:12px">'
    + '<div style="font-size:12px;font-weight:600;margin-bottom:8px;color:var(--warn)">Produção</div>'
    + '<div class="form-group" style="margin:0"><label>Template de Vídeo</label>'
    + '<select id="col-edit-template">' + tmplOpts + '</select></div></div>'
    + '</div>'
    + '<div class="modal-footer">'
    + '<button class="btn btn-secondary" onclick="document.getElementById(\\'modal-col-edit\\').remove()">Cancelar</button>'
    + '<button class="btn btn-primary" onclick="salvarColuna(' + ci + ')">Salvar</button>'
    + '</div></div>';
  document.body.appendChild(div);
}

function salvarColuna(ci) {
  temasData.colunas[ci].nome = document.getElementById('col-edit-nome').value;
  temasData.colunas[ci].pipeline_id = document.getElementById('col-edit-pipeline').value;
  temasData.colunas[ci].template_id = document.getElementById('col-edit-template').value;
  var vozSel = document.getElementById('col-edit-voz');
  temasData.colunas[ci].voice_id = vozSel.value;
  temasData.colunas[ci].voice_provider = vozSel.selectedOptions[0] ? vozSel.selectedOptions[0].dataset.provider || '' : '';
  document.getElementById('modal-col-edit').remove();
  salvarTemasLocal();
  renderTemasGrid();
  atualizarLoteDataSelect();
  toast('Coluna atualizada', 'success');
}

function toggleAllRows() {
  var anyExpanded = temasData.linhas.some(function(r){ return !r.collapsed; });
  temasData.linhas.forEach(function(r){ r.collapsed = anyExpanded; });
  salvarTemasLocal();
  renderTemasGrid();
}

function toggleRowCollapse(ri) {
  temasData.linhas[ri].collapsed = !temasData.linhas[ri].collapsed;
  salvarTemasLocal();
  renderTemasGrid();
}

function toggleDone() {
  if (!_celulaEditando) return;
  var key = _celulaEditando.row + '_' + _celulaEditando.col;
  if (!temasData.celulas) temasData.celulas = {};
  if (!temasData.celulas[key]) temasData.celulas[key] = {};
  var cel = temasData.celulas[key];
  if (cel.done) {
    cel.done = false;
    cel.done_type = '';
    document.getElementById('cel-done-btn').textContent = 'Done';
    document.getElementById('cel-done-btn').style.background = 'var(--accent)';
    toast('Done removido', 'success');
  } else {
    cel.done = true;
    cel.done_type = 'manual';
    document.getElementById('cel-done-btn').textContent = 'Done (manual)';
    document.getElementById('cel-done-btn').style.background = 'var(--border)';
    toast('Marcado como Done (manual)', 'success');
  }
  salvarTemasLocal();
}

function renomearLinha(ri) {
  var novo = prompt('Nova data:', temasData.linhas[ri].data);
  if (novo) { temasData.linhas[ri].data = novo; salvarTemasLocal(); renderTemasGrid(); }
}

function removerColuna(ci) {
  if (!confirm('Remover coluna "' + temasData.colunas[ci].nome + '"?')) return;
  temasData.colunas.splice(ci, 1);
  // Re-indexar celulas
  var novas = {};
  Object.keys(temasData.celulas || {}).forEach(function(k) {
    var parts = k.split('_');
    var r = parseInt(parts[0]), c = parseInt(parts[1]);
    if (c < ci) novas[r + '_' + c] = temasData.celulas[k];
    else if (c > ci) novas[r + '_' + (c-1)] = temasData.celulas[k];
  });
  temasData.celulas = novas;
  salvarTemasLocal();
  renderTemasGrid();
}

function removerLinha(ri) {
  if (!confirm('Remover linha ' + temasData.linhas[ri].data + '?')) return;
  temasData.linhas.splice(ri, 1);
  var novas = {};
  Object.keys(temasData.celulas || {}).forEach(function(k) {
    var parts = k.split('_');
    var r = parseInt(parts[0]), c = parseInt(parts[1]);
    if (r < ri) novas[r + '_' + c] = temasData.celulas[k];
    else if (r > ri) novas[(r-1) + '_' + c] = temasData.celulas[k];
  });
  temasData.celulas = novas;
  salvarTemasLocal();
  renderTemasGrid();
}

// Drag & drop colunas
var _dragCol = -1;
function dragColStart(e) { _dragCol = parseInt(e.target.closest('th').dataset.col); e.dataTransfer.effectAllowed = 'move'; }
function dropCol(e) {
  var target = parseInt(e.target.closest('th').dataset.col);
  if (_dragCol < 0 || _dragCol === target) return;
  var col = temasData.colunas.splice(_dragCol, 1)[0];
  temasData.colunas.splice(target, 0, col);
  // Re-indexar celulas
  var novas = {};
  var map = {};
  temasData.colunas.forEach(function(c, i) { map[i] = i; });
  // Simple swap for now
  Object.keys(temasData.celulas || {}).forEach(function(k) {
    var parts = k.split('_');
    var r = parseInt(parts[0]), c = parseInt(parts[1]);
    var nc = c;
    if (c === _dragCol) nc = target;
    else if (_dragCol < target && c > _dragCol && c <= target) nc = c - 1;
    else if (_dragCol > target && c >= target && c < _dragCol) nc = c + 1;
    novas[r + '_' + nc] = temasData.celulas[k];
  });
  temasData.celulas = novas;
  _dragCol = -1;
  salvarTemasLocal();
  renderTemasGrid();
}

// Drag & drop linhas
var _dragRow = -1;
function dragRowStart(e) { _dragRow = parseInt(e.target.closest('tr').dataset.row); e.dataTransfer.effectAllowed = 'move'; }
function dropRow(e) {
  var target = parseInt(e.target.closest('tr').dataset.row);
  if (_dragRow < 0 || _dragRow === target) return;
  var row = temasData.linhas.splice(_dragRow, 1)[0];
  temasData.linhas.splice(target, 0, row);
  var novas = {};
  Object.keys(temasData.celulas || {}).forEach(function(k) {
    var parts = k.split('_');
    var r = parseInt(parts[0]), c = parseInt(parts[1]);
    var nr = r;
    if (r === _dragRow) nr = target;
    else if (_dragRow < target && r > _dragRow && r <= target) nr = r - 1;
    else if (_dragRow > target && r >= target && r < _dragRow) nr = r + 1;
    novas[nr + '_' + c] = temasData.celulas[k];
  });
  temasData.celulas = novas;
  _dragRow = -1;
  salvarTemasLocal();
  renderTemasGrid();
}

// Editar célula
function editarCelula(ri, ci) {
  _celulaEditando = { row: ri, col: ci };
  var key = ri + '_' + ci;
  var cel = (temasData.celulas || {})[key] || {};
  var colNome = temasData.colunas[ci].nome;
  var rowData = temasData.linhas[ri].data;
  document.getElementById('celula-title').textContent = colNome + ' - ' + rowData;
  document.getElementById('cel-tema').value = cel.tema || '';
  document.getElementById('cel-titulo').value = cel.titulo || '';
  document.getElementById('cel-thumb').value = cel.thumb || '';
  // Carregar roteiro completo da API (não vem no light mode)
  if (cel.tem_roteiro && !cel.roteiro) {
    document.getElementById('cel-roteiro').value = 'Carregando roteiro...';
    fetch('/api/temas?light=false').then(function(r){ return r.json(); }).then(function(fullData){
      var fullCel = (fullData.celulas || {})[key] || {};
      if (fullCel.roteiro) {
        document.getElementById('cel-roteiro').value = fullCel.roteiro;
        document.getElementById('cel-roteiro-chars').textContent = fullCel.roteiro.length + ' chars';
        // Cachear localmente
        if (!temasData.celulas[key]) temasData.celulas[key] = {};
        temasData.celulas[key].roteiro = fullCel.roteiro;
      }
    });
  } else {
    document.getElementById('cel-roteiro').value = cel.roteiro || '';
  }
  document.getElementById('cel-roteiro-chars').textContent = (cel.roteiro ? cel.roteiro.length : (cel.tem_roteiro || 0)) + ' chars';
  document.getElementById('cel-roteiro').oninput = function() {
    document.getElementById('cel-roteiro-chars').textContent = this.value.length + ' chars';
  };
  // Pipelines dropdown
  var sel = document.getElementById('cel-pipeline');
  sel.innerHTML = '<option value="">Nenhuma</option>' + pipelines.map(function(p) {
    return '<option value="' + p.id + '"' + (cel.pipeline_id===p.id?' selected':'') + '>' + p.nome + '</option>';
  }).join('');
  // Atualizar botão Done
  var doneBtn = document.getElementById('cel-done-btn');
  if (cel.done) {
    doneBtn.textContent = 'Done (' + (cel.done_type || 'manual') + ')';
    doneBtn.style.background = 'var(--border)';
  } else {
    doneBtn.textContent = 'Done';
    doneBtn.style.background = 'var(--accent)';
  }

  document.getElementById('modal-celula').classList.add('active');
  _initCaseBtns();
}

function fecharCelula() { document.getElementById('modal-celula').classList.remove('active'); }

function salvarCelula() {
  if (!_celulaEditando) return;
  var key = _celulaEditando.row + '_' + _celulaEditando.col;
  if (!temasData.celulas) temasData.celulas = {};
  var existingCel = temasData.celulas[key] || {};
  temasData.celulas[key] = {
    tema: document.getElementById('cel-tema').value,
    titulo: document.getElementById('cel-titulo').value,
    thumb: document.getElementById('cel-thumb').value,
    roteiro: document.getElementById('cel-roteiro').value,
    pipeline_id: document.getElementById('cel-pipeline').value,
    done: existingCel.done || false,
    done_type: existingCel.done_type || '',
    synced: false,
  };
  fecharCelula();
  salvarTemasLocal();
  renderTemasGrid();
}

var _replicarCampo = '';
var _temasUndo = []; // pilha de undo

function abrirReplicar(campo) {
  if (!_celulaEditando) return;
  _replicarCampo = campo;
  var valor = document.getElementById('cel-' + campo).value;
  if (!valor.trim()) { toast('Campo vazio', 'error'); return; }

  document.getElementById('replicar-title').textContent = 'Replicar ' + {tema:'Tema',titulo:'Título',thumb:'Thumbnail'}[campo] + ' para...';
  document.getElementById('replicar-valor').value = valor;
  document.getElementById('replicar-campo-destino').value = 'mesmo';
  document.getElementById('replicar-campo-destino').closest('.form-group').style.display = '';

  // Preencher datas
  var dataSel = document.getElementById('replicar-data');
  var rows = temasData.linhas || [];
  dataSel.innerHTML = rows.map(function(r, i) {
    return '<option value="' + i + '"' + (i === _celulaEditando.row ? ' selected' : '') + '>' + r.data + '</option>';
  }).join('');

  // Preencher canais (excluir o atual)
  var cols = temasData.colunas || [];
  var canaisDiv = document.getElementById('replicar-canais');
  canaisDiv.innerHTML = cols.map(function(col, ci) {
    if (ci === _celulaEditando.col) return '';
    return '<label style="display:flex;align-items:center;gap:8px;padding:4px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;cursor:pointer;font-size:12px">'
      + '<input type="checkbox" class="replicar-check" value="' + ci + '">'
      + '<span>' + col.nome + '</span></label>';
  }).join('');

  document.getElementById('modal-replicar').classList.add('active');
}

function _executarReplicar(colunas) {
  // Salvar snapshot para undo
  _temasUndo.push(JSON.stringify(temasData));
  if (_temasUndo.length > 10) _temasUndo.shift(); // max 10 undos

  var ri = parseInt(document.getElementById('replicar-data').value);
  var count = 0;

  if (_replicarCampo === '_all') {
    // Replicar todos os campos
    var tema = document.getElementById('cel-tema').value;
    var titulo = document.getElementById('cel-titulo').value;
    var thumb = document.getElementById('cel-thumb').value;
    colunas.forEach(function(ci) {
      var key = ri + '_' + ci;
      if (!temasData.celulas) temasData.celulas = {};
      if (!temasData.celulas[key]) temasData.celulas[key] = {};
      if (tema) temasData.celulas[key].tema = tema;
      if (titulo) temasData.celulas[key].titulo = titulo;
      if (thumb) temasData.celulas[key].thumb = thumb;
      temasData.celulas[key].synced = false;
      count++;
    });
  } else {
    // Replicar campo individual
    var valor = document.getElementById('replicar-valor').value;
    var campoOrigem = _replicarCampo;
    var campoDestino = document.getElementById('replicar-campo-destino').value;
    var campo = campoDestino === 'mesmo' ? campoOrigem : campoDestino;
    colunas.forEach(function(ci) {
      var key = ri + '_' + ci;
      if (!temasData.celulas) temasData.celulas = {};
      if (!temasData.celulas[key]) temasData.celulas[key] = {};
      temasData.celulas[key][campo] = valor;
      temasData.celulas[key].synced = false;
      count++;
    });
  }

  // Restaurar visibilidade do campo destino
  document.getElementById('replicar-campo-destino').closest('.form-group').style.display = '';

  salvarTemasLocal();
  renderTemasGrid();
  document.getElementById('modal-replicar').classList.remove('active');
  document.getElementById('btn-undo-temas').style.display = 'inline-flex';
  toast('Replicado para ' + count + ' canais', 'success');
}

function undoTemas() {
  if (!_temasUndo.length) { toast('Nada para desfazer', 'error'); return; }
  temasData = JSON.parse(_temasUndo.pop());
  salvarTemasLocal();
  renderTemasGrid();
  if (!_temasUndo.length) document.getElementById('btn-undo-temas').style.display = 'none';
  toast('Desfeito!', 'success');
}

function replicarSelecionados() {
  var checks = document.querySelectorAll('.replicar-check:checked');
  var colunas = Array.from(checks).map(function(c){ return parseInt(c.value); });
  if (!colunas.length) { toast('Selecione ao menos um canal', 'error'); return; }
  _executarReplicar(colunas);
}

function replicarTodos() {
  var cols = temasData.colunas || [];
  var colunas = [];
  cols.forEach(function(col, ci) {
    if (ci !== _celulaEditando.col) colunas.push(ci);
  });
  _executarReplicar(colunas);
}

function abrirReplicarTudo() {
  if (!_celulaEditando) return;
  var tema = document.getElementById('cel-tema').value;
  var titulo = document.getElementById('cel-titulo').value;
  var thumb = document.getElementById('cel-thumb').value;
  if (!tema && !titulo && !thumb) { toast('Todos os campos estão vazios', 'error'); return; }

  _replicarCampo = '_all';
  document.getElementById('replicar-title').textContent = 'Replicar Tudo para...';
  document.getElementById('replicar-valor').value = (tema ? 'Tema: ' + tema.substring(0,50) : '') + (titulo ? '\\nTítulo: ' + titulo.substring(0,50) : '') + (thumb ? '\\nThumb: ' + thumb.substring(0,30) : '');
  document.getElementById('replicar-campo-destino').closest('.form-group').style.display = 'none';

  var dataSel = document.getElementById('replicar-data');
  var rows = temasData.linhas || [];
  dataSel.innerHTML = rows.map(function(r, i) {
    return '<option value="' + i + '"' + (i === _celulaEditando.row ? ' selected' : '') + '>' + r.data + '</option>';
  }).join('');

  var cols = temasData.colunas || [];
  var canaisDiv = document.getElementById('replicar-canais');
  canaisDiv.innerHTML = cols.map(function(col, ci) {
    if (ci === _celulaEditando.col) return '';
    return '<label style="display:flex;align-items:center;gap:8px;padding:4px 8px;background:var(--bg);border:1px solid var(--border);border-radius:4px;cursor:pointer;font-size:12px">'
      + '<input type="checkbox" class="replicar-check" value="' + ci + '">'
      + '<span>' + col.nome + '</span></label>';
  }).join('');

  document.getElementById('modal-replicar').classList.add('active');
}

function _initCaseBtns() {
  document.querySelectorAll('.case-btns').forEach(function(span) {
    if (span.children.length) return;
    var tid = span.dataset.target;
    span.innerHTML = '<button type="button" style="font-size:9px;padding:1px 5px;background:none;border:1px solid var(--border);border-radius:3px;color:var(--text-sec);cursor:pointer;margin-left:2px" onclick="caseTransform(\\'' + tid + '\\',\\'upper\\')" title="MAIÚSCULA">AA</button>'
      + '<button type="button" style="font-size:9px;padding:1px 5px;background:none;border:1px solid var(--border);border-radius:3px;color:var(--text-sec);cursor:pointer;margin-left:2px" onclick="caseTransform(\\'' + tid + '\\',\\'lower\\')" title="minúscula">aa</button>'
      + '<button type="button" style="font-size:9px;padding:1px 5px;background:none;border:1px solid var(--border);border-radius:3px;color:var(--text-sec);cursor:pointer;margin-left:2px" onclick="caseTransform(\\'' + tid + '\\',\\'title\\')" title="Título">Aa</button>';
  });
}

function caseTransform(targetId, mode) {
  var el = document.getElementById(targetId);
  if (!el) return;
  if (mode === 'upper') el.value = el.value.toUpperCase();
  else if (mode === 'lower') el.value = el.value.toLowerCase();
  else if (mode === 'title') el.value = el.value.replace(/\\b\\w/g, function(c){ return c.toUpperCase(); });
}

async function gerarRoteiroCelula() {
  if (!_celulaEditando) return;
  // Salvar célula primeiro
  salvarCelula();
  // Reabrir pra manter o modal visível
  editarCelula(_celulaEditando.row, _celulaEditando.col);

  var ri = _celulaEditando.row, ci = _celulaEditando.col;
  var key = ri + '_' + ci;
  var cel = (temasData.celulas || {})[key] || {};
  var col = temasData.colunas[ci] || {};
  var row = temasData.linhas[ri] || {};
  var pipelineId = cel.pipeline_id || col.pipeline_id || '';

  if (!pipelineId) { toast('Selecione uma pipeline na célula ou configure na coluna', 'error'); return; }
  if (!cel.tema) { toast('Preencha o campo Tema primeiro', 'error'); return; }

  document.getElementById('cel-gerar-btn').disabled = true;
  document.getElementById('cel-gen-status').style.display = 'block';
  document.getElementById('cel-gen-fill').style.width = '5%';
  document.getElementById('cel-gen-badge').textContent = 'Enviando...';
  document.getElementById('cel-gen-badge').className = 'badge badge-transcribing';

  var res = await fetch('/api/pipelines/' + pipelineId + '/executar', {
    method: 'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({
      entrada: cel.tema,
      tema: cel.tema,
      titulo: cel.titulo || '',
      thumb: cel.thumb || '',
      canal: col.nome || '',
      data: row.data || '',
    })
  });
  if (!res.ok) {
    var err = await res.json();
    var erroMsg = err.detail || 'Falha ao iniciar pipeline';
    document.getElementById('cel-gerar-btn').disabled = false;
    document.getElementById('cel-gen-badge').textContent = 'Erro';
    document.getElementById('cel-gen-badge').className = 'badge badge-error';
    document.getElementById('cel-gen-etapas').innerHTML = '<div style="color:var(--danger);font-size:11px;margin-top:4px">' + erroMsg + '</div>';
    toast('Erro: ' + erroMsg, 'error');
    return;
  }

  // Poll
  var pollInterval = setInterval(async function() {
    try {
      var sr = await fetch('/api/pipelines/execucao');
      var st = await sr.json();
    } catch(e) { return; }

    if (!st.etapas) return;
    var total = st.etapas.length;
    var concluidas = st.etapas.filter(function(e){ return e.status==='concluido'; }).length;
    var pct = total > 0 ? Math.round(concluidas / total * 100) : 0;
    document.getElementById('cel-gen-fill').style.width = pct + '%';

    var etapaInfo = st.etapas.map(function(e){
      var icon = e.status === 'concluido' ? 'OK' : e.status === 'processando' ? '...' : e.status === 'erro' ? 'X' : '-';
      return icon + ' ' + e.nome;
    }).join(' | ');
    document.getElementById('cel-gen-etapas').textContent = etapaInfo;

    if (!st.ativo) {
      clearInterval(pollInterval);
      document.getElementById('cel-gerar-btn').disabled = false;
      if (st.resultado_final) {
        document.getElementById('cel-gen-badge').textContent = 'Concluído';
        document.getElementById('cel-gen-badge').className = 'badge badge-done';
        toast('Roteiro gerado!', 'success');
        // Salvar resultado na célula e mostrar no campo
        if (!temasData.celulas) temasData.celulas = {};
        if (!temasData.celulas[key]) temasData.celulas[key] = {};
        temasData.celulas[key].roteiro = st.resultado_final;
        temasData.celulas[key].synced = false;
        document.getElementById('cel-roteiro').value = st.resultado_final;
        document.getElementById('cel-roteiro-chars').textContent = st.resultado_final.length + ' chars';
        salvarTemasLocal();
      } else {
        document.getElementById('cel-gen-badge').textContent = 'Erro';
        document.getElementById('cel-gen-badge').className = 'badge badge-error';
        // Mostrar detalhes do erro no painel e no toast
        var erros = st.etapas ? st.etapas.filter(function(e){ return e.status==='erro'; }).map(function(e){ return e.nome + ': ' + (e.erro||''); }) : [];
        var erroMsg = erros.join(' | ') || 'Falha na geração';
        document.getElementById('cel-gen-etapas').innerHTML = '<div style="color:var(--danger);font-size:11px;margin-top:4px;white-space:pre-wrap">' + erroMsg.replace(/</g,'&lt;') + '</div>';
        toast('Erro: ' + erroMsg.substring(0, 100), 'error');
      }
    }
  }, 2000);
}

function copiarCelula() {
  var tema = document.getElementById('cel-tema').value;
  var titulo = document.getElementById('cel-titulo').value;
  var thumb = document.getElementById('cel-thumb').value;
  var texto = (tema ? 'Tema: ' + tema + '\\n' : '') + titulo + (thumb ? '\\nThumb: ' + thumb : '');
  navigator.clipboard.writeText(texto).then(function() { toast('Copiado!', 'success'); });
}

async function salvarTemasLocal() {
  await fetch('/api/temas', {
    method: 'POST', headers:{'Content-Type':'application/json'},
    body: JSON.stringify(temasData)
  });
}

async function syncTemasSupabase() {
  toast('Sincronizando com Supabase...', 'success');
  var config = await (await fetch('/api/config')).json();
  var rows = temasData.linhas || [];
  var cols = temasData.colunas || [];
  var count = 0;
  rows.forEach(function(row, ri) {
    cols.forEach(function(col, ci) {
      var key = ri + '_' + ci;
      var cel = (temasData.celulas || {})[key];
      if (cel && cel.titulo && !cel.synced) {
        // Mark as synced locally
        cel.synced = true;
        count++;
      }
    });
    row.status = 'ok';
  });
  await salvarTemasLocal();
  renderTemasGrid();
  toast(count + ' temas sincronizados', 'success');
}

// === GERAÇÃO LOTE ROTEIROS ===
function atualizarLoteDataSelect() {
  var sel = document.getElementById('lote-data-select');
  var rows = temasData.linhas || [];
  sel.innerHTML = '<option value="">Selecione a data</option>' + rows.map(function(r, i) {
    return '<option value="' + i + '">' + r.data + '</option>';
  }).join('');
  sel.onchange = function() { previewLote(); };
}

function previewLote() {
  var ri = parseInt(document.getElementById('lote-data-select').value);
  var preview = document.getElementById('lote-preview');
  if (isNaN(ri)) { preview.innerHTML = ''; return; }
  var cols = temasData.colunas || [];
  var itens = [];
  cols.forEach(function(col, ci) {
    var key = ri + '_' + ci;
    var cel = (temasData.celulas || {})[key] || {};
    var pipId = cel.pipeline_id || col.pipeline_id || '';
    var pipNome = '';
    if (pipId) {
      var p = pipelines.find(function(x){ return x.id === pipId; });
      pipNome = p ? p.nome : pipId;
    }
    var status = cel.roteiro ? '<span style="color:var(--accent)">tem roteiro (pula)</span>' : (cel.tema ? '<span style="color:var(--warn)">tema ok</span>' : '<span style="color:var(--text-sec)">vazio</span>');
    itens.push('<span style="margin-right:16px"><strong>' + col.nome + '</strong>: ' + status + (pipNome ? ' (' + pipNome + ')' : ' <span style="color:var(--danger)">sem pipeline</span>') + '</span>');
  });
  preview.innerHTML = itens.join('');
}

var _loteRoteiroCancelled = false;

function cancelarLoteRoteiros() {
  _loteRoteiroCancelled = true;
  var btn = document.getElementById('btn-lote-roteiros-cancel');
  btn.textContent = 'Cancelando...';
  btn.style.opacity = '0.6';
  btn.disabled = true;
  var log = document.getElementById('lote-log');
  log.innerHTML += '<div style="color:var(--danger);font-weight:600;padding:4px 0;border-top:1px solid var(--danger)">⛔ CANCELAMENTO SOLICITADO — finalizando job atual...</div>';
  log.scrollTop = 99999;
  toast('Cancelando após job atual terminar...', 'error');
}

async function gerarLoteRoteiros() {
  _loteRoteiroCancelled = false;
  document.getElementById('btn-lote-roteiros').style.display = 'none';
  document.getElementById('btn-lote-roteiros-cancel').style.display = 'inline-flex';
  var ri = parseInt(document.getElementById('lote-data-select').value);
  if (isNaN(ri)) { toast('Selecione uma data', 'error'); return; }
  var row = temasData.linhas[ri];
  var cols = temasData.colunas || [];

  // Coletar jobs
  var jobs = [];
  cols.forEach(function(col, ci) {
    var key = ri + '_' + ci;
    var cel = (temasData.celulas || {})[key] || {};
    var pipId = cel.pipeline_id || col.pipeline_id || '';
    if (!pipId || !cel.tema) return;
    if (cel.roteiro) return; // pular quem já tem roteiro
    jobs.push({ ri: ri, ci: ci, key: key, col: col, cel: cel, pipId: pipId });
  });

  if (!jobs.length) { toast('Nenhuma célula com tema + pipeline nessa data', 'error'); return; }

  document.getElementById('lote-status').style.display = 'block';
  var log = document.getElementById('lote-log');
  log.innerHTML = '<div style="font-weight:600">Lote: ' + row.data + ' | ' + jobs.length + ' roteiros</div>';
  var startTime = Date.now();

  for (var i = 0; i < jobs.length; i++) {
    var job = jobs[i];
    if (_loteRoteiroCancelled) {
      var restantes = jobs.length - i;
      log.innerHTML += '<div style="color:var(--danger);font-weight:600;padding:4px 0">⛔ CANCELADO | ' + i + '/' + jobs.length + ' concluídos | ' + restantes + ' pulados</div>';
      log.scrollTop = 99999;
      break;
    }
    document.getElementById('lote-count').textContent = (i+1) + '/' + jobs.length;
    document.getElementById('lote-fill').style.width = (i / jobs.length * 100) + '%';
    log.innerHTML += '<div>' + job.col.nome + ': Iniciando pipeline...</div>';
    log.scrollTop = 99999;

    try {
      // Esperar pipeline anterior terminar (evitar 409)
      var waitCount = 0;
      while (waitCount < 30) {
        try { var chk = await (await fetch('/api/pipelines/execucao')).json(); if (!chk.ativo) break; } catch(e){}
        await new Promise(function(r){ setTimeout(r, 2000); });
        waitCount++;
      }

      var res = await fetch('/api/pipelines/' + job.pipId + '/executar', {
        method: 'POST', headers:{'Content-Type':'application/json'},
        body: JSON.stringify({
          entrada: job.cel.tema,
          tema: job.cel.tema,
          titulo: job.cel.titulo || '',
          thumb: job.cel.thumb || '',
          canal: job.col.nome || '',
          data: row.data || '',
        })
      });
      if (!res.ok) {
        var err = await res.json();
        log.innerHTML += '<div style="color:var(--danger)">' + job.col.nome + ': ERRO - ' + (err.detail||'') + '</div>';
        continue;
      }

      // Poll até completar
      var done = false;
      while (!done) {
        await new Promise(function(r){ setTimeout(r, 2000); });
        try {
          var sr = await fetch('/api/pipelines/execucao');
          var st = await sr.json();
        } catch(e) { continue; }
        if (!st.ativo) {
          done = true;
          if (st.resultado_final) {
            // Salvar roteiro na célula
            if (!temasData.celulas[job.key]) temasData.celulas[job.key] = {};
            temasData.celulas[job.key].roteiro = st.resultado_final;
            temasData.celulas[job.key].synced = false;
            log.innerHTML += '<div style="color:var(--accent)">' + job.col.nome + ': OK (' + st.resultado_final.length + ' chars)</div>';
          } else {
            log.innerHTML += '<div style="color:var(--danger)">' + job.col.nome + ': Sem resultado</div>';
          }
        }
      }
    } catch(e) {
      log.innerHTML += '<div style="color:var(--danger)">' + job.col.nome + ': ' + e.message + '</div>';
    }
    log.scrollTop = 99999;
  }

  await salvarTemasLocal();
  renderTemasGrid();
  document.getElementById('lote-fill').style.width = '100%';
  document.getElementById('lote-count').textContent = jobs.length + '/' + jobs.length;
  var totalTime = ((Date.now() - startTime) / 1000).toFixed(0);
  log.innerHTML += '<div style="font-weight:600;margin-top:4px;border-top:1px solid var(--border);padding-top:4px">Concluído | ' + totalTime + 's total</div>';
  var btnCancel = document.getElementById('btn-lote-roteiros-cancel');
  btnCancel.style.display = 'none';
  btnCancel.textContent = 'Cancelar';
  btnCancel.style.opacity = '1';
  btnCancel.disabled = false;
  document.getElementById('btn-lote-roteiros').style.display = 'inline-flex';
  toast(_loteRoteiroCancelled ? 'Lote cancelado' : 'Lote concluído!', _loteRoteiroCancelled ? 'error' : 'success');
}

// === PRODUÇÃO COMPLETA ===
var _produzirTudoCancelled = false;

function cancelarProduzirTudo() {
  _produzirTudoCancelled = true;
  fetch('/api/producao-completa/cancelar', { method: 'POST' }).catch(function(){});
  fetch('/api/batch/cancel', { method: 'POST' }).catch(function(){});
  var btn = document.getElementById('btn-produzir-tudo-cancel');
  if (btn) { btn.textContent = 'Cancelando...'; btn.style.opacity = '0.6'; btn.disabled = true; }
  toast('Cancelando produção...', 'error');
}

async function produzirDataCompleta() {
  // Delega ao backend Python (orchestrator.py)
  _produzirTudoCancelled = false;
  var ri = parseInt(document.getElementById('lote-data-select').value);
  if (isNaN(ri)) { toast('Selecione uma data', 'error'); return; }
  var row = temasData.linhas[ri];

  if (!confirm('Produzir todos os vídeos para ' + row.data + '?')) return;

  document.getElementById('btn-produzir-tudo').style.display = 'none';
  document.getElementById('btn-produzir-tudo-cancel').style.display = 'inline-flex';

  try {
    var res = await fetch('/api/producao-completa/iniciar', {
      method: 'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ data_idx: ri })
    });
    if (!res.ok) {
      var err = await res.json();
      toast('Erro: ' + (err.detail || ''), 'error');
      document.getElementById('btn-produzir-tudo').style.display = 'inline-flex';
      document.getElementById('btn-produzir-tudo-cancel').style.display = 'none';
      return;
    }
    toast('Produção iniciada! Acompanhe no Monitor.', 'success');
    // Ir para aba Monitor automaticamente
    showPage('monitor');
  } catch(e) {
    toast('Erro: ' + e.message, 'error');
    document.getElementById('btn-produzir-tudo').style.display = 'inline-flex';
    document.getElementById('btn-produzir-tudo-cancel').style.display = 'none';
  }
}

// === CHAT (CLAUDE CLI) ===
var _chatAberto = false;
var _instrCarregadas = false;

var _currentAgent = 'temas';

function trocarAgente() {
  var sel = document.getElementById('chat-agent-select');
  _currentAgent = sel.value;
  // Recarregar instruções do novo agente
  _instrCarregadas = false;
  fetch('/api/chat/instructions?agent=' + _currentAgent).then(function(r){ return r.json(); }).then(function(d){
    document.getElementById('chat-instrucoes').value = d.instrucoes || '';
    _instrCarregadas = true;
  });
  // Limpar mensagens visuais (histórico é compartilhado)
  document.getElementById('chat-messages').innerHTML = '';
  toast('Agente: ' + _currentAgent, 'success');
}

function toggleChat() {
  _chatAberto = !_chatAberto;
  document.getElementById('chat-panel').style.display = _chatAberto ? 'flex' : 'none';
  if (_chatAberto && !_instrCarregadas) {
    fetch('/api/chat/instructions?agent=' + _currentAgent).then(function(r){ return r.json(); }).then(function(d){
      document.getElementById('chat-instrucoes').value = d.instrucoes || '';
      _instrCarregadas = true;
    });
    // Carregar histórico
    fetch('/api/chat/history').then(function(r){ return r.json(); }).then(function(msgs){
      if (msgs && msgs.length) {
        msgs.forEach(function(m){ _addChatMsg(m.role, m.text); });
      }
    });
  }
}

async function limparChatHistorico() {
  if (!confirm('Limpar todo o histórico do chat?')) return;
  await fetch('/api/chat/history', { method: 'DELETE' });
  document.getElementById('chat-messages').innerHTML = '';
  toast('Histórico limpo', 'success');
}

function toggleInstrucoes() {
  var panel = document.getElementById('chat-instrucoes-panel');
  panel.style.display = panel.style.display === 'none' ? 'block' : 'none';
}

async function salvarInstrucoes() {
  var texto = document.getElementById('chat-instrucoes').value;
  var res = await fetch('/api/chat/instructions', {
    method: 'PUT', headers:{'Content-Type':'application/json'},
    body: JSON.stringify({ instrucoes: texto, agent: _currentAgent })
  });
  if (res.ok) { toast('Instruções do agente "' + _currentAgent + '" salvas!', 'success'); toggleInstrucoes(); }
  else toast('Erro ao salvar', 'error');
}

function _addChatMsg(role, text) {
  var div = document.createElement('div');
  var isUser = role === 'user';
  div.style.cssText = 'padding:8px 12px;border-radius:8px;font-size:12px;line-height:1.5;max-width:90%;word-wrap:break-word;'
    + (isUser ? 'background:var(--accent-dim);color:var(--text);align-self:flex-end;' : 'background:var(--bg);color:var(--text);align-self:flex-start;');

  if (!isUser) {
    // Adicionar botão "Usar" para respostas do Claude
    var content = document.createElement('div');
    content.style.whiteSpace = 'pre-wrap';
    content.textContent = text;
    div.appendChild(content);
    var actions = document.createElement('div');
    actions.style.cssText = 'margin-top:6px;display:flex;gap:4px';
    var btnCopy = document.createElement('button');
    btnCopy.className = 'btn btn-secondary btn-sm';
    btnCopy.style.cssText = 'font-size:10px;padding:2px 6px';
    btnCopy.textContent = 'Copiar';
    btnCopy.onclick = function() { navigator.clipboard.writeText(text); toast('Copiado!', 'success'); };
    actions.appendChild(btnCopy);
    var btnUsar = document.createElement('button');
    btnUsar.className = 'btn btn-primary btn-sm';
    btnUsar.style.cssText = 'font-size:10px;padding:2px 6px';
    btnUsar.textContent = 'Usar na célula';
    btnUsar.onclick = function() {
      if (_celulaEditando) {
        document.getElementById('cel-titulo').value = text;
        toast('Inserido no editor da célula!', 'success');
      } else {
        navigator.clipboard.writeText(text);
        toast('Copiado! Abra uma célula para colar.', 'success');
      }
    };
    actions.appendChild(btnUsar);
    div.appendChild(actions);
  } else {
    div.textContent = text;
  }

  document.getElementById('chat-messages').appendChild(div);
  document.getElementById('chat-messages').scrollTop = 99999;
}

async function enviarChat() {
  var input = document.getElementById('chat-input');
  var msg = input.value.trim();
  if (!msg) return;
  input.value = '';

  _addChatMsg('user', msg);

  var btn = document.getElementById('chat-send-btn');
  btn.disabled = true;
  btn.textContent = '...';

  // Indicador de "pensando"
  var thinking = document.createElement('div');
  thinking.style.cssText = 'padding:8px 12px;border-radius:8px;font-size:12px;background:var(--bg);color:var(--text-sec);align-self:flex-start;font-style:italic';
  thinking.textContent = 'Claude está pensando...';
  document.getElementById('chat-messages').appendChild(thinking);

  try {
    var res = await fetch('/api/chat', {
      method: 'POST', headers:{'Content-Type':'application/json'},
      body: JSON.stringify({ prompt: msg, agent: _currentAgent })
    });
    var data = await res.json();
    thinking.remove();
    _addChatMsg('assistant', data.resposta || 'Sem resposta');
  } catch(e) {
    thinking.remove();
    _addChatMsg('assistant', 'Erro: ' + e.message);
  }

  btn.disabled = false;
  btn.textContent = 'Enviar';
}

// === THUMBNAIL ===

var _thumbYtData = null;
var _thumbCards = {};

function _getThumbTextConfig() {
  return {
    font: document.getElementById('thumb-cfg-font').value,
    size: parseInt(document.getElementById('thumb-cfg-size').value) || 72,
    color: document.getElementById('thumb-cfg-color').value,
    outline_color: document.getElementById('thumb-cfg-outline-color').value,
    outline_width: parseInt(document.getElementById('thumb-cfg-outline-w').value) || 4,
    shadow: document.getElementById('thumb-cfg-shadow').checked,
    position: document.getElementById('thumb-cfg-position').value,
  };
}

function thumbCfgChanged() {
  var hex = document.getElementById('thumb-cfg-color').value;
  document.getElementById('thumb-cfg-color-hex').textContent = hex;
}

async function carregarThumbPage() {
  if (!templates.length) {
    var r = await fetch('/api/templates');
    templates = await r.json();
  }
  // Carregar fontes para o select
  try {
    var fr = await fetch('/api/fonts');
    var fontes = await fr.json();
    var sel = document.getElementById('thumb-cfg-font');
    var current = sel.value;
    sel.innerHTML = fontes.map(function(f) {
      return '<option' + (f === current ? ' selected' : '') + '>' + f + '</option>';
    }).join('');
    if (!current || fontes.indexOf(current) < 0) sel.value = 'Arial Black';
  } catch(e) {}

  // Carregar configs salvas do localStorage
  var savedCfg = JSON.parse(localStorage.getItem('thumbTextConfig') || '{}');
  if (savedCfg.font) document.getElementById('thumb-cfg-font').value = savedCfg.font;
  if (savedCfg.size) document.getElementById('thumb-cfg-size').value = savedCfg.size;
  if (savedCfg.color) document.getElementById('thumb-cfg-color').value = savedCfg.color;
  if (savedCfg.outline_color) document.getElementById('thumb-cfg-outline-color').value = savedCfg.outline_color;
  if (savedCfg.outline_width !== undefined) document.getElementById('thumb-cfg-outline-w').value = savedCfg.outline_width;
  if (savedCfg.shadow !== undefined) document.getElementById('thumb-cfg-shadow').checked = savedCfg.shadow;
  if (savedCfg.position) document.getElementById('thumb-cfg-position').value = savedCfg.position;
  thumbCfgChanged();

  renderThumbCards();

  var dateInput = document.getElementById('thumb-batch-data');
  if (!dateInput.value) {
    var hoje = new Date();
    dateInput.value = hoje.toISOString().slice(0, 10);
  }
}

function _saveThumbTextConfig() {
  localStorage.setItem('thumbTextConfig', JSON.stringify(_getThumbTextConfig()));
}

async function renderThumbCards() {
  var grid = document.getElementById('thumb-batch-cards');
  var empty = document.getElementById('thumb-empty');

  if (!templates.length) {
    grid.innerHTML = '';
    empty.style.display = 'block';
    return;
  }
  empty.style.display = 'none';

  var savedCards = JSON.parse(localStorage.getItem('thumbCardsData') || '{}');

  grid.innerHTML = templates.map(function(t) {
    var sc = savedCards[t.id] || {};
    var texto = (sc.texto || '').replace(/</g, '&lt;').replace(/"/g, '&quot;');
    var imagem = (sc.imagem || '').replace(/"/g, '&quot;');
    var modo = sc.modo || 'existente';
    var prompt = (sc.prompt || '').replace(/</g, '&lt;').replace(/"/g, '&quot;');

    return '<div class="card" style="cursor:default">'
      + '<div class="card-header">'
      + '<span class="card-tag">' + (t.tag || t.id).toUpperCase() + '</span>'
      + '<span class="card-title" style="font-size:13px">' + (t.nome || '') + '</span>'
      + '</div>'
      + '<div class="form-group" style="margin:0 0 8px">'
      + '<label style="font-size:10px">Texto da Thumb</label>'
      + '<textarea class="tb-texto" data-tid="' + t.id + '" rows="2" placeholder="Texto da thumbnail..." style="font-size:11px;padding:4px 6px" oninput="_saveThumbCardData()">' + texto + '</textarea>'
      + '</div>'
      + '<div style="display:flex;gap:8px;align-items:center;margin-bottom:8px">'
      + '<label style="font-size:10px;color:var(--text-sec)"><input type="radio" name="tb-modo-' + t.id + '" value="existente" class="tb-modo" data-tid="' + t.id + '" ' + (modo === 'existente' ? 'checked' : '') + ' onchange="thumbModoChanged(\\'' + t.id + '\\')"> Imagem Existente</label>'
      + '<label style="font-size:10px;color:var(--text-sec)"><input type="radio" name="tb-modo-' + t.id + '" value="prompt" class="tb-modo" data-tid="' + t.id + '" ' + (modo === 'prompt' ? 'checked' : '') + ' onchange="thumbModoChanged(\\'' + t.id + '\\')"> Gerar por Prompt</label>'
      + '</div>'
      + '<div class="tb-modo-existente" data-tid="' + t.id + '" style="' + (modo !== 'existente' ? 'display:none' : '') + '">'
      + '<div class="form-group" style="margin:0 0 8px">'
      + '<label style="font-size:10px">Imagem de Fundo</label>'
      + '<div class="input-with-btn">'
      + '<input type="text" class="tb-imagem" data-tid="' + t.id + '" value="' + imagem + '" placeholder="Caminho da imagem..." style="font-size:11px;padding:4px" onchange="_saveThumbCardData()">'
      + '<button class="btn btn-secondary btn-sm" style="font-size:10px;padding:2px 6px" onclick="abrirBrowser(\\'tb-imagem-' + t.id + '\\',\\'file\\')">...</button>'
      + '</div>'
      + '</div>'
      + '</div>'
      + '<div class="tb-modo-prompt" data-tid="' + t.id + '" style="' + (modo !== 'prompt' ? 'display:none' : '') + '">'
      + '<div class="form-group" style="margin:0 0 8px">'
      + '<label style="font-size:10px">Prompt para Gerar Imagem <span style="color:var(--warn)">(em breve)</span></label>'
      + '<textarea class="tb-prompt" data-tid="' + t.id + '" rows="2" placeholder="Descreva a imagem de fundo desejada..." style="font-size:11px;padding:4px 6px" oninput="_saveThumbCardData()">' + prompt + '</textarea>'
      + '</div>'
      + '</div>'
      + '<div class="tb-preview" data-tid="' + t.id + '" style="background:var(--bg);border:1px solid var(--border);border-radius:6px;aspect-ratio:16/9;display:flex;align-items:center;justify-content:center;overflow:hidden;margin-bottom:8px;position:relative">'
      + '<span style="font-size:11px;color:var(--text-sec)">Preview aparecera aqui</span>'
      + '</div>'
      + '<div style="display:flex;gap:6px;align-items:center">'
      + '<button class="btn btn-secondary btn-sm" onclick="previewThumb(\\'' + t.id + '\\')" style="font-size:10px">Preview</button>'
      + '<button class="btn btn-primary btn-sm" onclick="gerarThumb(\\'' + t.id + '\\')" style="font-size:10px">Gerar</button>'
      + '<span class="tb-status badge badge-waiting" data-tid="' + t.id + '" style="font-size:9px;margin-left:auto">--</span>'
      + '</div></div>';
  }).join('');

  // Restaurar IDs para o file browser funcionar
  templates.forEach(function(t) {
    var el = document.querySelector('.tb-imagem[data-tid="' + t.id + '"]');
    if (el) el.id = 'tb-imagem-' + t.id;
  });
}

function thumbModoChanged(tid) {
  var radios = document.querySelectorAll('.tb-modo[data-tid="' + tid + '"]');
  var modo = 'existente';
  radios.forEach(function(r) { if (r.checked) modo = r.value; });
  var existente = document.querySelector('.tb-modo-existente[data-tid="' + tid + '"]');
  var promptDiv = document.querySelector('.tb-modo-prompt[data-tid="' + tid + '"]');
  if (existente) existente.style.display = modo === 'existente' ? '' : 'none';
  if (promptDiv) promptDiv.style.display = modo === 'prompt' ? '' : 'none';
  _saveThumbCardData();
}

function _saveThumbCardData() {
  var data = {};
  templates.forEach(function(t) {
    var texto = document.querySelector('.tb-texto[data-tid="' + t.id + '"]');
    var imagem = document.querySelector('.tb-imagem[data-tid="' + t.id + '"]');
    var prompt = document.querySelector('.tb-prompt[data-tid="' + t.id + '"]');
    var radios = document.querySelectorAll('.tb-modo[data-tid="' + t.id + '"]');
    var modo = 'existente';
    radios.forEach(function(r) { if (r.checked) modo = r.value; });
    data[t.id] = {
      texto: texto ? texto.value : '',
      imagem: imagem ? imagem.value : '',
      prompt: prompt ? prompt.value : '',
      modo: modo,
    };
  });
  localStorage.setItem('thumbCardsData', JSON.stringify(data));
  _saveThumbTextConfig();
}

async function puxarTextosThumb() {
  var dateVal = document.getElementById('thumb-batch-data').value;
  if (!dateVal) { toast('Selecione a data primeiro', 'error'); return; }

  if (!temasData || !temasData.linhas || !temasData.linhas.length) {
    try { var r = await fetch('/api/temas'); var raw = await r.json(); if (raw && raw.colunas) temasData = raw; } catch(e) {}
  }

  var dateParts = dateVal.split('-');
  var dataFormatada = dateParts[2] + '/' + dateParts[1] + '/' + dateParts[0];
  var ri = -1;
  (temasData.linhas || []).forEach(function(row, i) {
    if (row.data === dataFormatada) ri = i;
  });
  if (ri < 0) { toast('Data ' + dataFormatada + ' nao encontrada no grid de Temas', 'error'); return; }

  var count = 0;
  templates.forEach(function(t) {
    var textarea = document.querySelector('.tb-texto[data-tid="' + t.id + '"]');
    if (!textarea) return;
    (temasData.colunas || []).forEach(function(col, ci) {
      if (col.template_id === t.id) {
        var key = ri + '_' + ci;
        var cel = (temasData.celulas || {})[key] || {};
        if (cel.thumb) {
          textarea.value = cel.thumb;
          count++;
        }
      }
    });
  });
  _saveThumbCardData();
  toast(count + ' textos de thumb puxados do grid de Temas', count > 0 ? 'success' : 'error');
}

async function previewThumb(tid) {
  var texto = document.querySelector('.tb-texto[data-tid="' + tid + '"]');
  var imagem = document.querySelector('.tb-imagem[data-tid="' + tid + '"]');
  if (!texto || !texto.value.trim()) { toast('Digite o texto da thumbnail', 'error'); return; }
  if (!imagem || !imagem.value.trim()) { toast('Selecione a imagem de fundo', 'error'); return; }

  var cfg = _getThumbTextConfig();
  var params = new URLSearchParams({
    imagem: imagem.value,
    texto: texto.value,
    font: cfg.font,
    size: cfg.size,
    color: cfg.color,
    outline_color: cfg.outline_color,
    outline_width: cfg.outline_width,
    shadow: cfg.shadow,
    position: cfg.position,
  });

  var preview = document.querySelector('.tb-preview[data-tid="' + tid + '"]');
  preview.innerHTML = '<span style="font-size:11px;color:var(--text-sec)">Gerando preview...</span>';

  try {
    var res = await fetch('/api/thumbnail/preview?' + params.toString());
    var data = await res.json();
    if (data.base64) {
      preview.innerHTML = '<img src="data:image/jpeg;base64,' + data.base64 + '" style="width:100%;height:100%;object-fit:contain">';
    } else {
      preview.innerHTML = '<span style="color:var(--danger);font-size:11px">Erro: ' + (data.detail || 'Falha') + '</span>';
    }
  } catch(e) {
    preview.innerHTML = '<span style="color:var(--danger);font-size:11px">Erro: ' + e.message + '</span>';
  }
}

async function gerarThumb(tid) {
  var t = templates.find(function(x) { return x.id === tid; });
  if (!t) return;

  var texto = document.querySelector('.tb-texto[data-tid="' + tid + '"]');
  var imagem = document.querySelector('.tb-imagem[data-tid="' + tid + '"]');
  var badge = document.querySelector('.tb-status[data-tid="' + tid + '"]');
  if (!texto || !texto.value.trim()) { toast('Digite o texto da thumbnail', 'error'); return; }
  if (!imagem || !imagem.value.trim()) { toast('Selecione a imagem de fundo', 'error'); return; }

  var dateVal = document.getElementById('thumb-batch-data').value || new Date().toISOString().slice(0, 10);
  var dateParts = dateVal.split('-');
  var dataStr = dateParts[0] + dateParts[1] + dateParts[2];
  var dataPasta = dateVal;
  var tag = t.tag || t.id;
  var pasta = t.pasta_saida || 'temp';
  var output = pasta + '/' + dataPasta + '/' + tag + '_' + dataStr + '_thumb.jpg';

  if (badge) { badge.textContent = 'Gerando...'; badge.className = 'tb-status badge badge-transcribing'; }

  try {
    var res = await fetch('/api/thumbnail/generate', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({
        imagem: imagem.value,
        texto: texto.value,
        output: output,
        config: _getThumbTextConfig(),
      })
    });
    var data = await res.json();
    if (data.ok) {
      if (badge) { badge.textContent = 'Salvo'; badge.className = 'tb-status badge badge-done'; }
      toast(tag + ': thumb salva em ' + data.output, 'success');
      // Atualizar preview
      previewThumb(tid);
    } else {
      if (badge) { badge.textContent = 'Erro'; badge.className = 'tb-status badge badge-error'; }
      toast(tag + ': ' + (data.detail || 'Erro'), 'error');
    }
  } catch(e) {
    if (badge) { badge.textContent = 'Erro'; badge.className = 'tb-status badge badge-error'; }
    toast(tag + ': ' + e.message, 'error');
  }
}

async function gerarTodasThumbs() {
  var jobs = [];
  templates.forEach(function(t) {
    var texto = document.querySelector('.tb-texto[data-tid="' + t.id + '"]');
    var imagem = document.querySelector('.tb-imagem[data-tid="' + t.id + '"]');
    var radios = document.querySelectorAll('.tb-modo[data-tid="' + t.id + '"]');
    var modo = 'existente';
    radios.forEach(function(r) { if (r.checked) modo = r.value; });
    if (modo !== 'existente') return;
    if (!texto || !texto.value.trim()) return;
    if (!imagem || !imagem.value.trim()) return;
    jobs.push(t.id);
  });

  if (!jobs.length) { toast('Nenhum template pronto para gerar (texto + imagem necessarios)', 'error'); return; }

  for (var i = 0; i < jobs.length; i++) {
    await gerarThumb(jobs[i]);
    if (i < jobs.length - 1) await new Promise(function(r) { setTimeout(r, 300); });
  }
  toast('Todas as thumbnails geradas (' + jobs.length + ')', 'success');
}

async function extrairThumbYT() {
  var url = document.getElementById('thumb-yt-url').value.trim();
  if (!url) { toast('Cole a URL do YouTube', 'error'); return; }

  try {
    var res = await fetch('/api/thumbnail/extract-youtube', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: url })
    });
    var data = await res.json();
    if (data.thumbnail_url) {
      _thumbYtData = data;
      document.getElementById('thumb-yt-result').style.display = 'block';
      document.getElementById('thumb-yt-img').src = data.thumbnail_url;
      document.getElementById('thumb-yt-title').textContent = data.title || data.video_id;
      document.getElementById('thumb-yt-author').textContent = data.author || '';
      toast('Thumbnail extraida', 'success');
    } else {
      toast('Erro: ' + (data.detail || 'Falha'), 'error');
    }
  } catch(e) {
    toast('Erro: ' + e.message, 'error');
  }
}

function copiarUrlThumbYT(qual) {
  if (!_thumbYtData) return;
  var url = qual === 'maxres' ? _thumbYtData.thumbnail_url : _thumbYtData.thumbnail_hq;
  navigator.clipboard.writeText(url);
  toast('URL copiada', 'success');
}

async function baixarThumbYT() {
  if (!_thumbYtData) return;
  var output = prompt('Salvar como (caminho completo):', 'temp/' + _thumbYtData.video_id + '_thumb.jpg');
  if (!output) return;

  try {
    var res = await fetch('/api/thumbnail/extract-youtube', {
      method: 'POST',
      headers: { 'Content-Type': 'application/json' },
      body: JSON.stringify({ url: document.getElementById('thumb-yt-url').value, baixar: true, output: output })
    });
    var data = await res.json();
    if (data.local_path) {
      toast('Thumbnail salva em: ' + data.local_path, 'success');
    } else if (data.download_error) {
      toast('Erro ao baixar: ' + data.download_error, 'error');
    }
  } catch(e) {
    toast('Erro: ' + e.message, 'error');
  }
}

// === INIT ===
window.onerror = function(msg, url, line, col, error) {
  console.error('JS Error:', msg, 'at line', line, ':', col);
  toast('JS Error: ' + msg + ' (line ' + line + ')', 'error');
  return false;
};

window.addEventListener('unhandledrejection', function(event) {
  console.error('Unhandled promise rejection:', event.reason);
  // Não mostrar toast para "Failed to fetch" — é retry silencioso do polling
  var msg = event.reason?.message || String(event.reason);
  if (msg.indexOf('fetch') === -1) {
    toast('Erro: ' + msg, 'error');
  }
});

// Lazy load: só carregar temas (leve) no init. Pipelines carregam sob demanda.
carregarTemas().then(function(){ atualizarLoteDataSelect(); });
document.getElementById('chat-toggle-btn').style.display = 'block';

// Sempre iniciar polling do Monitor em background
startMonitorPolling();

// === MONITOR ===
var _monitorInterval = null;
var _monitorTimerInterval = null;
var _monitorInicio = null;
var _monitorAtivo = false;

function startMonitorPolling() {
  stopMonitorPolling();
  refreshMonitor();
  _monitorInterval = setInterval(refreshMonitor, 3000);
  _monitorTimerInterval = setInterval(_updateMonitorTimer, 1000);
}

function stopMonitorPolling() {
  if (_monitorInterval) { clearInterval(_monitorInterval); _monitorInterval = null; }
  if (_monitorTimerInterval) { clearInterval(_monitorTimerInterval); _monitorTimerInterval = null; }
}

function _updateMonitorTimer() {
  if (!_monitorInicio) return;
  var elapsed = _monitorAtivo ? (Date.now()/1000 - _monitorInicio) : 0;
  var el = document.getElementById('mon-timer-global');
  if (el) {
    var hh = String(Math.floor(elapsed / 3600)).padStart(2, '0');
    var mm = String(Math.floor((elapsed % 3600) / 60)).padStart(2, '0');
    var ss = String(Math.floor(elapsed % 60)).padStart(2, '0');
    el.textContent = hh + ':' + mm + ':' + ss;
  }
}

function _monEtapaBadge(etapa) {
  if (etapa === 'concluido') return '<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;background:#166534;color:#4ade80">concluido</span>';
  if (etapa === 'erro') return '<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;background:#7f1d1d;color:#f87171">erro</span>';
  if (etapa === 'roteiro') return '<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;background:#854d0e;color:#facc15">roteiro</span>';
  if (etapa === 'narracao') return '<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;background:#854d0e;color:#facc15">narracao</span>';
  if (etapa === 'video') return '<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;background:#854d0e;color:#facc15">video</span>';
  if (etapa === 'pulado') return '<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;background:#1e3a5f;color:#60a5fa">pulado</span>';
  return '<span style="display:inline-block;padding:2px 8px;border-radius:4px;font-size:10px;font-weight:600;background:#333;color:#888">aguardando</span>';
}

function _fmtTempo(seg) {
  if (!seg || seg <= 0) return '--';
  if (seg < 60) return Math.round(seg) + 's';
  if (seg < 3600) return Math.floor(seg / 60) + 'm ' + Math.round(seg % 60) + 's';
  return Math.floor(seg / 3600) + 'h ' + Math.floor((seg % 3600) / 60) + 'm';
}

function _monCanalTimer(c) {
  if (!c.inicio) return '';
  var end = c.fim || (Date.now()/1000);
  var seg = end - c.inicio;
  return _fmtTempo(seg);
}

async function refreshMonitor() {
  try {
    var res = await fetch('/api/monitor');
    var d = await res.json();

    // Uptime
    document.getElementById('mon-uptime').textContent = 'Uptime: ' + d.uptime;

    // Production log data
    var pc = d.producao_completa || {};
    var canais = pc.canais || [];
    var isAtivo = !!pc.ativo;
    _monitorAtivo = isAtivo;
    _monitorInicio = pc.inicio || null;

    // Data ref
    var dataRefEl = document.getElementById('mon-data-ref');
    if (pc.data_ref) {
      dataRefEl.textContent = pc.data_ref + (isAtivo ? ' (em andamento)' : (pc.cancelado ? ' (cancelado)' : ' (finalizado)'));
      dataRefEl.style.color = isAtivo ? 'var(--warn)' : (pc.cancelado ? 'var(--danger)' : 'var(--accent)');
    } else {
      dataRefEl.textContent = '';
    }

    // Timer for non-active (show final time)
    if (!isAtivo && pc.tempo_decorrido) {
      var td = pc.tempo_decorrido;
      var hh = String(Math.floor(td / 3600)).padStart(2, '0');
      var mm = String(Math.floor((td % 3600) / 60)).padStart(2, '0');
      var ss = String(Math.floor(td % 60)).padStart(2, '0');
      document.getElementById('mon-timer-global').textContent = hh + ':' + mm + ':' + ss;
    }

    // Progress overview
    var total = canais.length;
    var concluidos = 0, erros = 0, pulados = 0, processando = 0, aguardando = 0;
    for (var ci = 0; ci < canais.length; ci++) {
      var et = canais[ci].etapa;
      if (et === 'concluido') concluidos++;
      else if (et === 'erro') erros++;
      else if (et === 'pulado') pulados++;
      else if (et === 'aguardando') aguardando++;
      else processando++;
    }
    var pct = total > 0 ? Math.round(concluidos / total * 100) : 0;
    document.getElementById('mon-progress-fill').style.width = pct + '%';
    var statusLabel = document.getElementById('mon-status-label');
    if (total === 0) {
      statusLabel.textContent = 'Nenhuma producao registrada';
      statusLabel.style.color = 'var(--text-sec)';
    } else if (isAtivo) {
      statusLabel.textContent = 'Producao em andamento';
      statusLabel.style.color = 'var(--warn)';
    } else if (pc.cancelado) {
      statusLabel.textContent = 'Producao cancelada';
      statusLabel.style.color = 'var(--danger)';
    } else {
      statusLabel.textContent = 'Producao finalizada';
      statusLabel.style.color = 'var(--accent)';
    }
    document.getElementById('mon-count-label').textContent = total > 0 ? concluidos + '/' + total + ' canais concluidos' : '';
    document.getElementById('mon-sum-ok').textContent = concluidos + ' concluidos';
    document.getElementById('mon-sum-erros').textContent = erros + ' erros';
    document.getElementById('mon-sum-pulados').textContent = pulados + ' pulados';
    document.getElementById('mon-sum-processando').textContent = processando + ' processando';
    document.getElementById('mon-sum-aguardando').textContent = aguardando + ' aguardando';

    // Canal cards
    var grid = document.getElementById('mon-canal-grid');
    var gHtml = '';
    for (var ci2 = 0; ci2 < canais.length; ci2++) {
      var c = canais[ci2];
      var borderColor = c.etapa === 'concluido' ? '#4ade80' : (c.etapa === 'erro' ? '#f87171' : (c.etapa === 'aguardando' ? '#444' : (c.etapa === 'pulado' ? '#60a5fa' : '#facc15')));
      gHtml += '<div style="background:var(--panel);border:1px solid ' + borderColor + ';border-radius:8px;padding:14px;border-left:4px solid ' + borderColor + '">';
      gHtml += '<div style="display:flex;justify-content:space-between;align-items:center;margin-bottom:8px">';
      gHtml += '<span style="font-weight:600;font-size:13px;color:var(--text)">' + (c.tag || 'Canal ' + (ci2+1)) + '</span>';
      gHtml += _monEtapaBadge(c.etapa);
      gHtml += '</div>';
      if (c.etapa_detalhe) {
        gHtml += '<div style="font-size:11px;color:var(--text-sec);margin-bottom:6px">' + c.etapa_detalhe + '</div>';
      }
      // Progress bar per canal
      gHtml += '<div class="progress-bar" style="height:5px;margin-bottom:6px"><div class="progress-fill" style="width:' + (c.progresso || 0) + '%;transition:width 0.3s"></div></div>';
      // Info row
      gHtml += '<div style="display:flex;gap:12px;font-size:10px;color:var(--text-sec);flex-wrap:wrap">';
      if (c.roteiro_chars) gHtml += '<span>Roteiro: ' + c.roteiro_chars + ' chars</span>';
      if (c.narracao_path) gHtml += '<span>MP3: ' + c.narracao_path.split('/').pop().split('\\\\').pop() + '</span>';
      if (c.video_path) gHtml += '<span>Video: ' + c.video_path.split('/').pop().split('\\\\').pop() + '</span>';
      var timer = _monCanalTimer(c);
      if (timer) gHtml += '<span style="margin-left:auto;font-family:monospace">' + timer + '</span>';
      gHtml += '</div>';
      if (c.erro) {
        gHtml += '<div style="font-size:10px;color:#f87171;margin-top:4px">' + c.erro + '</div>';
      }
      gHtml += '</div>';
    }
    if (canais.length === 0) {
      gHtml = '<div style="color:var(--text-sec);font-size:12px;text-align:center;padding:24px;grid-column:1/-1">Nenhum canal na producao atual. Inicie uma producao via "Produzir Tudo" na aba Temas.</div>';
    }
    grid.innerHTML = gHtml;

    // Log panel
    var logPanel = document.getElementById('mon-log-panel');
    var logs = pc.log || [];
    var lHtml = '';
    for (var li = 0; li < logs.length; li++) {
      var logEntry = logs[li];
      var ts = logEntry.ts ? logEntry.ts.substring(11, 19) : '';
      lHtml += '<div style="padding:1px 0"><span style="color:var(--accent)">' + ts + '</span> ' + (logEntry.msg || '') + '</div>';
    }
    logPanel.innerHTML = lHtml || '<div style="color:#555">Sem entradas de log</div>';
    // Auto-scroll to bottom
    logPanel.scrollTop = logPanel.scrollHeight;

    // General status cards
    var bAtivo = d.batch && d.batch.ativo;
    var bBadge = document.getElementById('mon-batch-badge');
    bBadge.className = 'badge ' + (bAtivo ? 'badge-encoding' : 'badge-waiting');
    bBadge.textContent = bAtivo ? 'Ativo' : 'Inativo';

    var nAtivo = d.narracao && d.narracao.ativo;
    var nBadge = document.getElementById('mon-narr-badge');
    nBadge.className = 'badge ' + (nAtivo ? 'badge-encoding' : 'badge-waiting');
    nBadge.textContent = nAtivo ? 'Ativo' : 'Inativo';

    var pAtivo = d.pipeline && d.pipeline.ativo;
    var pBadge = document.getElementById('mon-pipe-badge');
    pBadge.className = 'badge ' + (pAtivo ? 'badge-encoding' : 'badge-waiting');
    pBadge.textContent = pAtivo ? 'Ativo' : 'Inativo';

    var credEl = document.getElementById('mon-credits');
    credEl.textContent = d.creditos != null ? d.creditos : '--';

    // Stats
    var st = d.estatisticas || {};
    document.getElementById('mon-stat-hoje').textContent = st.videos_hoje || 0;
    document.getElementById('mon-stat-semana').textContent = st.videos_semana || 0;
    document.getElementById('mon-stat-total').textContent = st.videos_total || 0;
    document.getElementById('mon-stat-tempo').textContent = _fmtTempo(st.tempo_medio);

    // Disco
    var discoEl = document.getElementById('mon-disco');
    var dHtml2 = '';
    if (d.disco) {
      var keys = Object.keys(d.disco);
      for (var di = 0; di < keys.length; di++) {
        var dk = keys[di];
        var dv = d.disco[dk];
        dHtml2 += '<div style="background:var(--bg);border:1px solid var(--border);border-radius:6px;padding:8px 12px;min-width:100px">';
        dHtml2 += '<div style="font-size:10px;color:var(--text-sec);margin-bottom:2px">' + dk + '</div>';
        dHtml2 += '<div style="font-size:13px;font-weight:600;color:var(--text)">' + (dv.humano || '?') + '</div>';
        dHtml2 += '</div>';
      }
    }
    discoEl.innerHTML = dHtml2;

  } catch (e) {
    // Silently ignore fetch errors during polling
  }
}

</script>
</body>
</html>"""


@app.get("/", response_class=HTMLResponse)
@app.get("/dashboard", response_class=HTMLResponse)
def dashboard():
    return DASHBOARD_HTML


if __name__ == "__main__":
    import uvicorn
    uvicorn.run(app, host="127.0.0.1", port=8500)
