"""Painel Youtube NATIVO no Automator — bridge pro Supabase do drive-to-youtube.

Reconstrucao da estrutura do youtube-painel (Next.js/Vercel) dentro do
Automator, pedida pelo Piter 31/07 (handoff: Notion 3ae770cbb8de81b29ab1cae708e0933e).
O painel Vercel CONTINUA existindo (colaborador do aquecimento usa la); esta
versao e' a visao do OPERADOR dentro do Automator.

⚠️ SEGURANCA — leia antes de mexer:
  O Automator roda SEM auth num IP publico (:8500). O Supabase daqui guarda as
  credenciais da rede inteira (proxies, tokens OAuth dos 16 canais). Por isso:
    1. TODA rota daqui exige o header `X-Painel-Key` (chave em
       painel_yt_config.json na VPS, gitignored — repo e' PUBLICO).
    2. Segredos sao WRITE-ONLY: da pra gravar, NUNCA voltam na resposta
       (mascarados em *_set booleans). Pra VER um segredo, usar o painel
       Vercel (HTTPS + JWT). Aqui e' HTTP puro — nao trafega segredo.
    3. O PATCH ignora segredo vazio — corrige o bug conhecido do painel
       ("PATCH de canal sobrescreve token_yt_json vazio", handoff §8).

Config na VPS (`/opt/video-automator/painel_yt_config.json`, gitignored):
    {"supabase_url": "...", "supabase_service_key": "...", "painel_key": "..."}
"""
import json
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

from fastapi import APIRouter, Header, HTTPException, Request

router = APIRouter(prefix="/api/painel-yt", tags=["painel-yt"])

_CFG_PATH = Path(__file__).parent / "painel_yt_config.json"
_cfg_cache: dict = {}


def _cfg() -> dict:
    global _cfg_cache
    if not _cfg_cache:
        try:
            _cfg_cache = json.loads(_CFG_PATH.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            _cfg_cache = {}
    return _cfg_cache


def _auth(x_painel_key: str | None, sensivel: bool = False):
    """Piter 31/07: SEM senha na aba. `painel_key` no config virou OPCIONAL —
    ausente = rotas abertas (mesma postura do resto do Automator, que nao tem
    auth). Excecao: rotas `sensivel=True` (aquecimento — devolve SENHAS de
    contas Google em texto puro) ficam BLOQUEADAS enquanto nao houver chave;
    liberar isso pra internet nao e' opcao. Os demais segredos seguem
    write-only/mascarados independente de chave."""
    chave = (_cfg().get("painel_key") or "").strip()
    if chave:
        if (x_painel_key or "").strip() != chave:
            raise HTTPException(401, "X-Painel-Key invalida")
        return
    if sensivel:
        raise HTTPException(403, "rota sensivel: exige painel_key configurada na VPS")


# ---------------------------------------------------------------- Supabase REST
def _sb(method: str, path: str, body=None, params: dict | None = None,
        prefer: str | None = None):
    c = _cfg()
    url = c.get("supabase_url", "").rstrip("/") + "/rest/v1" + path
    if params:
        url += "?" + urllib.parse.urlencode(params, doseq=True)
    headers = {
        "apikey": c.get("supabase_service_key", ""),
        "Authorization": f"Bearer {c.get('supabase_service_key', '')}",
        "Content-Type": "application/json",
    }
    if prefer:
        headers["Prefer"] = prefer
    data = json.dumps(body).encode() if body is not None else None
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=30) as r:
            raw = r.read().decode("utf-8")
            return json.loads(raw) if raw.strip() else None
    except urllib.error.HTTPError as e:
        detalhe = e.read().decode("utf-8", "replace")[:300]
        raise HTTPException(502, f"Supabase {e.code}: {detalhe}")
    except (urllib.error.URLError, TimeoutError) as e:
        raise HTTPException(502, f"Supabase inacessivel: {e}")


# ---------------------------------------------------------------- mascaramento
# WRITE-ONLY: estes campos NUNCA voltam na resposta (viram *_set: bool).
_SEGREDOS_JSON = ("token_yt_json", "token_personal_json", "client_secret_json")
_SEGREDOS_TXT = ("autenticador_2fa", "backup_codes")


def _mascarar(c: dict) -> dict:
    c = dict(c)
    p = c.get("proxy_socks5")
    if isinstance(p, dict):
        c["proxy_socks5"] = {"host": p.get("host"), "port": p.get("port"),
                             "user": p.get("user"), "pass_set": bool(p.get("pass"))}
    for k in _SEGREDOS_JSON:
        v = c.pop(k, None)
        c[k.replace("_json", "_set")] = bool(v)
        if k == "token_yt_json" and isinstance(v, dict):
            c["token_yt_expiry"] = v.get("expiry")
    for k in _SEGREDOS_TXT:
        c[f"{k}_set"] = bool(c.pop(k, None))
    return c


# Campos simples que o PATCH/POST aceita direto (espelho do lib/types.ts).
_CAMPOS_OK = {
    "alias", "nome_youtube", "channel_id_youtube", "email_google",
    "email_recuperacao", "google_cloud_project", "ordem", "custo_proxy_usd",
    "proxy_data_compra", "telefone", "checklist", "adspower_profile_id",
    "drive_folder_id", "sheet_id", "sheet_tab", "playlist_id", "category_id",
    "default_description", "altered_content", "comment_template",
    "comment_slug_base", "comment_slug_pattern", "slug_pattern",
    "thumbnail_template", "prompt_template_name", "ai_engine", "source",
    "automator_aliases", "video_layout", "yt_lang", "publish_slots",
    "timezone", "status", "enable_pin_rpa", "notes",
    # Piter 31/07: nº do pedido do gmail/canal comprado + dump raw de infos
    # (colunas criadas via Management API em 31/07)
    "pedido_num", "raw_info",
}


def _montar_patch(body: dict, atual: dict | None) -> dict:
    """Patch seguro: campos simples passam; segredos SO se vierem preenchidos
    (vazio = mantem o que esta — mata o bug do token_yt_json sobrescrito)."""
    patch = {k: v for k, v in body.items() if k in _CAMPOS_OK}
    for k in (*_SEGREDOS_JSON, *_SEGREDOS_TXT):
        v = body.get(k)
        if v not in (None, "", {}, []):
            patch[k] = v
    p = body.get("proxy_socks5")
    if isinstance(p, dict) and (p.get("host") or p.get("port")):
        atual_p = (atual or {}).get("proxy_socks5") or {}
        novo = {"host": p.get("host"), "port": p.get("port"), "user": p.get("user")}
        # senha vazia = preserva a atual (write-only)
        novo["pass"] = p.get("pass") or atual_p.get("pass") or ""
        patch["proxy_socks5"] = novo
    return patch


def _anexar_videos(runs: list) -> list:
    """Anexa a cada run os videos dele. `videos.run_id` NUNCA e' preenchido
    pelo pipeline (checado no dado real 31/07) — o join direto volta vazio.
    Casamos por CANAL + JANELA DE TEMPO: video criado entre o inicio e o fim
    do run (com 60s de folga) e' daquele run."""
    from datetime import datetime, timedelta, timezone as _tz

    def _dt(s):
        try:
            return datetime.fromisoformat((s or "").replace("Z", "+00:00"))
        except ValueError:
            return None

    inicios = [d for d in (_dt(r.get("started_at")) for r in runs) if d]
    if not inicios:
        return runs
    corte = (min(inicios) - timedelta(minutes=2)).isoformat()
    vids = _sb("GET", "/videos", params={
        "select": "canal_id,titulo,publish_at_utc,stage,created_at",
        "created_at": f"gte.{corte}", "order": "created_at.asc",
        "limit": 500}) or []
    agora = datetime.now(_tz.utc)
    folga = timedelta(seconds=60)
    for r in runs:
        ini = _dt(r.get("started_at"))
        fim = _dt(r.get("finished_at")) or agora
        r["videos"] = [] if not ini else [
            v for v in vids
            if v.get("canal_id") == r.get("canal_id")
            and (dv := _dt(v.get("created_at"))) is not None
            and ini - folga <= dv <= fim + folga
        ]
    _videos_do_grid(runs)
    return runs


def _videos_do_grid(runs: list):
    """Fallback pros runs SEM linha na tabela videos (fluxo automator nao cria):
    o cli_args do run carrega `--tema-row/--tema-col` — a CELULA do grid. O
    temas.json mora nesta VPS -> data e titulo saem de graca."""
    pend = [r for r in runs if not r.get("videos") and r.get("cli_args")]
    if not pend:
        return
    try:
        import scriptwriter
        t = scriptwriter.carregar_temas() or {}
    except Exception:
        return
    linhas, cel = t.get("linhas") or [], t.get("celulas") or {}
    for r in pend:
        args = [str(a) for a in (r.get("cli_args") or [])]
        row = col = None
        for i, a in enumerate(args[:-1]):
            if a == "--tema-row":
                row = int(args[i + 1]) if args[i + 1].isdigit() else None
            elif a == "--tema-col":
                col = int(args[i + 1]) if args[i + 1].isdigit() else None
        if row is None or col is None or row >= len(linhas):
            continue
        data_br = (linhas[row].get("data") or "").strip()
        c = cel.get(f"{row}_{col}") or {}
        iso = "-".join(reversed(data_br.split("/"))) if data_br.count("/") == 2 else None
        r["videos"] = [{"canal_id": r.get("canal_id"), "titulo": c.get("titulo") or "",
                        "publish_at_utc": iso, "stage": None, "origem": "grid"}]


# ---------------------------------------------------------------- rotas
@router.get("/canais")
def listar_canais(x_painel_key: str = Header(None)):
    _auth(x_painel_key)
    rows = _sb("GET", "/canais_yt", params={"order": "ordem.asc.nullslast,alias.asc"})
    return {"ok": True, "canais": [_mascarar(c) for c in rows or []]}


@router.get("/canais/{alias}")
def obter_canal(alias: str, x_painel_key: str = Header(None)):
    _auth(x_painel_key)
    rows = _sb("GET", "/canais_yt", params={"alias": f"eq.{alias}"})
    if not rows:
        raise HTTPException(404, f"canal '{alias}' nao existe")
    return {"ok": True, "canal": _mascarar(rows[0])}


@router.post("/canais")
async def criar_canal(request: Request, x_painel_key: str = Header(None)):
    _auth(x_painel_key)
    body = await request.json()
    if not (body.get("alias") and body.get("nome_youtube")):
        raise HTTPException(400, "alias e nome_youtube sao obrigatorios")
    novo = _montar_patch(body, None)
    novo.setdefault("status", "ativo")
    novo.setdefault("timezone", body.get("timezone") or "UTC")
    owner = _cfg().get("owner_id")
    if owner:
        novo["owner_id"] = owner
    rows = _sb("POST", "/canais_yt", body=novo, prefer="return=representation")
    return {"ok": True, "canal": _mascarar(rows[0])}


@router.patch("/canais/{alias}")
async def editar_canal(alias: str, request: Request, x_painel_key: str = Header(None)):
    _auth(x_painel_key)
    body = await request.json()
    rows = _sb("GET", "/canais_yt", params={"alias": f"eq.{alias}"})
    if not rows:
        raise HTTPException(404, f"canal '{alias}' nao existe")
    patch = _montar_patch(body, rows[0])
    if not patch:
        raise HTTPException(400, "nenhum campo valido no payload")
    upd = _sb("PATCH", "/canais_yt", body=patch,
              params={"alias": f"eq.{alias}"}, prefer="return=representation")
    return {"ok": True, "canal": _mascarar(upd[0])}


@router.get("/dashboard")
def dashboard(x_painel_key: str = Header(None)):
    _auth(x_painel_key)
    canais = [_mascarar(c) for c in _sb("GET", "/canais_yt", params={
        "select": "id,alias,nome_youtube,status,ordem,adspower_profile_id,"
                  "playlist_id,timezone,proxy_socks5,token_yt_json",
        "order": "ordem.asc.nullslast,alias.asc"}) or []]
    runs = _anexar_videos(_sb("GET", "/runs", params={
        "select": "*,canais_yt(alias)", "order": "started_at.desc", "limit": 10}) or [])
    videos = _sb("GET", "/videos", params={
        "select": "*,canais_yt(alias)", "order": "created_at.desc", "limit": 15}) or []
    alerts = _sb("GET", "/alerts", params={
        "acknowledged": "eq.false", "order": "created_at.desc", "limit": 20}) or []
    health = _sb("GET", "/health_status", params={
        "order": "last_checked_at.desc", "limit": 50}) or []
    return {"ok": True, "canais": canais, "runs": runs, "videos": videos,
            "alerts": alerts, "health": health}


@router.get("/runs")
def listar_runs(limit: int = 50, x_painel_key: str = Header(None)):
    _auth(x_painel_key)
    rows = _anexar_videos(_sb("GET", "/runs", params={
        "select": "*,canais_yt(alias)", "order": "started_at.desc",
        "limit": min(limit, 200)}) or [])
    return {"ok": True, "runs": rows}


@router.get("/videos")
def listar_videos(limit: int = 100, canal: str = "", x_painel_key: str = Header(None)):
    _auth(x_painel_key)
    params = {"select": "*,canais_yt(alias)", "order": "created_at.desc",
              "limit": min(limit, 300)}
    if canal:
        ids = _sb("GET", "/canais_yt", params={"alias": f"eq.{canal}", "select": "id"})
        if ids:
            params["canal_id"] = f"eq.{ids[0]['id']}"
    rows = _sb("GET", "/videos", params=params) or []
    return {"ok": True, "videos": rows}


@router.get("/alerts")
def listar_alerts(limit: int = 100, x_painel_key: str = Header(None)):
    _auth(x_painel_key)
    rows = _sb("GET", "/alerts", params={"order": "created_at.desc",
                                         "limit": min(limit, 300)}) or []
    return {"ok": True, "alerts": rows}


@router.post("/alerts/{alert_id}/ack")
def ack_alert(alert_id: str, x_painel_key: str = Header(None)):
    _auth(x_painel_key)
    _sb("PATCH", "/alerts", body={"acknowledged": True},
        params={"id": f"eq.{alert_id}"})
    return {"ok": True}


# --------- Aquecimento (mesmo contrato do painel Vercel: singleton id=1) -----
_AQ_CHAVES = ("emails", "cells", "creds", "proxies")


@router.get("/aquecimento")
def aquecimento_get(x_painel_key: str = Header(None)):
    _auth(x_painel_key, sensivel=True)   # creds em texto puro — nunca sem chave
    rows = _sb("GET", "/aquecimento_state", params={"id": "eq.1"})
    data = (rows[0].get("data") if rows else None) or {}
    return {"ok": True, "data": {k: data.get(k) or ({} if k in ("cells", "creds") else [])
                                 for k in _AQ_CHAVES}}


@router.put("/aquecimento")
async def aquecimento_put(request: Request, x_painel_key: str = Header(None)):
    _auth(x_painel_key, sensivel=True)   # creds em texto puro — nunca sem chave
    body = await request.json()
    data = body.get("data") if isinstance(body, dict) else None
    if not isinstance(data, dict):
        raise HTTPException(400, "payload precisa de {data:{...}}")
    # `clean` identico ao painel Vercel: so as chaves conhecidas persistem
    clean = {k: data.get(k) or ({} if k in ("cells", "creds") else [])
             for k in _AQ_CHAVES}
    _sb("PATCH", "/aquecimento_state", body={"data": clean},
        params={"id": "eq.1"})
    return {"ok": True}


# --------- Config global ------------------------------------------------------
@router.get("/config")
def config_get(x_painel_key: str = Header(None)):
    _auth(x_painel_key)
    rows = _sb("GET", "/global_config", params={"order": "key.asc"}) or []
    for r in rows:
        if r.get("is_secret"):
            r["value"] = "•••"      # write-only tambem aqui
    return {"ok": True, "config": rows}


@router.put("/config/{key}")
async def config_put(key: str, request: Request, x_painel_key: str = Header(None)):
    _auth(x_painel_key)
    body = await request.json()
    _sb("PATCH", "/global_config", body={"value": body.get("value")},
        params={"key": f"eq.{key}"})
    return {"ok": True}
