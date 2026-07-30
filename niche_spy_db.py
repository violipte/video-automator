"""Niche Spy — acesso ao Supabase (canais espionados, templates de pesquisa, cache, quota).

Armazena no Supabase do FlowLink (projeto ctmhpvuixgmuomdcvccq), tabelas `niche_*`.

CONFIG (config.json) — chaves DEDICADAS de propósito:
    "niche_supabase_url": "https://ctmhpvuixgmuomdcvccq.supabase.co"
    "niche_supabase_key": "<service_role key>"
    "youtube_api_keys":  [{"id": "EN", "key": "AIza..."}, ...]   # opcional (enriquecimento)

⚠️ NÃO usar as chaves genéricas `supabase_url`/`supabase_key` do config: o app.py
(POST /api/temas) dispara um sync_supabase dormente quando elas existem — preenchê-las
ativaria escrita inesperada a cada save do grid.

Custo de quota do YouTube (importante): channels.list = 1 unidade | search.list = 100.
Enriquecer um canal é barato; BUSCAR é caro (por isso cache + contador em niche_api_usage).
"""
import json
import re
from datetime import date
from pathlib import Path

import httpx

BASE_DIR = Path(__file__).parent
CONFIG_FILE = BASE_DIR / "config.json"
YT_API = "https://www.googleapis.com/youtube/v3"
_TIMEOUT = 20.0


# ---------------- config ----------------
def _cfg() -> dict:
    try:
        return json.load(open(CONFIG_FILE, encoding="utf-8"))
    except Exception:
        return {}


def _sb():
    """(url, key) do Supabase do Niche Spy. Levanta se não configurado."""
    c = _cfg()
    url = (c.get("niche_supabase_url") or "").rstrip("/")
    key = c.get("niche_supabase_key") or ""
    if not url or not key:
        raise RuntimeError("niche_supabase_url/niche_supabase_key ausentes no config.json")
    return url, key


def _headers(extra=None):
    _, key = _sb()
    h = {"apikey": key, "Authorization": f"Bearer {key}", "Content-Type": "application/json"}
    if extra:
        h.update(extra)
    return h


def _rest(method: str, tabela: str, **kw):
    url, _ = _sb()
    with httpx.Client(timeout=_TIMEOUT) as cli:
        r = cli.request(method, f"{url}/rest/v1/{tabela}", headers=_headers(kw.pop("extra_headers", None)), **kw)
    r.raise_for_status()
    return r.json() if r.content and r.headers.get("content-type", "").startswith("application/json") else []


def configurado() -> bool:
    try:
        _sb()
        return True
    except Exception:
        return False


# ---------------- canais ----------------
def listar_canais(tier=None, nicho=None, favorito=None, limit=500):
    params = {"select": "*", "order": "criado_em.desc", "limit": str(limit)}
    if tier:
        params["tier"] = f"eq.{tier}"
    if nicho:
        params["nicho"] = f"eq.{nicho}"
    if favorito is not None:
        params["favorito"] = f"eq.{'true' if favorito else 'false'}"
    return _rest("GET", "niche_channels", params=params)


def salvar_canal(dados: dict):
    """Upsert por channel_id. Retorna a linha salva."""
    dados = {k: v for k, v in dados.items() if v is not None}
    dados.pop("id", None)
    out = _rest("POST", "niche_channels", json=dados, params={"on_conflict": "channel_id"},
                extra_headers={"Prefer": "resolution=merge-duplicates,return=representation"})
    return out[0] if isinstance(out, list) and out else out


def atualizar_canal(canal_id: str, campos: dict):
    campos = dict(campos)
    campos["atualizado_em"] = "now()"
    out = _rest("PATCH", "niche_channels", json=campos, params={"id": f"eq.{canal_id}"},
                extra_headers={"Prefer": "return=representation"})
    return out[0] if isinstance(out, list) and out else out


def remover_canal(canal_id: str):
    _rest("DELETE", "niche_channels", params={"id": f"eq.{canal_id}"})
    return True


# ---------------- templates de pesquisa ----------------
def listar_templates():
    return _rest("GET", "niche_search_templates", params={"select": "*", "order": "criado_em.desc"})


def salvar_template(nome: str, criterios: dict, descricao: str = None):
    out = _rest("POST", "niche_search_templates",
                json={"nome": nome, "criterios": criterios, "descricao": descricao},
                extra_headers={"Prefer": "return=representation"})
    return out[0] if isinstance(out, list) and out else out


def remover_template(tpl_id: str):
    _rest("DELETE", "niche_search_templates", params={"id": f"eq.{tpl_id}"})
    return True


# ---------------- quota (protege o Link Tracker de estourar) ----------------
def registrar_uso(key_id: str, unidades: int):
    """Soma unidades gastas hoje por essa key. Best-effort (não derruba a request)."""
    hoje = date.today().isoformat()
    try:
        atual = _rest("GET", "niche_api_usage", params={
            "select": "id,unidades", "key_id": f"eq.{key_id}", "dia": f"eq.{hoje}", "limit": "1"})
        if atual:
            _rest("PATCH", "niche_api_usage", json={"unidades": atual[0]["unidades"] + unidades},
                  params={"id": f"eq.{atual[0]['id']}"})
        else:
            _rest("POST", "niche_api_usage", json={"key_id": key_id, "dia": hoje, "unidades": unidades})
    except Exception:
        pass


def uso_hoje():
    return _rest("GET", "niche_api_usage", params={"select": "*", "dia": f"eq.{date.today().isoformat()}"})


# ---------------- YouTube (enriquecimento — channels.list = 1 unidade) ----------------
def _yt_keys():
    return [k for k in (_cfg().get("youtube_api_keys") or []) if k.get("key")]


def parse_canal(url_ou_handle: str):
    """Extrai (tipo, valor) de um link/handle de canal.
    tipo ∈ id | handle | user | custom. Retorna (None, None) se não reconhecer."""
    s = (url_ou_handle or "").strip()
    if not s:
        return None, None
    if s.startswith("@"):
        return "handle", s
    if re.fullmatch(r"UC[\w-]{20,}", s):
        return "id", s
    m = re.search(r"youtube\.com/(?:channel/(UC[\w-]+)|@([\w.\-]+)|user/([\w.\-]+)|c/([\w.\-]+))", s, re.I)
    if m:
        if m.group(1):
            return "id", m.group(1)
        if m.group(2):
            return "handle", "@" + m.group(2)
        if m.group(3):
            return "user", m.group(3)
        if m.group(4):
            return "custom", m.group(4)
    return None, None


def buscar_canal_yt(url_ou_handle: str):
    """Metadados do canal via YouTube Data API (channels.list = 1 unidade).
    Retorna dict pronto pro salvar_canal, ou None se não achou / sem key."""
    tipo, valor = parse_canal(url_ou_handle)
    if not tipo:
        return None
    keys = _yt_keys()
    if not keys:
        return None
    params = {"part": "snippet,statistics", "maxResults": "1"}
    if tipo == "id":
        params["id"] = valor
    elif tipo == "handle":
        params["forHandle"] = valor
    else:
        params["forUsername"] = valor

    for k in keys:
        try:
            with httpx.Client(timeout=_TIMEOUT) as cli:
                r = cli.get(f"{YT_API}/channels", params={**params, "key": k["key"]})
            if r.status_code == 403:      # quota estourada nessa key -> tenta a próxima
                continue
            r.raise_for_status()
            items = r.json().get("items") or []
            registrar_uso(k.get("id", "?"), 1)
            if not items:
                return None
            it = items[0]
            sn, st = it.get("snippet", {}), it.get("statistics", {})
            thumbs = (sn.get("thumbnails") or {})
            return {
                "channel_id": it["id"],
                "handle": (sn.get("customUrl") or "") or None,
                "titulo": sn.get("title"),
                "url": f"https://www.youtube.com/channel/{it['id']}",
                "thumb_url": (thumbs.get("high") or thumbs.get("default") or {}).get("url"),
                "descricao": (sn.get("description") or "")[:800] or None,
                "pais": sn.get("country"),
                "subs": int(st["subscriberCount"]) if st.get("subscriberCount") else None,
                "views_total": int(st["viewCount"]) if st.get("viewCount") else None,
                "videos_count": int(st["videoCount"]) if st.get("videoCount") else None,
            }
        except Exception:
            continue
    return None
