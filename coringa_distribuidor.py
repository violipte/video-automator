"""
Distribuidor Coringa: pega items do Backlog Temas, cria linha no grid Temas,
preenche celula Coringa (col 0), marca geral=Ok no Backlog.

FASE 1: cria linha + Coringa BASE.
FASE 2 (atual): distribuicao pros canais Geral com adaptacao Claude CLI.
FASE 3: logica CO* em cruz com cascade NARC/NPD adaptado.
"""
import json
import re
import threading
import time
from datetime import datetime, date
from pathlib import Path
from typing import Optional

import backlog_temas_db

BASE_DIR = Path(__file__).parent
TEMAS_FILE = BASE_DIR / "temas.json"
AUTOMACAO_FLAG_FILE = BASE_DIR / "coringa_automacao.json"
_temas_lock = threading.RLock()  # protege read-modify-write do temas.json
_automacao_lock = threading.Lock()


# ============= FLAG GLOBAL DE AUTOMACAO (liga/desliga crons) =============
# Persistida em disco. Default: False (crons dormem mas nao processam).
# Endpoints manuais (/processar-agora, /distribuir-agora, /processar-co-agora)
# IGNORAM essa flag - sempre rodam.

def get_automacao_habilitada() -> bool:
    if not AUTOMACAO_FLAG_FILE.exists():
        return False  # default DESLIGADA
    try:
        with open(AUTOMACAO_FLAG_FILE, "r", encoding="utf-8") as f:
            return bool(json.load(f).get("habilitada", False))
    except Exception:
        return False


def set_automacao_habilitada(valor: bool) -> bool:
    with _automacao_lock:
        try:
            with open(AUTOMACAO_FLAG_FILE, "w", encoding="utf-8") as f:
                json.dump({
                    "habilitada": bool(valor),
                    "atualizado_em": datetime.now().isoformat(timespec="seconds"),
                }, f)
            return True
        except Exception as e:
            print(f"[coringa] erro salvando flag automacao: {e}")
            return False


# ============= TEMAS.JSON HELPERS =============

def _carregar_temas() -> dict:
    if not TEMAS_FILE.exists():
        return {"colunas": [], "linhas": [], "celulas": {}}
    try:
        with open(TEMAS_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        data.setdefault("colunas", [])
        data.setdefault("linhas", [])
        data.setdefault("celulas", {})
        # Migration: rename coluna 'Coringa' (legado) -> 'BASE'
        for col in data.get("colunas", []):
            if col.get("tipo") == "coringa" and col.get("nome") == "Coringa":
                col["nome"] = "BASE"
        return data
    except Exception as e:
        print(f"[coringa] erro carregando temas.json: {e}")
        return {"colunas": [], "linhas": [], "celulas": {}}


def _salvar_temas(data: dict):
    with open(TEMAS_FILE, "w", encoding="utf-8") as f:
        json.dump(data, f, ensure_ascii=False, indent=2)


def _shift_celulas_coluna(celulas: dict, from_col: int, delta: int = 1) -> dict:
    """Reindexa keys '{ri}_{ci}' shiftando ci >= from_col por delta. Retorna novo dict."""
    novo = {}
    for k, v in celulas.items():
        try:
            ri, ci = k.split("_")
            ri, ci = int(ri), int(ci)
            if ci >= from_col:
                ci += delta
            novo[f"{ri}_{ci}"] = v
        except Exception:
            novo[k] = v
    return novo


def _shift_celulas_linha(celulas: dict, from_row: int, delta: int = 1) -> dict:
    """Reindexa keys '{ri}_{ci}' shiftando ri >= from_row por delta."""
    novo = {}
    for k, v in celulas.items():
        try:
            ri, ci = k.split("_")
            ri, ci = int(ri), int(ci)
            if ri >= from_row:
                ri += delta
            novo[f"{ri}_{ci}"] = v
        except Exception:
            novo[k] = v
    return novo


def garantir_coluna_coringa(temas: dict) -> int:
    """Garante coluna 'BASE' (tipo=coringa) na posicao idx 0 do grid.
    Idempotente: se ja existe na pos 0 com tipo=coringa, nao mexe."""
    colunas = temas.get("colunas", [])
    # Ja existe coluna coringa (em qq posicao)?
    for i, col in enumerate(colunas):
        nome_low = col.get("nome", "").lower()
        if (col.get("tipo") == "coringa") or nome_low in ("coringa", "base"):
            if i == 0:
                # garante tipo + nome novo
                col["tipo"] = "coringa"
                if col.get("nome") == "Coringa":
                    col["nome"] = "BASE"
                return 0
            # mover pra posicao 0
            col_coringa = colunas.pop(i)
            col_coringa["tipo"] = "coringa"
            if col_coringa.get("nome") == "Coringa":
                col_coringa["nome"] = "BASE"
            colunas.insert(0, col_coringa)
            # shift celulas: as colunas 0..i-1 viraram 1..i, e a antiga i virou 0
            # mais simples: criar mapa antigo->novo
            mapping = {0: 1}
            for j in range(1, i):
                mapping[j] = j + 1
            mapping[i] = 0
            for j in range(i + 1, len(colunas)):
                mapping[j] = j  # nao mudou
            celulas = temas.get("celulas", {})
            novo = {}
            for k, v in celulas.items():
                try:
                    ri, ci = k.split("_")
                    ri, ci = int(ri), int(ci)
                    novo_ci = mapping.get(ci, ci)
                    novo[f"{ri}_{novo_ci}"] = v
                except Exception:
                    novo[k] = v
            temas["celulas"] = novo
            return 0

    # Nao existe: cria nova na posicao 0
    col_nova = {
        "nome": "BASE",
        "tipo": "coringa",
        "pipeline_id": "",
        "template_id": "",
        "voice_id": "",
        "voice_provider": "",
        "coringa_recebe": False,
    }
    colunas.insert(0, col_nova)
    # Shift TODAS as celulas existentes: ci += 1
    temas["celulas"] = _shift_celulas_coluna(temas.get("celulas", {}), from_col=0, delta=1)
    return 0


def _achar_ou_criar_linha(temas: dict, data_str: str) -> int:
    """Acha linha pela data DD/MM/YYYY. Cria nova mantendo ordem cronologica. Retorna idx."""
    linhas = temas.get("linhas", [])
    for i, L in enumerate(linhas):
        if L.get("data") == data_str:
            return i

    # Inserir cronologicamente
    try:
        data_obj = datetime.strptime(data_str, "%d/%m/%Y").date()
    except Exception:
        # formato invalido: append no final
        linhas.append({"data": data_str})
        return len(linhas) - 1

    insert_at = len(linhas)
    for i, L in enumerate(linhas):
        try:
            d = datetime.strptime(L.get("data", ""), "%d/%m/%Y").date()
            if data_obj < d:
                insert_at = i
                break
        except Exception:
            continue

    linhas.insert(insert_at, {"data": data_str})
    # Shift celulas: ri >= insert_at recebe +1
    temas["celulas"] = _shift_celulas_linha(temas.get("celulas", {}), from_row=insert_at, delta=1)
    return insert_at


# ============= PROCESSAMENTO =============

def processar_item_geral(item: dict) -> dict:
    """Para 1 item do Backlog: garante coluna Coringa + linha da data + preenche Coringa.
    NAO marca geral=Ok aqui (decisao do caller). Retorna {'ok', 'erro?', 'row_idx', 'col_idx'}."""
    if not item.get("titulo") and not item.get("texto_thumb"):
        return {"ok": False, "erro": "Item sem titulo nem texto_thumb (esperando enrich)"}

    data_str = item.get("data", "")
    if not data_str:
        return {"ok": False, "erro": "Item sem data"}

    with _temas_lock:
        temas = _carregar_temas()
        col_idx = garantir_coluna_coringa(temas)
        row_idx = _achar_ou_criar_linha(temas, data_str)

        cel_key = f"{row_idx}_{col_idx}"
        cel = temas["celulas"].get(cel_key, {})
        # Preenche BASE - tema = fusao coerente de titulo + thumb (via Claude)
        titulo = item.get("titulo", "")
        thumb = item.get("texto_thumb", "")
        cel["titulo"] = titulo
        cel["thumb"] = thumb
        cel["tema"] = gerar_tema_fundido(titulo, thumb)
        cel["coringa_origem"] = item.get("id", "")
        cel["coringa_link"] = item.get("link", "")
        cel["coringa_criado_em"] = datetime.now().isoformat(timespec="seconds")
        # Garante que distribuicao seja re-executada se item Coringa for re-processado
        cel.pop("coringa_distribuido_em", None)
        temas["celulas"][cel_key] = cel

        _salvar_temas(temas)

    return {"ok": True, "row_idx": row_idx, "col_idx": col_idx, "data": data_str}


def _tentar_enrich(item: dict) -> dict:
    """Tenta enrich (oEmbed + OCR) se titulo OU texto_thumb estao vazios.
    Retorna o item atualizado (memoria + persistido)."""
    if item.get("titulo") and item.get("texto_thumb"):
        return item  # ja completo
    try:
        import videos_meta
        meta = videos_meta.enriquecer_video(item.get("link", ""), video_id=item.get("video_id", ""))
        campos = {}
        if not item.get("titulo") and meta.get("titulo"):
            campos["titulo"] = meta["titulo"]
        if not item.get("texto_thumb") and meta.get("texto_thumb"):
            campos["texto_thumb"] = meta["texto_thumb"]
        if campos:
            atualizado = backlog_temas_db.atualizar(item["id"], **campos)
            if isinstance(atualizado, dict) and "id" in atualizado:
                return atualizado
    except Exception as e:
        print(f"[coringa] falha enrich {item.get('id')}: {e}")
    return item


def processar_backlog_pendentes_geral() -> dict:
    """Roda 1 ciclo: pega items do Backlog com geral='', tenta enrich se preciso,
    cria linha + Coringa, marca geral=Ok. Retorna {'processados', 'erros', 'detalhes'}."""
    items = backlog_temas_db.listar(geral="")
    processados = 0
    erros = 0
    pendentes = 0
    detalhes = []

    for it in items:
        # Tenta enrich automatico se faltam campos
        it = _tentar_enrich(it)

        # Precisa pelo menos do titulo pra processar
        if not it.get("titulo"):
            pendentes += 1
            detalhes.append({"id": it["id"], "data": it["data"], "pendente": "sem titulo apos enrich"})
            continue

        try:
            res = processar_item_geral(it)
            if res.get("ok"):
                backlog_temas_db.atualizar(it["id"], geral="Ok")
                processados += 1
                detalhes.append({"id": it["id"], "data": it["data"], "ok": True})
            else:
                erros += 1
                detalhes.append({"id": it["id"], "data": it["data"], "erro": res.get("erro")})
        except Exception as e:
            erros += 1
            detalhes.append({"id": it["id"], "erro": str(e)})

    if processados or erros or pendentes:
        print(f"[coringa] ciclo: {processados} processados, {erros} erros, {pendentes} pendentes (sem titulo)")

    return {"processados": processados, "erros": erros, "pendentes": pendentes, "detalhes": detalhes}


# ============= CRON BACKGROUND =============

CORINGA_CRON_INTERVAL_SEC = 900  # 15 min
_cron_started = False
_cron_lock = threading.Lock()
_ultimo_ciclo: Optional[dict] = None  # {'ts', 'processados', 'erros'}


def iniciar_cron_coringa():
    """Inicia thread daemon que processa Backlog→Coringa periodicamente.
    Idempotente: chamadas multiplas sao ignoradas."""
    global _cron_started
    with _cron_lock:
        if _cron_started:
            return
        _cron_started = True

    def worker():
        # Pequeno warm-up pra app subir
        time.sleep(20)
        print(f"[coringa-cron] iniciado, intervalo={CORINGA_CRON_INTERVAL_SEC}s")
        global _ultimo_ciclo
        while True:
            try:
                if get_automacao_habilitada():
                    res = processar_backlog_pendentes_geral()
                    _ultimo_ciclo = {
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "processados": res["processados"],
                        "erros": res["erros"],
                    }
                # Quando desligada: dorme silenciosamente sem processar
            except Exception as e:
                print(f"[coringa-cron] erro: {e}")
            time.sleep(CORINGA_CRON_INTERVAL_SEC)

    threading.Thread(target=worker, daemon=True, name="coringa-cron").start()


def status_cron() -> dict:
    return {
        "rodando": _cron_started,
        "intervalo_seg": CORINGA_CRON_INTERVAL_SEC,
        "ultimo_ciclo": _ultimo_ciclo,
    }


# ============= CONFIG POR CANAL (Fase 2) =============

# Cada coluna nao-Coringa do temas.json pode ter:
#   coringa_recebe: bool        - se True, recebe distribuicao da Coringa
#   coringa_adaptado: bool      - se True, adapta via Claude CLI; se False, copia direto
#   vinculo_co_origem: str      - 'CO1'|'CO2'|'CO3'|'CO4'|'' - canal CO* fonte (pra NARC/NPD/etc)

CO_SLOTS = ("CO1", "CO2", "CO3", "CO4")


CASING_OPCOES = ("", "uppercase", "titlecase")  # "" = default (deixa agent decidir)


def get_canal_config(coluna: dict) -> dict:
    return {
        "nome": coluna.get("nome", ""),
        "tipo": coluna.get("tipo", ""),
        "idioma": coluna.get("idioma", ""),
        "recebe": bool(coluna.get("coringa_recebe", False)),
        "adaptado": bool(coluna.get("coringa_adaptado", True)),
        "casing": (coluna.get("coringa_casing", "") or "").lower(),  # ""|uppercase|titlecase
        "vinculo_co_origem": coluna.get("vinculo_co_origem", "") or "",
    }


def listar_config_canais() -> list:
    """Retorna config Coringa de todos os canais (exceto a propria Coringa)."""
    with _temas_lock:
        temas = _carregar_temas()
        return [
            get_canal_config(col)
            for col in temas.get("colunas", [])
            if col.get("tipo") != "coringa"
        ]


def atualizar_config_canal(nome: str, **campos) -> dict:
    """Atualiza coringa_recebe/coringa_adaptado/vinculo_co_origem de um canal.
    Retorna config atualizada ou {'erro': ...}."""
    permitidos = {
        "recebe": "coringa_recebe",
        "adaptado": "coringa_adaptado",
        "casing": "coringa_casing",
        "vinculo_co_origem": "vinculo_co_origem",
    }
    if "vinculo_co_origem" in campos and campos["vinculo_co_origem"]:
        v = str(campos["vinculo_co_origem"]).strip().upper()
        if v not in CO_SLOTS:
            return {"erro": f"vinculo_co_origem invalido: {v}. Use {CO_SLOTS} ou ''"}
        campos["vinculo_co_origem"] = v
    if "casing" in campos and campos["casing"] is not None:
        v = str(campos["casing"]).strip().lower()
        if v not in CASING_OPCOES:
            return {"erro": f"casing invalido: {v}. Use {CASING_OPCOES}"}
        campos["casing"] = v

    with _temas_lock:
        temas = _carregar_temas()
        for col in temas.get("colunas", []):
            if col.get("nome") == nome and col.get("tipo") != "coringa":
                for chave_in, chave_storage in permitidos.items():
                    if chave_in in campos and campos[chave_in] is not None:
                        if chave_in in ("recebe", "adaptado"):
                            col[chave_storage] = bool(campos[chave_in])
                        else:
                            col[chave_storage] = str(campos[chave_in] or "")
                _salvar_temas(temas)
                return get_canal_config(col)
        return {"erro": f"Canal '{nome}' nao encontrado"}


# ============= ADAPTACAO VIA CLAUDE CLI / API =============

AGENT_TITULOS_PATH = BASE_DIR / "agents" / "titulos" / "CLAUDE.md"
DIST_DELAY_MIN = 30  # min entre criacao BASE e distribuicao automatica
DIST_CRON_INTERVAL_SEC = 900  # 15 min


def gerar_tema_fundido(titulo: str, thumb: str) -> str:
    """Concatenacao literal: titulo + '. ' + thumb. Sem LLM, sem adaptacao."""
    titulo = (titulo or "").strip()
    thumb = (thumb or "").strip()
    if not titulo and not thumb:
        return ""
    if not titulo:
        return thumb
    if not thumb:
        return titulo
    return f"{titulo}. {thumb}"


def _carregar_instrucoes_titulos() -> str:
    try:
        return AGENT_TITULOS_PATH.read_text(encoding="utf-8")
    except Exception as e:
        print(f"[coringa] falha lendo agents/titulos/CLAUDE.md: {e}")
        return ""


def _parse_json_resposta(texto: str) -> Optional[dict]:
    """Tenta parsear JSON da resposta do LLM. Tolera markdown fences e texto antes/depois."""
    if not texto:
        return None
    t = texto.strip()
    # Remove fences ```json ... ``` ou ``` ... ```
    t = re.sub(r'^```(?:json|JSON)?\s*\n?', '', t)
    t = re.sub(r'\n?```\s*$', '', t)
    inicio = t.find('{')
    fim = t.rfind('}')
    if inicio == -1 or fim == -1 or fim < inicio:
        return None
    try:
        return json.loads(t[inicio:fim + 1])
    except Exception:
        return None


def _chamar_llm_com_fallback(system_msg: str, user_msg: str, contexto_log: str = "") -> dict:
    """Helper: tenta claude_cli -> claude API -> gemini. Retorna {ok, raw, provider_usado, erro?}."""
    import scriptwriter

    resultado_raw = ""
    provider_usado = ""
    erro_msgs = []

    try:
        resultado_raw = scriptwriter._chamar_claude_cli(system_msg, user_msg, "local-cli", "sonnet")
        provider_usado = "claude_cli"
    except Exception as e:
        erro_msgs.append(f"claude_cli: {str(e)[:120]}")

    if not resultado_raw:
        try:
            cred = next((c for c in scriptwriter.carregar_credenciais()
                         if c.get("provedor") == "claude" and c.get("status") == "ok"), None)
            if cred:
                resultado_raw = scriptwriter._chamar_claude(
                    system_msg, user_msg, cred.get("api_key", ""), "claude-sonnet-4-6")
                provider_usado = "claude_api"
            else:
                erro_msgs.append("claude_api: sem credencial")
        except Exception as e:
            erro_msgs.append(f"claude_api: {str(e)[:120]}")

    if not resultado_raw:
        try:
            cred = next((c for c in scriptwriter.carregar_credenciais()
                         if c.get("provedor") == "gemini" and c.get("status") == "ok"), None)
            if cred:
                resultado_raw = scriptwriter._chamar_gemini(
                    system_msg, user_msg, cred.get("api_key", ""), "gemini-2.5-flash")
                provider_usado = "gemini"
        except Exception as e:
            erro_msgs.append(f"gemini: {str(e)[:120]}")

    if not resultado_raw:
        return {"ok": False, "erro": f"[{contexto_log}] todos providers falharam: " + "; ".join(erro_msgs)}
    return {"ok": True, "raw": resultado_raw, "provider_usado": provider_usado}


# ============= ADAPTACOES CUSTOM (NARC, NPD) =============

NARC_INSTRUCOES = """You adapt YouTube video content from a CO* (Chosen One English) channel to the NARC channel format.

NARC CHANNEL RULES (different from the agents/titulos rules):

1. NICHE TERM REPLACEMENTS (Arcturian style):
   - "Chosen One" / "Chosen Ones" → "Starseed" / "Starseeds"
   - "God" → "The Source"
   - "Angels" / "Heaven" → "High Beings"
   - "Demons" / "Devil" → "Shadow Forces"
   - "Earth" → "Gaia"
   - Identity call at start (if present): use "Starseed," (singular, with comma)

2. CASING: **Title Case** — first letter of each significant word capitalized.
   NOT ALL CAPS. Articles/prepositions stay lowercase (a, an, the, of, to, in, on, with, etc.) UNLESS they are the first word.

3. SUFFIX: Append " | Arcturians" at the very end of the title (single space, pipe, single space).

4. KEEP the original meaning, length and impact. Adapt only terminology, casing, and add suffix.

5. The thumb headline (short text on thumbnail) follows same Title Case rules but WITHOUT the suffix.

OUTPUT (JSON only, no markdown fences, no commentary):
{"titulo": "...", "thumb": "..."}
- titulo: full Title Case title with " | Arcturians" appended
- thumb: thumbnail headline in Title Case, max ~60 chars

Output starts with { and ends with }."""

NPD_INSTRUCOES = """You adapt YouTube video content from a CO* (Chosen One English) channel to the NPD channel format.

NPD CHANNEL RULES (different from the agents/titulos rules):

1. NICHE TERM REPLACEMENTS (Pleiadian style):
   - "Chosen One" / "Chosen Ones" → "Starseed" / "Starseeds"
   - "God" → "The Source"
   - "Angels" / "Heaven" → "The Council"
   - "Demons" / "Devil" → "Shadow Forces"
   - "Earth" → "Gaia"
   - Identity call at start (if present): use "Starseed," (singular, with comma)

2. CASING: **Title Case** — first letter of each significant word capitalized.
   NOT ALL CAPS. Articles/prepositions stay lowercase (a, an, the, of, to, in, on, with, etc.) UNLESS they are the first word.

3. SUFFIX: Append " | Pleiadians" at the very end of the title (single space, pipe, single space).

4. KEEP the original meaning, length and impact. Adapt only terminology, casing, and add suffix.

5. The thumb headline follows same Title Case rules but WITHOUT the suffix.

OUTPUT (JSON only, no markdown fences, no commentary):
{"titulo": "...", "thumb": "..."}
- titulo: full Title Case title with " | Pleiadians" appended
- thumb: thumbnail headline in Title Case, max ~60 chars

Output starts with { and ends with }."""


def _adaptar_custom(tema: str, titulo: str, thumb: str,
                     instrucoes: str, canal_alvo: str, fonte: str = "CO") -> dict:
    """Adapta com instrucoes custom (NARC ou NPD), partindo de conteudo CO*."""
    user_msg = (
        f"Adapt this {fonte} content to {canal_alvo}:\n\n"
        f"{fonte} tema:   {tema}\n"
        f"{fonte} titulo: {titulo}\n"
        f"{fonte} thumb:  {thumb}\n\n"
        f"Return ONLY the JSON object {{tema, titulo, thumb}}."
    )
    res = _chamar_llm_com_fallback(instrucoes, user_msg, contexto_log=canal_alvo)
    if not res.get("ok"):
        return res

    parsed = _parse_json_resposta(res["raw"])
    if not parsed:
        print(f"[coringa] parse falhou pra {canal_alvo}. Raw: {res['raw'][:500]!r}")
        return {"ok": False, "erro": "Resposta nao parseavel como JSON",
                "raw": res["raw"][:300], "provider_usado": res["provider_usado"]}

    titulo_out = (parsed.get("titulo") or "").strip()
    if not titulo_out:
        return {"ok": False, "erro": f"Adaptacao {canal_alvo} retornou titulo vazio",
                "raw": res["raw"][:500], "provider_usado": res["provider_usado"]}

    thumb_out = (parsed.get("thumb") or "").strip()
    return {
        "ok": True,
        "tema": gerar_tema_fundido(titulo_out, thumb_out),  # concatenacao literal
        "titulo": titulo_out,
        "thumb": thumb_out,
        "provider_usado": res["provider_usado"],
    }


def adaptar_narc(tema: str, titulo: str, thumb: str) -> dict:
    """CO1 -> NARC: Arcturian-style + Title Case + ' | Arcturians' sufixo."""
    return _adaptar_custom(tema, titulo, thumb, NARC_INSTRUCOES, "NARC", "CO1")


def adaptar_npd(tema: str, titulo: str, thumb: str) -> dict:
    """CO2 -> NPD: Pleiadian-style + Title Case + ' | Pleiadians' sufixo."""
    return _adaptar_custom(tema, titulo, thumb, NPD_INSTRUCOES, "NPD", "CO2")


def adaptar_via_claude(
    tema: str, titulo: str, thumb: str,
    canal_alvo: str, idioma: str = "",
    casing: str = "",  # ""|"uppercase"|"titlecase" - override de casing
    timeout_s: int = 120,
) -> dict:
    """Adapta conteudo Coringa pro canal alvo usando agents/titulos/CLAUDE.md.

    Tenta: 1. Claude CLI (Max plan, $0)  2. Claude API  3. Gemini.

    Retorna {'ok': bool, 'tema', 'titulo', 'thumb', 'provider_usado', 'erro'?, 'raw'?}.
    """
    instrucoes = _carregar_instrucoes_titulos()
    if not instrucoes:
        return {"ok": False, "erro": "agents/titulos/CLAUDE.md nao pode ser lido"}

    casing_instr = ""
    if casing == "uppercase":
        casing_instr = (
            "\n\n=== CASING OVERRIDE (highest priority) ===\n"
            "For the 'titulo' field, use ALL CAPS (every letter uppercase).\n"
            "For the 'thumb' field, also use ALL CAPS.\n"
            "This overrides any casing rule in your channel instructions. "
            "Do NOT mix Title Case with ALL CAPS — output ONE casing only: ALL CAPS.\n"
        )
    elif casing == "titlecase":
        casing_instr = (
            "\n\n=== CASING OVERRIDE (highest priority) ===\n"
            "For the 'titulo' field, use Title Case (first letter of each significant word "
            "capitalized; articles/prepositions like 'a', 'an', 'the', 'of', 'to', 'in', 'on' "
            "stay lowercase unless they are the first word).\n"
            "For the 'thumb' field, also use Title Case.\n"
            "This overrides any casing rule in your channel instructions. "
            "Do NOT use ALL CAPS — output ONE casing only: Title Case.\n"
        )

    system_msg = (
        instrucoes +
        casing_instr +
        "\n\n=== STRICT OUTPUT FORMAT (override) ===\n"
        f"The target channel is '{canal_alvo}'. Apply the rules defined in your instructions "
        f"for that channel (DE, EN, EN2, EN3, ENO2, or ENS). "
        f"If the channel name is not in your instructions, apply the closest match by intent.\n\n"
        "Return ONLY a single JSON object. NO markdown fences, NO commentary, NO dual-format output. "
        "Pick exactly ONE casing variant and output it.\n\n"
        'Format: {"titulo": "...", "thumb": "..."}\n'
        "- titulo: full YouTube title (single string, ONE casing per channel rules)\n"
        "- thumb: thumbnail headline text (single string, max ~60 chars)\n\n"
        "Output starts with { and ends with }."
    )

    user_msg = (
        f"Adapt the Coringa BASE content for channel **{canal_alvo}**"
        + (f" (language/style hint: {idioma})" if idioma else "") + ":\n\n"
        f"BASE tema:   {tema}\n"
        f"BASE titulo: {titulo}\n"
        f"BASE thumb:  {thumb}\n\n"
        f"Apply the channel-specific terminology, identity calls, suffixes, language. "
        f"Return ONLY the JSON object {{tema, titulo, thumb}}."
    )

    res = _chamar_llm_com_fallback(system_msg, user_msg, contexto_log=canal_alvo)
    if not res.get("ok"):
        return res
    resultado_raw = res["raw"]
    provider_usado = res["provider_usado"]

    parsed = _parse_json_resposta(resultado_raw)
    if not parsed:
        print(f"[coringa] parse falhou pra {canal_alvo}. Raw: {resultado_raw[:500]!r}")
        return {"ok": False, "erro": "Resposta nao parseavel como JSON",
                "raw": resultado_raw[:300], "provider_usado": provider_usado}

    titulo_out = (parsed.get("titulo") or "").strip()
    thumb_out = (parsed.get("thumb") or "").strip()

    # Validacao: pelo menos titulo precisa estar preenchido
    if not titulo_out:
        print(f"[coringa] adaptacao retornou titulo vazio pra '{canal_alvo}'. "
              f"O agent provavelmente nao tem regras pra esse canal. Raw: {resultado_raw[:300]!r}")
        return {"ok": False,
                "erro": f"Adaptacao retornou titulo vazio - agents/titulos/CLAUDE.md pode nao ter regras pra canal '{canal_alvo}'",
                "raw": resultado_raw[:500], "provider_usado": provider_usado}

    return {
        "ok": True,
        "tema": gerar_tema_fundido(titulo_out, thumb_out),  # concatenacao literal
        "titulo": titulo_out,
        "thumb": thumb_out,
        "provider_usado": provider_usado,
    }


# ============= DISTRIBUICAO POR LINHA (Fase 2) =============

def _e_canal_co(nome: str) -> bool:
    """Detecta nome CO1/CO2/CO3/CO4 etc."""
    return bool(re.match(r'^CO\d', nome or ''))


def distribuir_linha_coringa(
    row_idx: int, ignorar_delay: bool = False,
    delay_min: int = DIST_DELAY_MIN,
) -> dict:
    """Distribui Coringa de UMA linha pros canais Geral configurados (recebe=true,
    sem vinculo_co_origem, nao CO*). NARC/NPD/CO* nao sao tocados aqui (Fase 3).

    Retorna {'ok': bool, 'distribuidos':[], 'pulados':[], 'erros':[]} ou {'erro': '...'}.
    """
    with _temas_lock:
        temas = _carregar_temas()
        colunas = temas.get("colunas", [])
        linhas = temas.get("linhas", [])
        celulas = temas.get("celulas", {})

        if row_idx >= len(linhas):
            return {"erro": f"row_idx {row_idx} fora do range ({len(linhas)} linhas)"}

        coringa_idx = next(
            (i for i, c in enumerate(colunas) if c.get("tipo") == "coringa"), None
        )
        if coringa_idx is None:
            return {"erro": "Coluna Coringa nao encontrada"}

        cel_coringa = celulas.get(f"{row_idx}_{coringa_idx}", {})
        if not cel_coringa.get("titulo") and not cel_coringa.get("tema"):
            return {"erro": "Coringa vazia (sem titulo/tema) nessa linha"}

        # Verifica delay (a menos que ignorar_delay)
        if not ignorar_delay:
            criado = cel_coringa.get("coringa_criado_em")
            if criado:
                try:
                    t = datetime.fromisoformat(criado)
                    secs = (datetime.now() - t).total_seconds()
                    if secs < delay_min * 60:
                        return {"erro": f"Aguardando delay de {delay_min}min ({int((delay_min*60-secs)//60)}min restantes)"}
                except Exception:
                    pass

    # Itera canais que recebem (fora do lock pra nao bloquear durante chamadas LLM)
    candidatos = []
    for ci, col in enumerate(colunas):
        if col.get("tipo") == "coringa":
            continue
        nome = col.get("nome", "")
        if not col.get("coringa_recebe"):
            continue
        if col.get("vinculo_co_origem"):
            continue  # NARC/NPD - recebem via CO* na Fase 3
        if _e_canal_co(nome):
            continue  # CO* - Fase 3
        candidatos.append((ci, col))

    distribuidos = []
    pulados = []
    erros = []
    resultados_canal = {}  # ci -> {tema, titulo, thumb, adaptado, provider}

    for ci, col in candidatos:
        nome = col.get("nome", "")
        # PRESERVA dados existentes: skip total se ja tem todos os 3 campos preenchidos.
        # Economiza chamada LLM (custo + tempo) e nao sobrescreve trabalho manual do user.
        cel_atual = celulas.get(f"{row_idx}_{ci}", {})
        ja_tem_tema = bool((cel_atual.get("tema") or "").strip())
        ja_tem_titulo = bool((cel_atual.get("titulo") or "").strip())
        ja_tem_thumb = bool((cel_atual.get("thumb") or "").strip())
        if ja_tem_tema and ja_tem_titulo and ja_tem_thumb:
            pulados.append({"canal": nome, "motivo": "ja preenchido (3/3 campos)"})
            continue

        adaptar = bool(col.get("coringa_adaptado", True))
        if adaptar:
            res = adaptar_via_claude(
                cel_coringa.get("tema", ""),
                cel_coringa.get("titulo", ""),
                cel_coringa.get("thumb", ""),
                canal_alvo=nome,
                idioma=col.get("idioma", ""),
                casing=(col.get("coringa_casing", "") or "").lower(),
            )
            if not res.get("ok"):
                erros.append({"canal": nome, "erro": res.get("erro", "?")})
                continue
            resultados_canal[ci] = {
                "tema": res["tema"], "titulo": res["titulo"], "thumb": res["thumb"],
                "adaptado": True, "provider": res.get("provider_usado", ""),
                "ja_tem": (ja_tem_tema, ja_tem_titulo, ja_tem_thumb),
            }
        else:
            resultados_canal[ci] = {
                "tema": cel_coringa.get("tema", ""),
                "titulo": cel_coringa.get("titulo", ""),
                "thumb": cel_coringa.get("thumb", ""),
                "adaptado": False, "provider": "direto",
                "ja_tem": (ja_tem_tema, ja_tem_titulo, ja_tem_thumb),
            }

    # Re-pega lock pra escrever resultados de uma vez
    with _temas_lock:
        temas = _carregar_temas()
        celulas = temas.get("celulas", {})
        cel_coringa = celulas.get(f"{row_idx}_{coringa_idx}", {})

        for ci, dados in resultados_canal.items():
            cel_destino = celulas.get(f"{row_idx}_{ci}", {})
            ja_tem_tema, ja_tem_titulo, ja_tem_thumb = dados.get("ja_tem", (False, False, False))
            preenchidos = []
            # PRESERVA campos ja preenchidos — so seta o que estava vazio
            if not ja_tem_tema and dados.get("tema"):
                cel_destino["tema"] = dados["tema"]; preenchidos.append("tema")
            if not ja_tem_titulo and dados.get("titulo"):
                cel_destino["titulo"] = dados["titulo"]; preenchidos.append("titulo")
            if not ja_tem_thumb and dados.get("thumb"):
                cel_destino["thumb"] = dados["thumb"]; preenchidos.append("thumb")
            cel_destino["coringa_origem_id"] = cel_coringa.get("coringa_origem", "")
            cel_destino["coringa_distribuido_em"] = datetime.now().isoformat(timespec="seconds")
            celulas[f"{row_idx}_{ci}"] = cel_destino
            distribuidos.append({
                "canal": colunas[ci].get("nome"),
                "adaptado": dados["adaptado"],
                "provider": dados["provider"],
                "preenchidos": preenchidos,  # quais campos foram setados (vazios -> agora cheios)
                "preservados": [k for k, v in [("tema", ja_tem_tema), ("titulo", ja_tem_titulo), ("thumb", ja_tem_thumb)] if v],
            })

        cel_coringa["coringa_distribuido_em"] = datetime.now().isoformat(timespec="seconds")
        cel_coringa["coringa_distribuido_count"] = len(distribuidos)
        celulas[f"{row_idx}_{coringa_idx}"] = cel_coringa

        _salvar_temas(temas)

    return {"ok": True, "distribuidos": distribuidos, "pulados": pulados, "erros": erros,
            "row_idx": row_idx, "data": linhas[row_idx].get("data")}


# ============= CRON DISTRIBUICAO =============

_ultimo_ciclo_dist: Optional[dict] = None
_dist_started = False
_dist_lock_thread = threading.Lock()


def processar_distribuicao_pendentes(ignorar_delay: bool = False) -> dict:
    """Roda 1 ciclo: pega linhas Coringa com BASE preenchida + delay expirado +
    nao distribuidas, distribui pros canais Geral configurados."""
    with _temas_lock:
        temas = _carregar_temas()
        colunas = temas.get("colunas", [])
        celulas = temas.get("celulas", {})
        coringa_idx = next(
            (i for i, c in enumerate(colunas) if c.get("tipo") == "coringa"), None
        )
        if coringa_idx is None:
            return {"erro": "Coluna Coringa nao encontrada"}

        candidatos = []
        for k, v in celulas.items():
            try:
                ri, ci = k.split("_")
                ri, ci = int(ri), int(ci)
            except Exception:
                continue
            if ci != coringa_idx:
                continue
            if v.get("coringa_distribuido_em"):
                continue
            if not v.get("titulo") and not v.get("tema"):
                continue
            if not ignorar_delay:
                criado = v.get("coringa_criado_em")
                if criado:
                    try:
                        t = datetime.fromisoformat(criado)
                        if (datetime.now() - t).total_seconds() < DIST_DELAY_MIN * 60:
                            continue
                    except Exception:
                        pass
            candidatos.append(ri)

    distribuidos_total = 0
    erros_total = 0
    detalhes = []
    for ri in sorted(candidatos):
        res = distribuir_linha_coringa(ri, ignorar_delay=ignorar_delay)
        if "erro" in res and not res.get("ok"):
            erros_total += 1
            detalhes.append({"row_idx": ri, "erro": res["erro"]})
        else:
            distribuidos_total += len(res.get("distribuidos", []))
            erros_total += len(res.get("erros", []))
            detalhes.append({
                "row_idx": ri,
                "data": res.get("data"),
                "distribuidos": len(res.get("distribuidos", [])),
                "erros_canais": len(res.get("erros", [])),
            })

    if candidatos:
        print(f"[coringa-dist] {len(candidatos)} linhas: {distribuidos_total} canais distribuidos, {erros_total} erros")
    return {"linhas": len(candidatos), "distribuidos": distribuidos_total,
            "erros": erros_total, "detalhes": detalhes}


def iniciar_cron_distribuicao():
    """Cron worker que roda distribuicao a cada DIST_CRON_INTERVAL_SEC.
    Independente do cron BASE - roda em paralelo com offset."""
    global _dist_started
    with _dist_lock_thread:
        if _dist_started:
            return
        _dist_started = True

    def worker():
        # Offset 60s do cron BASE (que dorme 20s no warmup)
        time.sleep(80)
        print(f"[coringa-dist-cron] iniciado, intervalo={DIST_CRON_INTERVAL_SEC}s, delay={DIST_DELAY_MIN}min")
        global _ultimo_ciclo_dist
        while True:
            try:
                if get_automacao_habilitada():
                    res = processar_distribuicao_pendentes(ignorar_delay=False)
                    _ultimo_ciclo_dist = {
                        "ts": datetime.now().isoformat(timespec="seconds"),
                        "linhas": res.get("linhas", 0),
                        "distribuidos": res.get("distribuidos", 0),
                        "erros": res.get("erros", 0),
                    }
                # Quando desligada: dorme silenciosamente sem processar
            except Exception as e:
                print(f"[coringa-dist-cron] erro: {e}")
            time.sleep(DIST_CRON_INTERVAL_SEC)

    threading.Thread(target=worker, daemon=True, name="coringa-dist-cron").start()


def status_cron_dist() -> dict:
    return {
        "rodando": _dist_started,
        "intervalo_seg": DIST_CRON_INTERVAL_SEC,
        "delay_min": DIST_DELAY_MIN,
        "ultimo_ciclo": _ultimo_ciclo_dist,
    }


# ============= FASE 3: CO* EM CRUZ + NARC/NPD =============
#
# Padrao de preenchimento (4 items consecutivos do Backlog com co=''):
#   Dia X:    CO1=A, CO2=B, CO3=C, CO4=D, NARC=adapt(A), NPD=adapt(B)
#   Dia X+1:  CO1=C, CO2=D, CO3=A, CO4=B, NARC=adapt(C), NPD=adapt(D)
# Onde A,B,C,D sao os 4 items mais antigos do Backlog com co=''.
# Dia X = primeira linha do grid Temas com CO1 vazio.
# Dia X+1 = data Dia X + 1 dia (cria linha se nao existir).
# Apos preencher, marca os 4 items com co='Ok'.

def _item_to_cell(item: dict) -> dict:
    """Converte item Backlog em celula CO* (copia direta)."""
    titulo = item.get("titulo", "") or ""
    thumb = item.get("texto_thumb", "") or ""
    return {
        "titulo": titulo,
        "thumb": thumb,
        "tema": gerar_tema_fundido(titulo, thumb),
        "coringa_origem_id": item.get("id", ""),
        "coringa_link": item.get("link", ""),
        "coringa_distribuido_em": datetime.now().isoformat(timespec="seconds"),
    }


def processar_co_em_cruz() -> dict:
    """Processa CO* em cruz: pega 4 items mais antigos do Backlog com co='',
    preenche CO1/CO2/CO3/CO4 em 2 datas + cascade NARC (de CO1) + NPD (de CO2).

    Retorna {ok, dia_x, dia_x1, items_consumidos, distribuidos, erros}.
    """
    items = backlog_temas_db.listar(co="")
    items_validos = [it for it in items if it.get("titulo")]

    if len(items_validos) < 4:
        return {"ok": False, "erro": f"Precisa de 4 items Backlog com co='' e titulo. Tem {len(items_validos)}."}

    A, B, C, D = items_validos[:4]

    # === Lock 1: identificar/criar Dia X e Dia X+1, preencher CO* (copia direta) ===
    with _temas_lock:
        temas = _carregar_temas()
        colunas = temas.get("colunas", [])
        celulas = temas.setdefault("celulas", {})
        linhas = temas.setdefault("linhas", [])

        canal_idx = {}
        for i, col in enumerate(colunas):
            nome = col.get("nome", "")
            if nome in ("CO1", "CO2", "CO3", "CO4", "NARC", "NPD"):
                canal_idx[nome] = i

        faltando = [c for c in ("CO1", "CO2", "CO3", "CO4", "NARC", "NPD") if c not in canal_idx]
        if faltando:
            return {"ok": False, "erro": f"Canais faltando no grid: {faltando}"}

        # Achar Dia X: primeira linha com CO1 vazio
        dia_x = None
        for ri in range(len(linhas)):
            cel_co1 = celulas.get(f"{ri}_{canal_idx['CO1']}", {})
            if not cel_co1.get("titulo"):
                dia_x = ri
                break

        if dia_x is None:
            # Cria nova data: ultima_data + 1 dia (ou hoje se vazio)
            from datetime import date, timedelta
            ultima_data = None
            for L in linhas:
                try:
                    d = datetime.strptime(L.get("data", ""), "%d/%m/%Y").date()
                    if ultima_data is None or d > ultima_data:
                        ultima_data = d
                except Exception:
                    continue
            if ultima_data is None:
                ultima_data = date.today()
            dia_x_data = (ultima_data + timedelta(days=1)).strftime("%d/%m/%Y")
            dia_x = _achar_ou_criar_linha(temas, dia_x_data)
            celulas = temas.get("celulas", {})
            linhas = temas.get("linhas", [])

        dia_x_data_str = linhas[dia_x].get("data", "")
        try:
            from datetime import timedelta
            dx = datetime.strptime(dia_x_data_str, "%d/%m/%Y").date()
            dia_x1_data = (dx + timedelta(days=1)).strftime("%d/%m/%Y")
        except Exception:
            return {"ok": False, "erro": f"Data Dia X invalida: {dia_x_data_str!r}"}

        dia_x1 = _achar_ou_criar_linha(temas, dia_x1_data)
        celulas = temas.get("celulas", {})
        linhas = temas.get("linhas", [])
        # _achar_ou_criar_linha pode ter shiftado linhas; re-localizar dia_x
        for ri, L in enumerate(linhas):
            if L.get("data") == dia_x_data_str:
                dia_x = ri
                break
        for ri, L in enumerate(linhas):
            if L.get("data") == dia_x1_data:
                dia_x1 = ri
                break

        # Preencher CO* em cruz (copia direta) — PRESERVA campos ja preenchidos
        def _merge_preservando(key, item):
            atual = celulas.get(key, {}) or {}
            ja = (
                bool((atual.get("tema") or "").strip())
                and bool((atual.get("titulo") or "").strip())
                and bool((atual.get("thumb") or "").strip())
            )
            if ja:
                return  # ja completo — nao toca
            novo = _item_to_cell(item)
            # so sobrescreve campo a campo se atual estiver vazio
            for k in ("tema", "titulo", "thumb"):
                if not (atual.get(k) or "").strip() and novo.get(k):
                    atual[k] = novo[k]
            # campos auxiliares (vinculo_co_origem etc) sempre atualiza
            for k in novo:
                if k not in ("tema", "titulo", "thumb"):
                    atual[k] = novo[k]
            celulas[key] = atual

        # Dia X
        _merge_preservando(f"{dia_x}_{canal_idx['CO1']}", A)
        _merge_preservando(f"{dia_x}_{canal_idx['CO2']}", B)
        _merge_preservando(f"{dia_x}_{canal_idx['CO3']}", C)
        _merge_preservando(f"{dia_x}_{canal_idx['CO4']}", D)
        # Dia X+1 (rotacao)
        _merge_preservando(f"{dia_x1}_{canal_idx['CO1']}", C)
        _merge_preservando(f"{dia_x1}_{canal_idx['CO2']}", D)
        _merge_preservando(f"{dia_x1}_{canal_idx['CO3']}", A)
        _merge_preservando(f"{dia_x1}_{canal_idx['CO4']}", B)

        _salvar_temas(temas)

    print(f"[coringa-co] CO* preenchido em cruz: Dia X={dia_x_data_str} (row {dia_x}), Dia X+1={dia_x1_data} (row {dia_x1})")

    # === Fora do lock: adaptar NARC e NPD via Claude (4 chamadas) ===
    cascades = [
        (dia_x, "NARC", A),
        (dia_x1, "NARC", C),
        (dia_x, "NPD", B),
        (dia_x1, "NPD", D),
    ]

    cascade_results = {}
    erros_cascade = []
    for ri, canal, item in cascades:
        titulo = item.get("titulo", "")
        thumb = item.get("texto_thumb", "")
        if canal == "NARC":
            res = adaptar_narc(titulo, titulo, thumb)
        else:
            res = adaptar_npd(titulo, titulo, thumb)
        if not res.get("ok"):
            erros_cascade.append({"canal": canal, "row": ri, "erro": res.get("erro")})
            print(f"[coringa-co] {canal} row {ri} ERRO: {res.get('erro')}")
            continue
        cascade_results[(ri, canal_idx[canal])] = res
        print(f"[coringa-co] {canal} row {ri} OK ({res.get('provider_usado')})")

    # === Lock 2: gravar NARC/NPD adaptados ===
    with _temas_lock:
        temas = _carregar_temas()
        celulas = temas.setdefault("celulas", {})
        for (ri, ci), res in cascade_results.items():
            celulas[f"{ri}_{ci}"] = {
                "titulo": res["titulo"],
                "thumb": res["thumb"],
                "tema": res["tema"],
                "coringa_distribuido_em": datetime.now().isoformat(timespec="seconds"),
            }
        _salvar_temas(temas)

    # === Marcar 4 items co=Ok ===
    for it in (A, B, C, D):
        backlog_temas_db.atualizar(it["id"], co="Ok")

    return {
        "ok": True,
        "dia_x": dia_x_data_str,
        "dia_x1": dia_x1_data,
        "items_consumidos": [
            {"id": A["id"], "slot": "CO1 (=CO3 dia+1, NARC dia X)"},
            {"id": B["id"], "slot": "CO2 (=CO4 dia+1, NPD dia X)"},
            {"id": C["id"], "slot": "CO3 (=CO1 dia+1, NARC dia X+1)"},
            {"id": D["id"], "slot": "CO4 (=CO2 dia+1, NPD dia X+1)"},
        ],
        "cascade_ok": len(cascade_results),
        "cascade_erros": erros_cascade,
    }
