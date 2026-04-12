"""
Motor de geração de roteiros via APIs de LLM.
Suporta Claude, GPT e Gemini. Sistema de credenciais múltiplas com listagem automática de modelos.
"""

import json
import os
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
            prefixos = ("gpt-5", "gpt-4", "gpt-3.5", "o1", "o3", "o4")
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


# Fallback LLM padrão quando o modelo principal falha
# Ordem de prioridade: Claude > Gemini > GPT (evitar fallback pro mesmo provider)
FALLBACK_CHAIN = [
    ("claude_cli", "claude-sonnet-4-6"),
    ("claude", "claude-sonnet-4-6"),
    ("gemini", "gemini-2.5-flash"),
    ("gpt", "gpt-5.2"),
]


def _obter_fallback_credencial(provedor_atual: str = "") -> dict:
    """Busca credencial de fallback, pulando o provider que já falhou."""
    creds = carregar_credenciais()
    for fb_provider, fb_model in FALLBACK_CHAIN:
        if fb_provider == provedor_atual:
            continue  # Pular o mesmo provider que falhou
        for c in creds:
            if c.get("provedor") == fb_provider and c.get("status") == "ok":
                return {
                    "provedor": fb_provider,
                    "api_key": c.get("api_key", ""),
                    "modelo": fb_model,
                }
    return None


# === CHAMADAS AOS MODELOS ===

def _chamar_claude(system_msg: str, user_msg: str, api_key: str, model: str) -> str:
    for attempt in range(3):
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
            timeout=300.0,
        )
        if resp.status_code == 429:
            import time as _time; _time.sleep((attempt + 1) * 30)
            continue
        break
    resp.raise_for_status()
    data = resp.json()
    return data["content"][0]["text"]


def _chamar_gpt(system_msg: str, user_msg: str, api_key: str, model: str) -> str:
    for attempt in range(3):
        # GPT 5.x usa max_completion_tokens, modelos antigos usam max_tokens
        token_param = "max_completion_tokens" if model.startswith("gpt-5") or model.startswith("o") else "max_tokens"
        resp = httpx.post(
            "https://api.openai.com/v1/chat/completions",
            headers={
                "Authorization": f"Bearer {api_key}",
                "Content-Type": "application/json",
            },
            json={
                "model": model,
                token_param: 32000,
                "messages": [
                    {"role": "system", "content": system_msg},
                    {"role": "user", "content": user_msg},
                ],
            },
        timeout=300.0,
        )
        if resp.status_code == 429:
            import time as _time; _time.sleep((attempt + 1) * 30)
            continue
        break
    resp.raise_for_status()
    data = resp.json()
    return data["choices"][0]["message"]["content"]


def _chamar_gemini(system_msg: str, user_msg: str, api_key: str, model: str) -> str:
    # Retry com backoff para rate limiting (429)
    for attempt in range(3):
        resp = httpx.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "system_instruction": {"parts": [{"text": system_msg}]},
                "contents": [{"parts": [{"text": user_msg}]}],
                "generationConfig": {"maxOutputTokens": 32000},
            },
            timeout=300.0,
        )
        if resp.status_code == 429:
            wait = (attempt + 1) * 30  # 30s, 60s, 90s
            import time as _time
            _time.sleep(wait)
            continue
        break
    resp.raise_for_status()
    data = resp.json()
    return data["candidates"][0]["content"]["parts"][0]["text"]


def _chamar_claude_cli(system_msg: str, user_msg: str, api_key: str, model: str) -> str:
    """Chama Claude via CLI (-p mode). Usa plano Max, sem custo de API.
    System prompt enviado via arquivo temp pra evitar limite de cmd line."""
    import subprocess as sp
    import tempfile

    cli_model = "sonnet"
    if "opus" in model.lower():
        cli_model = "opus"
    elif "haiku" in model.lower():
        cli_model = "haiku"

    cmd = ["claude", "-p", "--model", cli_model, "--output-format", "text", "--tools", ""]

    # System prompt via arquivo temp (evita truncamento na cmd line do Windows)
    sys_file = None
    if system_msg:
        sys_file = tempfile.NamedTemporaryFile(mode="w", suffix=".txt", delete=False, encoding="utf-8")
        sys_file.write(system_msg)
        sys_file.close()
        cmd.extend(["--system-prompt", f"$(cat '{sys_file.name}')"])

    # Limpar env vars que causam "nested session" error
    env = dict(os.environ)
    for key in list(env.keys()):
        if "CLAUDE" in key.upper() or "ANTHROPIC" in key.upper():
            del env[key]

    # Combinar system + user no input pra garantir que o CLI recebe tudo
    combined_input = user_msg
    if system_msg:
        combined_input = f"[SYSTEM INSTRUCTIONS - Follow these exactly]\n{system_msg}\n[END SYSTEM INSTRUCTIONS]\n\n{user_msg}"
        # Nao usar --system-prompt, passar tudo como input
        cmd = ["claude", "-p", "--model", cli_model, "--output-format", "text", "--tools", ""]

    for attempt in range(2):
        try:
            proc = sp.run(
                cmd, input=combined_input, capture_output=True, text=True,
                timeout=300, encoding="utf-8", errors="replace",
                env=env, shell=True,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                return proc.stdout.strip()
            elif "rate limit" in proc.stderr.lower() or "too many" in proc.stderr.lower():
                import time as _time
                _time.sleep((attempt + 1) * 30)
                continue
            else:
                raise RuntimeError(f"Claude CLI erro (code {proc.returncode}): {proc.stderr[:200]}")
        except sp.TimeoutExpired:
            raise RuntimeError("Claude CLI timeout (300s)")
    raise RuntimeError("Claude CLI rate limited apos 2 tentativas")


CHAMADAS = {
    "claude": _chamar_claude,
    "claude_cli": _chamar_claude_cli,
    "gpt": _chamar_gpt,
    "gemini": _chamar_gemini,
}


def _substituir_variaveis(texto: str, variaveis: dict) -> str:
    import re
    for chave, valor in variaveis.items():
        # Substituir {{chave}} com tolerância a espaços
        pattern = r'\{\{\s*' + re.escape(chave) + r'\s*\}\}'
        texto = re.sub(pattern, str(valor), texto)
    # Limpar variaveis malformadas: {{algo} sem fechar (falta }})
    texto = re.sub(r'\{\{[a-zA-Z_][a-zA-Z0-9_]*\}(?!\})', '', texto)
    # Limpar variaveis nao-substituidas restantes: {{algo}}
    texto = re.sub(r'\{\{\s*[a-zA-Z_][a-zA-Z0-9_]*\s*\}\}', '', texto)
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

                    try:
                        resultado = fn(system_msg, user_msg, api_key, modelo)
                    except Exception as llm_err:
                        # FALLBACK: tentar com outro provider
                        fallback_cred = _obter_fallback_credencial(provedor)
                        if fallback_cred:
                            print(f"[FALLBACK] {provedor}/{modelo} falhou: {llm_err}. Tentando {fallback_cred['provedor']}/{fallback_cred['modelo']}...")
                            fb_fn = CHAMADAS.get(fallback_cred["provedor"])
                            if fb_fn:
                                resultado = fb_fn(system_msg, user_msg, fallback_cred["api_key"], fallback_cred["modelo"])
                            else:
                                raise llm_err
                        else:
                            raise llm_err

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
                # Se etapa LLM falhou, abortar pipeline (continuar geraria lixo)
                tipo_etapa = etapa_config.get("tipo", "llm")
                if tipo_etapa == "llm":
                    print(f"[PIPELINE] Etapa LLM '{etapa_config.get('nome','')}' falhou: {e}. Abortando pipeline.")
                    break
                # Para etapas code/texto, manter comportamento anterior
                variaveis["saida_anterior"] = variaveis.get("saida_anterior", "")

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
        # Salvar log persistente
        try:
            logs_dir = BASE_DIR / "logs"
            logs_dir.mkdir(exist_ok=True)
            log_data = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = logs_dir / f"pipeline_{pipeline_id}_{log_data}.log"
            lines = [f"Pipeline: {pipeline_id}", f"Data: {log_data}", ""]
            for i, e in enumerate(estado_execucao.get("etapas", [])):
                lines.append(f"Etapa {i+1} [{e.get('nome','')}]: {e.get('status','')} | {len(e.get('resultado',''))} chars")
                if e.get("erro"):
                    lines.append(f"  ERRO: {e['erro']}")
            lines.append(f"\nResultado final: {len(estado_execucao.get('resultado_final',''))} chars")
            log_file.write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            pass


# === EXECUCAO ISOLADA (thread-safe, para roteiros paralelos) ===

def executar_pipeline_isolado(pipeline_id: str, entrada: str, contexto_extra: dict = None) -> dict:
    """
    Executa pipeline sem usar estado_execucao global.
    Thread-safe: cada chamada usa apenas variaveis locais.
    Retorna {"ok": bool, "resultado": str, "erro": str, "etapas": list}.
    """
    pipelines = carregar_pipelines()
    pipeline = pipelines.get(pipeline_id)
    if not pipeline:
        return {"ok": False, "resultado": "", "erro": f"Pipeline nao encontrada: {pipeline_id}", "etapas": []}

    etapas_config = pipeline.get("etapas", [])
    etapas_log = []

    variaveis = {
        "entrada": entrada,
        "tema": entrada,
        "saida_anterior": "",
        "roteiro_atual": "",
    }
    if contexto_extra:
        variaveis.update(contexto_extra)

    resultado_final = ""

    try:
        for i, etapa_config in enumerate(etapas_config):
            etapa_info = {"nome": etapa_config.get("nome", f"Etapa {i+1}"), "status": "processando", "erro": None, "chars": 0}
            etapas_log.append(etapa_info)

            try:
                tipo = etapa_config.get("tipo", "llm")

                if tipo == "texto":
                    resultado = _substituir_variaveis(etapa_config.get("prompt", ""), variaveis)

                elif tipo == "code":
                    code = _substituir_variaveis(etapa_config.get("prompt", ""), variaveis)
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
                    # LLM call
                    cred_id = etapa_config.get("credencial", "")
                    cred = obter_credencial(cred_id)
                    if not cred:
                        raise ValueError(f"Credencial nao encontrada: {cred_id}")

                    provedor = cred.get("provedor", "claude")
                    api_key = cred.get("api_key", "")
                    modelo = etapa_config.get("modelo", "")

                    system_msg = _substituir_variaveis(etapa_config.get("system_message", ""), variaveis)
                    user_msg = _substituir_variaveis(etapa_config.get("prompt", ""), variaveis)

                    fn = CHAMADAS.get(provedor)
                    if not fn:
                        raise ValueError(f"Provedor desconhecido: {provedor}")

                    try:
                        resultado = fn(system_msg, user_msg, api_key, modelo)
                    except Exception as llm_err:
                        fallback_cred = _obter_fallback_credencial(provedor)
                        if fallback_cred:
                            print(f"[FALLBACK-ISO] {provedor}/{modelo} falhou: {llm_err}. Tentando {fallback_cred['provedor']}/{fallback_cred['modelo']}...")
                            fb_fn = CHAMADAS.get(fallback_cred["provedor"])
                            if fb_fn:
                                resultado = fb_fn(system_msg, user_msg, fallback_cred["api_key"], fallback_cred["modelo"])
                            else:
                                raise llm_err
                        else:
                            raise llm_err

                etapa_info["status"] = "concluido"
                etapa_info["chars"] = len(resultado)
                variaveis["saida_anterior"] = resultado
                variaveis[f"saida_etapa_{i+1}"] = resultado
                variaveis["roteiro_atual"] = resultado

            except Exception as e:
                etapa_info["status"] = "erro"
                etapa_info["erro"] = str(e)
                if etapa_config.get("tipo", "llm") == "llm":
                    print(f"[PIPELINE-ISO] Etapa LLM '{etapa_config.get('nome','')}' falhou: {e}. Abortando.")
                    break
                variaveis["saida_anterior"] = variaveis.get("saida_anterior", "")

        # Resultado final = ultima etapa concluida
        for etapa in reversed(etapas_log):
            if etapa["status"] == "concluido":
                resultado_final = variaveis.get("roteiro_atual", "")
                break

        # Salvar script em arquivo
        if resultado_final:
            nome_pipeline = pipeline.get("nome", pipeline_id)
            data = datetime.now().strftime("%Y%m%d_%H%M%S")
            arquivo = SCRIPTS_DIR / f"{nome_pipeline}_{data}.txt"
            arquivo.write_text(resultado_final, encoding="utf-8")

        # Log persistente
        try:
            logs_dir = BASE_DIR / "logs"
            logs_dir.mkdir(exist_ok=True)
            log_data = datetime.now().strftime("%Y%m%d_%H%M%S")
            log_file = logs_dir / f"pipeline_{pipeline_id}_{log_data}.log"
            lines = [f"Pipeline: {pipeline_id} (isolado)", f"Data: {log_data}", ""]
            for j, e in enumerate(etapas_log):
                lines.append(f"Etapa {j+1} [{e.get('nome','')}]: {e.get('status','')} | {e.get('chars',0)} chars")
                if e.get("erro"):
                    lines.append(f"  ERRO: {e['erro']}")
            lines.append(f"\nResultado final: {len(resultado_final)} chars")
            log_file.write_text("\n".join(lines), encoding="utf-8")
        except Exception:
            pass

        return {"ok": bool(resultado_final and len(resultado_final) > 100), "resultado": resultado_final, "erro": "", "etapas": etapas_log}

    except Exception as e:
        traceback.print_exc()
        return {"ok": False, "resultado": "", "erro": str(e), "etapas": etapas_log}


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
