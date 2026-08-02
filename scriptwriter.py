"""
Motor de geração de roteiros via APIs de LLM.
Suporta Claude, GPT e Gemini. Sistema de credenciais múltiplas com listagem automática de modelos.
"""

import json
import os
import shutil
import threading
import time
import traceback
from pathlib import Path
from datetime import datetime

import httpx

# Cliente httpx compartilhado com connection pooling (evita esgotamento de portas)
_http_client = httpx.Client(
    timeout=httpx.Timeout(300.0, connect=15.0),
    limits=httpx.Limits(max_connections=10, max_keepalive_connections=5),
)

BASE_DIR = Path(__file__).parent
PIPELINES_FILE = BASE_DIR / "pipelines.json"
CONFIG_FILE = BASE_DIR / "config.json"
CREDENTIALS_FILE = BASE_DIR / "credentials.json"
TEMAS_FILE = BASE_DIR / "temas.json"
SCRIPTS_DIR = BASE_DIR / "scripts"
SCRIPTS_DIR.mkdir(exist_ok=True)


# === PERSISTÊNCIA ===

# Lock global por path para evitar writes concorrentes corromperem o arquivo.
# Salva ATOMICAMENTE (tmp + os.replace) e mantém 5 backups rotativos.
# Bug histórico (03/06/2026): write não-atômico em temas.json deixou o arquivo
# com null bytes no meio + header zerado, derrubando o orchestrator.
_SAVE_LOCKS: dict = {}
_SAVE_LOCKS_LOCK = threading.Lock()

# Arquivos críticos: mantém backups rotativos antes de cada save.
_BACKUP_ROTATIVO = {"temas.json", "config.json", "pipelines.json", "credentials.json", "templates.json"}


def _get_save_lock(path: Path) -> threading.Lock:
    p = str(path)
    with _SAVE_LOCKS_LOCK:
        lk = _SAVE_LOCKS.get(p)
        if lk is None:
            lk = threading.Lock()
            _SAVE_LOCKS[p] = lk
        return lk


def _carregar_json(path: Path, default=None):
    if path.exists():
        try:
            with open(path, "r", encoding="utf-8") as f:
                return json.load(f)
        except (json.JSONDecodeError, UnicodeDecodeError) as e:
            # Arquivo corrompido — tentar último backup .bak1 e logar incidente.
            print(f"[_carregar_json] ARQUIVO CORROMPIDO {path.name}: {e}")
            for i in range(1, 6):
                bak = path.with_name(path.name + f".bak{i}")
                if bak.exists():
                    try:
                        with open(bak, "r", encoding="utf-8") as f:
                            data = json.load(f)
                        print(f"[_carregar_json] RECUPERADO de {bak.name}")
                        return data
                    except Exception:
                        continue
            print(f"[_carregar_json] NENHUM backup valido para {path.name}, usando default")
    return default if default is not None else {}


def _salvar_json(path: Path, data):
    """Write atômico: tmp + os.replace + backup rotativo.
    Garante: nunca deixa o arquivo final em estado parcial. Mesmo se o processo
    morrer no meio, o arquivo original permanece intacto (só o .tmp fica perdido).
    Concorrência: lock por path serializa writes ao mesmo arquivo no mesmo processo.
    """
    lock = _get_save_lock(path)
    with lock:
        path = Path(path)
        # 1. Backup rotativo (apenas para arquivos críticos)
        if path.name in _BACKUP_ROTATIVO and path.exists():
            try:
                # Rotaciona: bak4 -> bak5, bak3 -> bak4, ..., bak1 -> bak2
                for i in range(4, 0, -1):
                    src = path.with_name(path.name + f".bak{i}")
                    dst = path.with_name(path.name + f".bak{i+1}")
                    if src.exists():
                        os.replace(str(src), str(dst))
                # bak1 = snapshot atual (copy2 preserva mtime)
                shutil.copy2(str(path), str(path.with_name(path.name + ".bak1")))
            except Exception as e:
                # Backup falha não pode impedir o save — só loga.
                print(f"[_salvar_json] WARN backup {path.name} falhou: {e}")

        # 2. Write atômico: tmp + fsync + replace
        tmp = path.with_name(path.name + f".tmp.{os.getpid()}.{threading.get_ident()}")
        try:
            with open(tmp, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
                f.flush()
                os.fsync(f.fileno())
            os.replace(str(tmp), str(path))
        except Exception:
            # Limpa tmp se algo deu errado
            try:
                if tmp.exists():
                    tmp.unlink()
            except Exception:
                pass
            raise


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

# Campos de CONTEUDO que congelam assim que a celula vira video no ar.
# Os campos de upload (youtube_*, upload_status, uploaded_at, done*) seguem
# livres — o reconciliador precisa escrever neles.
_CONGELA_POS_UPLOAD = ("titulo", "tema", "thumb", "roteiro")


def _congelar_publicadas(temas) -> list:
    """Reverte alteracoes de CONTEUDO em celulas que ja viraram video no ar.

    Regra do Piter (30/07): "video postado NAO tem o grid alterado. Se houver
    alteracao, sera manual (ou solicitado) direto no video no canal."

    POR QUE E' CODIGO: o upload le titulo/thumb do grid NA HORA DE SUBIR
    (upload_one.py:78) e o reconciliador re-le a cada passada. Trocar o texto de
    uma celula ja publicada faz a proxima reconciliacao "consertar" o video no ar
    pro texto novo, sem ninguem pedir. Muta `temas` in-place e devolve a lista do
    que foi revertido.
    """
    if not isinstance(temas, dict):
        return []
    novas = temas.get("celulas")
    if not isinstance(novas, dict):
        return []
    disco = _carregar_json(TEMAS_FILE, {})
    antigas = disco.get("celulas") if isinstance(disco, dict) else None
    if not isinstance(antigas, dict):
        return []
    revertidos = []
    for key, cel_nova in novas.items():
        if not isinstance(cel_nova, dict):
            continue
        cel_velha = antigas.get(key)
        if not isinstance(cel_velha, dict) or not cel_velha.get("youtube_video_id"):
            continue                       # nunca publicou -> pode editar
        for campo in _CONGELA_POS_UPLOAD:
            if campo in cel_nova and cel_nova[campo] != cel_velha.get(campo):
                cel_nova[campo] = cel_velha.get(campo)
                revertidos.append(f"{key}.{campo}")
    return revertidos


def salvar_temas(temas: list):
    """Salva o grid inteiro. CELULA JA PUBLICADA E' IMUTAVEL no conteudo.

    O guard mora AQUI e nao no POST /api/temas porque o endpoint NAO e' o unico
    caminho: o coringa_distribuidor grava direto por `_salvar_temas` -> aqui.
    Um guard so no endpoint deixaria a distribuicao passar por baixo — que foi
    exatamente quem contaminou ENO (27/07) e CON (30/07-30/08). Este e' o funil
    real pro disco.

    Fora do lock de proposito: `_salvar_json` pega o MESMO lock por path e
    `threading.Lock` nao e' reentrante (pegar aqui = deadlock). A janela de
    corrida e' irrelevante: isto protege contra edicao humana/distribuidor, nao
    contra corrida de microssegundos — e o patch atomico de upload nem passa aqui.
    """
    try:
        revertidos = _congelar_publicadas(temas)
        if revertidos:
            print(f"[salvar_temas] CONGELADO (video ja publicado): "
                  f"{', '.join(revertidos[:12])}{' ...' if len(revertidos) > 12 else ''}")
    except Exception as e:
        print(f"[salvar_temas] WARN guard de celula publicada falhou: {e}")
    _salvar_json(TEMAS_FILE, temas)


def patch_temas_celula(key: str, patch: dict) -> dict:
    """Read-modify-write ATOMICO de UMA celula do temas.json, sob o MESMO lock
    (por-path) do _salvar_json. O cliente manda so a identidade + campos; o
    servidor serializa aqui -> sem lost-update entre marks/saves concorrentes e
    sem o read-modify-write de 4.4MB no cliente. Retorna a celula resultante.

    Usado pelo POST /api/upload/mark (plug do upload event-driven)."""
    lock = _get_save_lock(TEMAS_FILE)
    with lock:
        d = _carregar_json(TEMAS_FILE, {})
        if not isinstance(d, dict):
            d = {}
        cels = d.get("celulas")
        if not isinstance(cels, dict):
            cels = {}
            d["celulas"] = cels
        cel = cels.get(key)
        cel = dict(cel) if isinstance(cel, dict) else {}
        cel.update(patch)
        cels[key] = cel
        # Write atomico (mesma logica do _salvar_json; lock JA segurado -> nao re-adquire).
        _escrever_temas_sob_lock(d)
        return cel


def _escrever_temas_sob_lock(d: dict):
    """Backup rotativo + write atomico do temas.json. PRESSUPOE o lock JA segurado
    (extraido do patch_temas_celula pra ser reusado pelas escritas celula-a-celula)."""
    if TEMAS_FILE.name in _BACKUP_ROTATIVO and TEMAS_FILE.exists():
        try:
            for i in range(4, 0, -1):
                src = TEMAS_FILE.with_name(TEMAS_FILE.name + f".bak{i}")
                dst = TEMAS_FILE.with_name(TEMAS_FILE.name + f".bak{i+1}")
                if src.exists():
                    os.replace(str(src), str(dst))
            shutil.copy2(str(TEMAS_FILE), str(TEMAS_FILE.with_name(TEMAS_FILE.name + ".bak1")))
        except Exception as e:
            print(f"[_escrever_temas_sob_lock] WARN backup falhou: {e}")
    tmp = TEMAS_FILE.with_name(TEMAS_FILE.name + f".tmp.{os.getpid()}.{threading.get_ident()}")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2)
            f.flush()
            os.fsync(f.fileno())
        os.replace(str(tmp), str(TEMAS_FILE))
    except Exception:
        try:
            if tmp.exists():
                tmp.unlink()
        except Exception:
            pass
        raise


def ensure_linha_temas(data_str: str) -> dict:
    """Garante que existe uma linha com essa data (DD/MM/YYYY). IDEMPOTENTE e atomico.

    É o unico ponto com race real no modelo celula-a-celula (2 clientes criando a
    mesma data em paralelo criariam linhas duplicadas) -> resolvido aqui, sob o lock.
    Insere mantendo a ORDEM CRONOLOGICA. Retorna {row, criada, data}.
    """
    from datetime import datetime as _dt
    lock = _get_save_lock(TEMAS_FILE)
    with lock:
        d = _carregar_json(TEMAS_FILE, {})
        if not isinstance(d, dict):
            d = {}
        linhas = d.setdefault("linhas", [])
        for i, L in enumerate(linhas):
            if (L or {}).get("data") == data_str:
                return {"row": i, "criada": False, "data": data_str}
        try:
            alvo = _dt.strptime(data_str, "%d/%m/%Y").date()
        except Exception:
            raise ValueError(f"data invalida (esperado DD/MM/YYYY): {data_str}")
        pos = len(linhas)
        for i, L in enumerate(linhas):
            try:
                if _dt.strptime((L or {}).get("data", ""), "%d/%m/%Y").date() > alvo:
                    pos = i
                    break
            except Exception:
                continue
        linhas.insert(pos, {"data": data_str})
        # celulas sao chaveadas por "{row}_{col}" -> inserir linha SHIFTA todas abaixo
        cels = d.get("celulas")
        if isinstance(cels, dict) and pos < len(linhas) - 1:
            novo = {}
            for k, v in cels.items():
                try:
                    r, c = k.split("_")
                    r = int(r)
                except (ValueError, AttributeError):
                    novo[k] = v
                    continue
                novo[f"{r + 1 if r >= pos else r}_{c}"] = v
            d["celulas"] = novo
        _escrever_temas_sob_lock(d)
        return {"row": pos, "criada": True, "data": data_str}


# Campos de CONTEUDO que a criacao de canal (skill arquitetar-canal) escreve.
# Campos de upload NAO entram aqui — esses tem o /api/upload/mark proprio.
CAMPOS_CONTEUDO = ("tema", "titulo", "thumb", "roteiro", "pipeline_id",
                   "thumb_ia_prompt", "descricao", "tags")


def put_celula_temas(key: str, campos: dict, sobrescrever: bool = False) -> dict:
    """Escreve UMA celula COMPLETA de conteudo, com guard, tudo sob o MESMO lock.

    O guard precisa rodar DENTRO do lock: checar fora e gravar depois abriria uma
    janela (TOCTOU) em que outro writer publica a celula no meio.

    Guards (na ordem):
      1. celula PUBLICADA (tem youtube_video_id) -> ABORTA sempre (regra do Piter:
         video postado nao tem o grid alterado).
      2. celula OCUPADA (tem titulo ou roteiro) -> ABORTA, a menos que sobrescrever=True.
      3. titulo > 100 CODEPOINTS -> ABORTA (limite do YouTube; len() do Python, nunca no olho).

    Retorna {ok, celula} ou {ok:False, erro, motivo}.
    """
    campos = {k: v for k, v in (campos or {}).items() if k in CAMPOS_CONTEUDO}
    if not campos:
        return {"ok": False, "erro": "Nenhum campo de conteudo valido", "motivo": "vazio"}

    tit = campos.get("titulo")
    if tit and len(str(tit)) > 100:
        return {"ok": False, "motivo": "titulo_longo",
                "erro": f"titulo tem {len(str(tit))} codepoints (limite 100)"}

    lock = _get_save_lock(TEMAS_FILE)
    with lock:
        d = _carregar_json(TEMAS_FILE, {})
        if not isinstance(d, dict):
            d = {}
        cels = d.setdefault("celulas", {})
        atual = cels.get(key) if isinstance(cels.get(key), dict) else {}

        if atual.get("youtube_video_id"):
            return {"ok": False, "motivo": "publicada", "celula": atual,
                    "erro": f"celula {key} ja tem video publicado ({atual['youtube_video_id']}) — imutavel"}
        if not sobrescrever and (atual.get("titulo") or atual.get("roteiro")):
            return {"ok": False, "motivo": "ocupada", "celula": atual,
                    "erro": f"celula {key} ja tem conteudo — use sobrescrever=true se for intencional"}

        nova = dict(atual)
        nova.update(campos)
        cels[key] = nova
        _escrever_temas_sob_lock(d)
        return {"ok": True, "celula": nova, "criada": not atual}


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
            resp = _http_client.get(
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
            resp = _http_client.get(
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
            resp = _http_client.get(
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
        resp = _http_client.post(
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
        resp = _http_client.post(
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
        resp = _http_client.post(
            f"https://generativelanguage.googleapis.com/v1beta/models/{model}:generateContent?key={api_key}",
            headers={"Content-Type": "application/json"},
            json={
                "system_instruction": {"parts": [{"text": system_msg}]},
                "contents": [{"parts": [{"text": user_msg}]}],
                "generationConfig": {"maxOutputTokens": 65536},
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
    System prompt passado via --system-prompt como lista de args (sem shell)."""
    import subprocess as sp

    cli_model = "sonnet"
    if "opus" in model.lower():
        cli_model = "opus"
    elif "haiku" in model.lower():
        cli_model = "haiku"

    # Limpar env vars que causam "nested session" error
    env = dict(os.environ)
    for key in list(env.keys()):
        if "CLAUDE" in key.upper() or "ANTHROPIC" in key.upper():
            del env[key]

    # Encontrar claude.cmd
    claude_cmd = os.path.join(os.environ.get("APPDATA", ""), "npm", "claude.cmd")
    if not os.path.exists(claude_cmd):
        claude_cmd = "claude"

    for attempt in range(2):
        # Montar como lista (nao shell=True) pra evitar limite de cmd line
        cmd = [claude_cmd, "-p", "--model", cli_model, "--output-format", "text", "--tools", ""]
        if system_msg:
            # Adicionar instrucao pra nao narrar a propria acao
            clean_system = system_msg + "\n\nCRITICAL OUTPUT RULES:\n- Output ONLY the raw script text, nothing else\n- Do NOT add timestamps (0:00, 1:20, etc)\n- Do NOT add section headers (## HOOK, ## BRIDGE, ## ACT, etc)\n- Do NOT add markdown formatting (no #, ##, **, etc)\n- Do NOT add preamble or commentary (no 'I'll create...', 'Here is...', 'Sure...')\n- Start directly with the first spoken word of the script"
            cmd.extend(["--system-prompt", clean_system])

        try:
            proc = sp.run(
                cmd, input=user_msg, capture_output=True, text=True,
                timeout=300, encoding="utf-8", errors="replace",
                env=env,
            )
            if proc.returncode == 0 and proc.stdout.strip():
                output = proc.stdout.strip()
                import re
                # Limpar preambles (ex: "I'll create...", "Here is...")
                output = re.sub(r'^(?:(?:Okay|Sure|Here|I\'ll|Let me|Certainly)[^\n]*\n)+', '', output, flags=re.IGNORECASE).strip()
                # Limpar timestamps (ex: "## HOOK (0:00 - 0:35)")
                output = re.sub(r'^#+\s+.*?\(\d+:\d+.*?\)\s*\n', '', output, flags=re.MULTILINE).strip()
                # Limpar section headers markdown (ex: "## BRIDGE", "# TITLE")
                output = re.sub(r'^#+\s+(HOOK|BRIDGE|ACT|SECTION|THE LOVE|CLOSING|OUTRO|INTRO|PART)\b[^\n]*\n', '', output, flags=re.MULTILINE | re.IGNORECASE).strip()
                return output
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

def executar_pipeline_isolado(pipeline_id: str, entrada: str, contexto_extra: dict = None, forcar_fallback: bool = False) -> dict:
    """
    Executa pipeline sem usar estado_execucao global.
    Thread-safe: cada chamada usa apenas variaveis locais.

    Args:
        forcar_fallback: Se True, em cada etapa LLM pula direto pro fallback do provider primario,
                         em vez de tentar o primario primeiro. Util quando o primario esta gerando
                         output ruim (ex: roteiros curtos) e queremos forcar outro provider.

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

                    # Se forcar_fallback=True, pula direto pro fallback (sem tentar primario)
                    if forcar_fallback:
                        fallback_cred = _obter_fallback_credencial(provedor)
                        if fallback_cred:
                            fb_fn = CHAMADAS.get(fallback_cred["provedor"])
                            if fb_fn:
                                print(f"[FORCED-FALLBACK] Pulando {provedor}/{modelo}, usando {fallback_cred['provedor']}/{fallback_cred['modelo']}")
                                resultado = fb_fn(system_msg, user_msg, fallback_cred["api_key"], fallback_cred["modelo"])
                                etapa_info["provider_usado"] = fallback_cred["provedor"]
                                etapa_info["fallback_used"] = True
                            else:
                                # fallback nao disponivel, segue primario
                                resultado = fn(system_msg, user_msg, api_key, modelo)
                                etapa_info["provider_usado"] = provedor
                                etapa_info["fallback_used"] = False
                        else:
                            resultado = fn(system_msg, user_msg, api_key, modelo)
                            etapa_info["provider_usado"] = provedor
                            etapa_info["fallback_used"] = False
                    else:
                        try:
                            resultado = fn(system_msg, user_msg, api_key, modelo)
                            etapa_info["provider_usado"] = provedor
                            etapa_info["fallback_used"] = False
                        except Exception as llm_err:
                            fallback_cred = _obter_fallback_credencial(provedor)
                            if fallback_cred:
                                print(f"[FALLBACK-ISO] {provedor}/{modelo} falhou: {llm_err}. Tentando {fallback_cred['provedor']}/{fallback_cred['modelo']}...")
                                fb_fn = CHAMADAS.get(fallback_cred["provedor"])
                                if fb_fn:
                                    resultado = fb_fn(system_msg, user_msg, fallback_cred["api_key"], fallback_cred["modelo"])
                                    etapa_info["provider_usado"] = fallback_cred["provedor"]
                                    etapa_info["fallback_used"] = True
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
        resp = _http_client.post(
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
        resp = _http_client.post(
            f"https://sheets.googleapis.com/v4/spreadsheets/{sheet_id}/values/{sheet_name}!A:Z:append"
            f"?valueInputOption=USER_ENTERED&key={api_key}",
            json={"values": dados},
            timeout=15.0,
        )
        return resp.status_code < 300
    except Exception:
        return False
