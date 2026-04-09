"""
Motor de geração de roteiros via APIs de LLM.
Suporta Claude, GPT e Gemini. Sistema de credenciais múltiplas com listagem automática de modelos.
"""

import json
import time
import traceback
from pathlib import Path
from datetime import datetime

import httpx

BASE_DIR = Path(__file__).parent
PIPELINES_FILE = BASE_DIR / "pipelines.json"
CONFIG_FILE = BASE_DIR / "config.json"
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TEMAS_FILE = BASE_DIR / "temas.json"
SCRIPTS_DIR = BASE_DIR / "scripts"
SCRIPTS_DIR.mkdir(exist_ok=True)


# === PERSISTÊNCIA ===

def _carregar_json(path: Path, default=None):
    if path.exists():
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    return default if default is not None else {}


def _salvar_json(path: Path, data):
    with open(path, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def carregar_config() -> dict:
    return _carregar_json(CONFIG_FILE, {})

def salvar_config(config: dict):
    _salvar_json(CONFIG_FILE, config)

def carregar_pipelines() -> dict:
    return _carregar_json(PIPELINES_FILE, {})

def salvar_pipelines(pipelines: dict):
    _salvar_json(PIPELINES_FILE, pipelines)

def carregar_credenciais() -> list:
    return _carregar_json(CREDENTIALS_FILE, [])

def salvar_credenciais(creds: list):
    _salvar_json(CREDENTIALS_FILE, creds)

def carregar_temas() -> list:
    return _carregar_json(TEMAS_FILE, [])

def salvar_temas(temas: list):
    _salvar_json(TEMAS_FILE, temas)


# === CREDENCIAIS ===

def obter_credencial(cred_id: str) -> dict:
    """Busca credencial pelo ID."""
    for c in carregar_credenciais():
        if c.get("id") == cred_id:
            return c
    return {}


def listar_modelos(provedor: str, api_key: str) -> list:
    """Consulta a API do provedor e retorna lista de modelos disponíveis."""
    try:
        if provedor == "claude":
            resp = httpx.get(
                "https://api.anthropic.com/v1/models",
                headers={
                    "x-api-key": api_key,
                    "anthropic-version": "2023-06-01",
                },
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            return sorted([m["id"] for m in data.get("data", [])], reverse=True)

        elif provedor == "gpt":
            resp = httpx.get(
                "https://api.openai.com/v1/models",
                headers={"Authorization": f"Bearer {api_key}"},
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            # Filtrar só modelos de chat úteis
            modelos = [m["id"] for m in data.get("data", [])]
            prefixos = ("gpt-4", "gpt-3.5", "o1", "o3", "o4")
            filtrados = [m for m in modelos if any(m.startswith(p) for p in prefixos)]
            return sorted(filtrados, reverse=True) if filtrados else sorted(modelos[:30], reverse=True)

        elif provedor == "gemini":
            resp = httpx.get(
                f"https://generativelanguage.googleapis.com/v1beta/models?key={api_key}",
                timeout=15.0,
            )
            resp.raise_for_status()
            data = resp.json()
            modelos = []
            for m in data.get("models", []):
                nome = m.get("name", "").replace("models/", "")
                if "generateContent" in str(m.get("supportedGenerationMethods", [])):
                    modelos.append(nome)
            return sorted(modelos, reverse=True)

    except Exception as e:
        return [f"erro: {e}"]

    return []


def testar_credencial(provedor: str, api_key: str) -> dict:
    """Testa uma credencial e retorna status + modelos."""
    modelos = listar_modelos(provedor, api_key)
    if modelos and not modelos[0].startswith("erro:"):
        return {"ok": True, "modelos": modelos}
    erro = modelos[0] if modelos else "Nenhum modelo retornado"
    return {"ok": False, "erro": erro, "modelos": []}


# === CHAMADAS AOS MODELOS ===

def _chamar_claude(system_msg: str, user_msg: str, api_key: str, model: str) -> str:
    resp = httpx.post(
        "https://api.anthropic.com/v1/messages",
        headers={
            "x-api-key": api_key,
            "anthropic-version": "2023-06-01",
            "content-type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 32000,
            "system": system_msg,
            "messages": [{"role": "user", "content": user_msg}],
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"]


def _chamar_gpt(system_msg: str, user_msg: str, api_key: str, model: str) -> str:
    resp = httpx.post(
        "https://api.openai.com/v1/chat/completions",
        headers={
            "Authorization": f"Bearer {api_key}",
            "Content-Type": "application/json",
        },
        json={
            "model": model,
            "max_tokens": 32000,
            "messages": [
                {"role": "system", "content": system_msg},
                {"role": "user", "content": user_msg},
            ],
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _chamar_gemini(system_msg: str, user_msg: str, api_key: str, model: str) -> str:
    resp = httpx.post(
        f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
        headers={"Content-Type": "application/json"},
        json={
            "system_instruction": {"parts": [{"text": system_msg}]},
            "contents": [{"parts": [{"text": user_msg}]}],
            "generationConfig": {"maxOutputTokens": 32000},
        },
        timeout=120.0,
    )
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


CHAMADAS = {
    "claude": _chamar_claude,
    "gpt": _chamar_gpt,
    "gemini": _chamar_gemini,
}


def _substituir_variaveis(texto: str, variaveis: dict) -> str:
    import re
    for chave, valor in variaveis.items():
        # Substituir {{chave}} com tolerância a espaços
        pattern = r'\{\{\s*' + re.escape(chave) + r'\s*\}\}'
        texto = re.sub(pattern, str(valor), texto)
    return texto


# === ESTADO DA EXECUÇÃO ===

estado_execucao = {
    "ativo": False,
    "pipeline_id": None,
    "etapas": [],
    "etapa_atual": -1,
    "inicio": None,
    "cancelado": False,
    "resultado_final": "",
}


def executar_pipeline(pipeline_id: str, entrada: str, contexto_extra: dict = None):
    """Executa pipeline etapa por etapa. Roda em thread separada."""
    global estado_execucao

    pipelines = carregar_pipelines()
    pipeline = pipelines.get(pipeline_id)
    if not pipeline:
        raise ValueError(f"Pipeline não encontrada: {pipeline_id}")

    etapas = pipeline.get("etapas", [])

    estado_execucao = {
        "ativo": True,
        "pipeline_id": pipeline_id,
        "etapas": [
            {
                "nome": e.get("nome", f"Etapa {i+1}"),
                "modelo": e.get("modelo", ""),
                "credencial": e.get("credencial", ""),
                "status": "aguardando",
                "resultado": "",
                "erro": None,
                "inicio": None,
                "fim": None,
            }
            for i, e in enumerate(etapas)
        ],
        "etapa_atual": -1,
        "inicio": time.time(),
        "cancelado": False,
        "resultado_final": "",
    }

    variaveis = {
        "entrada": entrada,
        "tema": entrada,  # alias
        "saida_anterior": "",
        "roteiro_atual": "",
    }
    # Variáveis extras do contexto (tema, titulo, thumb, canal, data)
    if contexto_extra:
        variaveis.update(contexto_extra)

    try:
        for i, etapa_config in enumerate(etapas):
            if estado_execucao["cancelado"]:
                for j in range(i, len(etapas)):
                    estado_execucao["etapas"][j]["status"] = "cancelado"
                break

            estado_execucao["etapa_atual"] = i
            estado_execucao["etapas"][i]["status"] = "processando"
            estado_execucao["etapas"][i]["inicio"] = time.time()

            tipo = etapa_config.get("tipo", "llm")

            try:
                if tipo == "texto":
                    # Texto fixo: substitui variáveis e usa como resultado
                    raw_prompt = etapa_config.get("prompt", "")
                    # Debug: log variáveis disponíveis
                    print(f"[TEXTO FIXO] Etapa {i+1}: vars disponíveis = {list(variaveis.keys())}")
                    print(f"[TEXTO FIXO] Prompt contém saida_etapa_3: {'saida_etapa_3' in raw_prompt}")
                    resultado = _substituir_variaveis(raw_prompt, variaveis)

                elif tipo == "code":
                    # Code: roda Python com acesso a variaveis
                    code = _substituir_variaveis(
                        etapa_config.get("prompt", ""), variaveis
                    )
                    # Contexto seguro para execução
                    exec_globals = {
                        "entrada": variaveis.get("entrada", ""),
                        "saida_anterior": variaveis.get("saida_anterior", ""),
                        "roteiro_atual": variaveis.get("roteiro_atual", ""),
                        "variaveis": dict(variaveis),
                        "resultado": "",
                        "len": len, "str": str, "int": int, "float": float,
                        "replace": str.replace, "upper": str.upper, "lower": str.lower,
                        "re": __import__("re"),
                    }
                    exec(code, exec_globals)
                    resultado = str(exec_globals.get("resultado", ""))

                else:
                    # LLM: chamar API
                    cred_id = etapa_config.get("credencial", "")
                    cred = obter_credencial(cred_id)
                    if not cred:
                        raise ValueError(f"Credencial não encontrada: {cred_id}")

                    provedor = cred.get("provedor", "claude")
                    api_key = cred.get("api_key", "")
                    modelo = etapa_config.get("modelo", "")

                    system_msg = _substituir_variaveis(
                        etapa_config.get("system_message", ""), variaveis
                    )
                    user_msg = _substituir_variaveis(
                        etapa_config.get("prompt", ""), variaveis
                    )

                    fn = CHAMADAS.get(provedor)
                    if not fn:
                        raise ValueError(f"Provedor desconhecido: {provedor}")
                    resultado = fn(system_msg, user_msg, api_key, modelo)

                estado_execucao["etapas"][i]["status"] = "concluido"
                estado_execucao["etapas"][i]["resultado"] = resultado
                estado_execucao["etapas"][i]["fim"] = time.time()

                variaveis["saida_anterior"] = resultado
                variaveis[f"saida_etapa_{i+1}"] = resultado
                variaveis["roteiro_atual"] = resultado

            except Exception as e:
                estado_execucao["etapas"][i]["status"] = "erro"
                estado_execucao["etapas"][i]["erro"] = str(e)
                estado_execucao["etapas"][i]["fim"] = time.time()
                variaveis["saida_anterior"] = f"[ERRO na etapa: {e}]"

        # Resultado final = saída da última etapa concluída
        for etapa in reversed(estado_execucao["etapas"]):
            if etapa["status"] == "concluido" and etapa["resultado"]:
                estado_execucao["resultado_final"] = etapa["resultado"]
                break

        # Salvar roteiro
        if estado_execucao["resultado_final"]:
            nome_pipeline = pipeline.get("nome", pipeline_id)
            data = datetime.now().strftime("%Y%m%d_%H%M%S")
            arquivo = SCRIPTS_DIR / f"{nome_pipeline}_{data}.txt"
            arquivo.write_text(estado_execucao["resultado_final"], encoding="utf-8")

    except Exception:
        traceback.print_exc()
    finally:
        estado_execucao["ativo"] = False


# === SYNC SUPABASE / GOOGLE SHEETS ===

def sync_supabase(tabela: str, dados: dict, config: dict) -> bool:
    """Insere/atualiza registro no Supabase."""
    url = config.get("supabase_url", "")
    key = config.get("supabase_key", "")
    if not url or not key:
        return False
    try:
        resp = httpx.post(
            f"{url}/rest/v1/{tabela}",
            headers={
                "apikey": key,
                "Authorization": f"Bearer {key}",
                "Content-Type": "application/json",
                "Prefer": "resolution=merge-duplicates",
            },
            json=dados,
            timeout=15.0,
        )
        return resp.status_code < 300
    except Exception:
        return False


def sync_sheets(dados: list, config: dict) -> bool:
    """Append rows to Google Sheets."""
    sheet_id = config.get("sheets_id", "")
    api_key = config.get("sheets_api_key", "")
    sheet_name = config.get("sheets_tab", "Temas")
    if not sheet_id or not api_key:
        return False
    try:
        resp = httpx.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{sheet_name}!A:Z:append"
            f"?valueInputOption=USER_ENTERED&key={api_key}",
            json={"values": dados},
            timeout=15.0,
        )
        return resp.status_code < 300
    except Exception:
        return False
