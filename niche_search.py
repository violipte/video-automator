"""Niche Spy — busca de nichos e canais similares na YouTube Data API v3.

ECONOMIA DE QUOTA (o gargalo real):
  search.list        = 100 unidades  <- caro, e o que descobre
  channels.list      = 1 unidade     <- metricas em lote (ate 50 ids por chamada)
  playlistItems.list = 1 unidade     <- videos de um canal SEM gastar search
  Cota = 10.000 unidades/dia POR PROJETO do Google Cloud.
Por isso: cache no Supabase (niche_search_cache), lote de ids no channels.list,
e uploads-playlist em vez de search pra ler os videos do canal-referencia.

Duas funcoes principais:
  buscar(criterios)         -> descobre canais por palavra-chave + filtros
  similares(url_ou_handle)  -> dado um canal-ref, acha outros no mesmo estilo
"""
import hashlib
import json
import re
import urllib.parse
from datetime import datetime, timedelta, timezone
from pathlib import Path

import httpx

import niche_spy_db as db

BASE_DIR = Path(__file__).parent
CREDS = BASE_DIR / "credentials.json"
YT = "https://www.googleapis.com/youtube/v3"
GEMINI = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"
_TIMEOUT = 30.0
CACHE_H = 12          # horas de cache por busca


# ---------------- infra YouTube (com rotacao de keys + contador) ----------------
def _yt(endpoint: str, params: dict, custo: int):
    """Chama a API rotacionando as keys. Em 403 (quota/restricao) tenta a proxima."""
    keys = db._yt_keys()
    if not keys:
        raise RuntimeError("Nenhuma YouTube API key em config.json (youtube_api_keys)")
    ultimo = ""
    for k in keys:
        try:
            with httpx.Client(timeout=_TIMEOUT) as cli:
                r = cli.get(f"{YT}/{endpoint}", params={**params, "key": k["key"]})
            if r.status_code == 403:
                ultimo = (r.json().get("error", {}) or {}).get("message", "403")[:120]
                continue
            r.raise_for_status()
            db.registrar_uso(k.get("id", "?"), custo)
            return r.json()
        except httpx.HTTPStatusError as e:
            ultimo = f"HTTP{e.response.status_code}"
            continue
        except Exception as e:
            ultimo = str(e)[:100]
            continue
    raise RuntimeError(f"Todas as keys falharam: {ultimo}")


def _metricas(channel_ids, chunk=50):
    """channels.list em LOTE (1 unidade por 50 ids). Retorna {id: {...}}."""
    out = {}
    ids = [c for c in dict.fromkeys(channel_ids) if c]
    for i in range(0, len(ids), chunk):
        d = _yt("channels", {"part": "snippet,statistics",
                             "id": ",".join(ids[i:i + chunk]), "maxResults": str(chunk)}, 1)
        for it in d.get("items", []):
            sn, st = it.get("snippet", {}), it.get("statistics", {})
            th = sn.get("thumbnails") or {}
            out[it["id"]] = {
                "channel_id": it["id"],
                "titulo": sn.get("title"),
                "handle": sn.get("customUrl"),
                "descricao": (sn.get("description") or "")[:400],
                "thumb_url": (th.get("medium") or th.get("default") or {}).get("url"),
                "pais": sn.get("country"),
                "criado_em_yt": sn.get("publishedAt"),
                "url": f"https://www.youtube.com/channel/{it['id']}",
                "subs": int(st["subscriberCount"]) if st.get("subscriberCount") else None,
                "views_total": int(st["viewCount"]) if st.get("viewCount") else None,
                "videos_count": int(st["videoCount"]) if st.get("videoCount") else None,
            }
    return out


def _derivados(c):
    """Metricas derivadas que dizem mais que o numero cru de inscritos."""
    subs, views, vids = c.get("subs") or 0, c.get("views_total") or 0, c.get("videos_count") or 0
    c["views_por_video"] = round(views / vids) if vids else None
    c["views_por_sub"] = round(views / subs, 1) if subs else None      # tracao vs audiencia
    idade_dias = None
    if c.get("criado_em_yt"):
        try:
            dt = datetime.fromisoformat(c["criado_em_yt"].replace("Z", "+00:00"))
            idade_dias = (datetime.now(timezone.utc) - dt).days
        except Exception:
            pass
    c["idade_dias"] = idade_dias
    # canal novo com muita view = sinal forte de nicho quente
    c["views_por_dia"] = round(views / idade_dias) if idade_dias else None
    return c


# ---------------- cache ----------------
def _cache_get(chave):
    try:
        r = db._rest("GET", "niche_search_cache", params={
            "select": "resultado,expira_em", "cache_key": f"eq.{chave}", "limit": "1"})
        if r and r[0].get("expira_em"):
            if datetime.fromisoformat(r[0]["expira_em"].replace("Z", "+00:00")) > datetime.now(timezone.utc):
                return r[0]["resultado"]
    except Exception:
        pass
    return None


def _cache_set(chave, criterios, resultado):
    try:
        exp = (datetime.now(timezone.utc) + timedelta(hours=CACHE_H)).isoformat()
        db._rest("POST", "niche_search_cache",
                 json={"cache_key": chave, "criterios": criterios, "resultado": resultado, "expira_em": exp},
                 params={"on_conflict": "cache_key"},
                 extra_headers={"Prefer": "resolution=merge-duplicates"})
    except Exception:
        pass


# ---------------- 1) BUSCA POR CRITERIOS ----------------
def buscar(criterios: dict, forcar=False):
    """Descobre canais por palavra-chave + filtros.

    criterios: {q, min_subs, max_subs, min_views_video, dias, idioma, pais, order, max_resultados}
    Estrategia: search.list de VIDEOS (sinal de traccao real no tema) -> junta os canais
    -> channels.list em lote pra metricas -> filtra por inscritos/views.
    Custo: 100 (search) + ~1 (channels) por busca. Cache de 12h.
    """
    c = {k: v for k, v in (criterios or {}).items() if v not in (None, "")}
    q = (c.get("q") or "").strip()
    if not q:
        return {"ok": False, "erro": "Informe uma palavra-chave"}

    chave = hashlib.md5(json.dumps(c, sort_keys=True, ensure_ascii=False).encode()).hexdigest()
    if not forcar:
        hit = _cache_get(chave)
        if hit:
            return {"ok": True, "cache": True, **hit}

    params = {"part": "snippet", "type": "video", "q": q,
              "maxResults": str(min(int(c.get("max_resultados", 50)), 50)),
              "order": c.get("order", "viewCount")}
    if c.get("idioma"):
        params["relevanceLanguage"] = c["idioma"]
    if c.get("pais"):
        params["regionCode"] = c["pais"]
    if c.get("dias"):
        params["publishedAfter"] = (datetime.now(timezone.utc) - timedelta(days=int(c["dias"]))).isoformat()

    d = _yt("search", params, 100)
    vistos, exemplos = [], {}
    for it in d.get("items", []):
        cid = (it.get("snippet") or {}).get("channelId")
        if not cid:
            continue
        vistos.append(cid)
        exemplos.setdefault(cid, []).append({
            "video_id": (it.get("id") or {}).get("videoId"),
            "titulo": (it.get("snippet") or {}).get("title"),
            "publicado": (it.get("snippet") or {}).get("publishedAt"),
        })

    metr = _metricas(vistos)
    canais = []
    for cid, m in metr.items():
        m = _derivados(m)
        subs = m.get("subs") or 0
        if c.get("min_subs") and subs < int(c["min_subs"]):
            continue
        if c.get("max_subs") and subs > int(c["max_subs"]):
            continue
        if c.get("min_views_video") and (m.get("views_por_video") or 0) < int(c["min_views_video"]):
            continue
        m["videos_exemplo"] = exemplos.get(cid, [])[:3]
        m["hits"] = len(exemplos.get(cid, []))     # quantos videos dele apareceram na busca
        canais.append(m)

    canais.sort(key=lambda x: (x.get("hits", 0), x.get("views_por_video") or 0), reverse=True)
    res = {"canais": canais, "total_bruto": len(metr), "criterios": c}
    _cache_set(chave, c, res)
    return {"ok": True, "cache": False, **res}


# ---------------- 2) CANAIS SIMILARES (finder por referencia) ----------------
def _gemini(prompt, timeout=90):
    """LLM pra extrair temas / pontuar semelhanca. Usa as chaves gemini do credentials.json."""
    try:
        keys = [c["api_key"] for c in json.load(open(CREDS, encoding="utf-8"))
                if c.get("provedor") == "gemini" and c.get("api_key")]
    except Exception:
        keys = []
    body = {"contents": [{"parts": [{"text": prompt}]}],
            "safetySettings": [{"category": c, "threshold": "BLOCK_NONE"} for c in (
                "HARM_CATEGORY_HARASSMENT", "HARM_CATEGORY_HATE_SPEECH",
                "HARM_CATEGORY_SEXUALLY_EXPLICIT", "HARM_CATEGORY_DANGEROUS_CONTENT")]}
    for k in keys:
        try:
            with httpx.Client(timeout=timeout) as cli:
                r = cli.post(f"{GEMINI}?key={k}", json=body)
            if r.status_code in (429, 503):
                continue
            r.raise_for_status()
            return r.json()["candidates"][0]["content"]["parts"][0]["text"]
        except Exception:
            continue
    return ""


def _json_do_texto(txt, abre="[", fecha="]"):
    a, b = txt.find(abre), txt.rfind(fecha)
    if a < 0 or b <= a:
        return None
    try:
        return json.loads(txt[a:b + 1])
    except Exception:
        return None


def videos_do_canal(channel_id, n=25):
    """Ultimos videos SEM gastar search.list: uploads playlist (1+1 unidades)."""
    d = _yt("channels", {"part": "contentDetails", "id": channel_id}, 1)
    items = d.get("items") or []
    if not items:
        return []
    pl = ((items[0].get("contentDetails") or {}).get("relatedPlaylists") or {}).get("uploads")
    if not pl:
        return []
    d2 = _yt("playlistItems", {"part": "snippet", "playlistId": pl, "maxResults": str(min(n, 50))}, 1)
    return [{"titulo": (i.get("snippet") or {}).get("title"),
             "publicado": (i.get("snippet") or {}).get("publishedAt")} for i in d2.get("items", [])]


def similares(url_ou_handle: str, n_buscas=3, min_subs=None, max_subs=None):
    """Dado um canal de referencia, acha canais no mesmo estilo.

    O YouTube NAO tem endpoint de 'canais relacionados' (relatedToVideoId foi
    descontinuado em 2023) -> heuristica:
      1. le o canal-ref + seus videos recentes (uploads playlist, barato)
      2. LLM extrai os TEMAS/padroes de titulo
      3. search.list por esses temas (100 un. cada -> por isso n_buscas limitado)
      4. agrega canais, tira o proprio ref, pega metricas em lote
      5. LLM pontua a semelhanca (0-100) com o canal-ref
    Custo: ~n_buscas*100 + poucas unidades.
    """
    ref_meta = db.buscar_canal_yt(url_ou_handle)
    if not ref_meta:
        return {"ok": False, "erro": "Canal de referencia nao encontrado"}
    ref_id = ref_meta["channel_id"]

    vids = videos_do_canal(ref_id, 25)
    titulos = [v["titulo"] for v in vids if v.get("titulo")][:25]
    if not titulos:
        return {"ok": False, "erro": "Canal sem videos publicos para analisar"}

    p = ("Analise este canal do YouTube e os titulos dos videos recentes dele. "
         f"CANAL: {ref_meta.get('titulo')}\nDESCRICAO: {(ref_meta.get('descricao') or '')[:400]}\n"
         "TITULOS:\n- " + "\n- ".join(titulos) +
         f"\n\nRetorne APENAS um array JSON com {n_buscas} strings: as {n_buscas} MELHORES queries de busca "
         "do YouTube para encontrar OUTROS canais do mesmo nicho/estilo. Use o mesmo idioma dos titulos. "
         "Queries especificas do nicho (nao genericas). Exemplo: [\"query 1\",\"query 2\"]")
    queries = _json_do_texto(_gemini(p)) or []
    queries = [q for q in queries if isinstance(q, str)][:n_buscas]
    if not queries:
        queries = [ref_meta.get("titulo") or ""][:1]

    achados, exemplos = {}, {}
    for q in queries:
        try:
            d = _yt("search", {"part": "snippet", "type": "video", "q": q,
                               "maxResults": "50", "order": "viewCount"}, 100)
        except Exception:
            continue
        for it in d.get("items", []):
            cid = (it.get("snippet") or {}).get("channelId")
            if not cid or cid == ref_id:
                continue
            achados[cid] = achados.get(cid, 0) + 1
            exemplos.setdefault(cid, []).append((it.get("snippet") or {}).get("title"))

    if not achados:
        return {"ok": False, "erro": "Nenhum canal encontrado", "queries": queries}

    top = [c for c, _ in sorted(achados.items(), key=lambda x: -x[1])][:40]
    metr = _metricas(top)
    cands = []
    for cid, m in metr.items():
        m = _derivados(m)
        subs = m.get("subs") or 0
        if min_subs and subs < int(min_subs):
            continue
        if max_subs and subs > int(max_subs):
            continue
        m["hits"] = achados.get(cid, 0)
        m["titulos_exemplo"] = [t for t in exemplos.get(cid, []) if t][:3]
        cands.append(m)
    cands.sort(key=lambda x: -x.get("hits", 0))
    cands = cands[:20]

    # pontuacao de semelhanca pelo LLM
    lista = "\n".join(f'{i}. {c["titulo"]} ({c.get("subs") or 0} subs) — ex: {"; ".join(c["titulos_exemplo"][:2])}'
                      for i, c in enumerate(cands))
    p2 = (f'Canal de REFERENCIA: "{ref_meta.get("titulo")}" — titulos tipicos: {"; ".join(titulos[:6])}\n\n'
          f"CANDIDATOS:\n{lista}\n\n"
          "Para CADA candidato, avalie o quanto ele e parecido com o canal de referencia "
          "(mesmo nicho, mesmo formato, mesmo publico). Retorne APENAS um array JSON: "
          '[{"i": <indice>, "score": <0-100>, "motivo": "<frase curta em portugues>"}]')
    notas = {n.get("i"): n for n in (_json_do_texto(_gemini(p2)) or []) if isinstance(n, dict)}
    for i, c in enumerate(cands):
        n = notas.get(i) or {}
        c["similaridade"] = n.get("score")
        c["motivo"] = n.get("motivo")
    cands.sort(key=lambda x: (x.get("similaridade") or 0, x.get("hits", 0)), reverse=True)

    return {"ok": True, "referencia": _derivados(ref_meta), "queries": queries, "canais": cands}
