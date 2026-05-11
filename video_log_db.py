"""
Banco de dados de execucao de producao de videos.

Estrutura (video_log_db.json):
{
  "videos": {
    "2026-04-21__EN": {
      "data": "2026-04-21",
      "canal": "EN",
      "template": "Whispers from Arcturus",
      "template_id": "en",
      "roteiro":   {"status":"ok|erro|pendente", "provider":"claude_cli|gemini|gpt|claude", "fallback":bool,
                    "chars":int, "timestamp":iso_str, "erro":str},
      "narracao":  {"status":"ok|erro|pendente", "provider":"minimax_clone|elevenlabs|inworld", "voice_id":str,
                    "fallback":bool, "chunks":int, "timestamp":iso_str, "path":str, "erro":str},
      "render":    {"status":"ok|erro|pendente", "local_storage":"local|google_drive", "path":str,
                    "tamanho_mb":float, "timestamp":iso_str, "erro":str}
    }
  }
}

Substitui o antigo `historico.json` (por video, nao por producao).
"""

import json
import threading
from datetime import datetime
from pathlib import Path

BASE_DIR = Path(__file__).parent
DB_FILE = BASE_DIR / "video_log_db.json"
_lock = threading.RLock()


def _now() -> str:
    return datetime.now().isoformat(timespec="seconds")


def _key(data: str, canal: str) -> str:
    """Normaliza chave do registro. data pode ser YYYY-MM-DD ou DD/MM/YYYY."""
    if "/" in data:
        parts = data.split("/")
        if len(parts) == 3:
            dd, mm, yyyy = parts[0], parts[1], parts[2]
            data = f"{yyyy}-{mm}-{dd}"
    return f"{data}__{canal}"


def _load() -> dict:
    if not DB_FILE.exists():
        return {"videos": {}}
    try:
        with open(DB_FILE, "r", encoding="utf-8") as f:
            d = json.load(f)
            if "videos" not in d:
                d = {"videos": d} if isinstance(d, dict) else {"videos": {}}
            return d
    except Exception:
        return {"videos": {}}


def _save(db: dict):
    with open(DB_FILE, "w", encoding="utf-8") as f:
        json.dump(db, f, ensure_ascii=False, indent=2)


def _ensure_video(db: dict, data: str, canal: str, template: str = "", template_id: str = "") -> dict:
    k = _key(data, canal)
    if k not in db["videos"]:
        # Normaliza data pra YYYY-MM-DD
        data_norm = data
        if "/" in data:
            parts = data.split("/")
            if len(parts) == 3:
                data_norm = f"{parts[2]}-{parts[1]}-{parts[0]}"
        db["videos"][k] = {
            "data": data_norm,
            "canal": canal,
            "template": template,
            "template_id": template_id,
            "roteiro": {"status": "pendente"},
            "narracao": {"status": "pendente"},
            "render": {"status": "pendente"},
        }
    else:
        # Atualiza template se veio vazio antes
        if template and not db["videos"][k].get("template"):
            db["videos"][k]["template"] = template
        if template_id and not db["videos"][k].get("template_id"):
            db["videos"][k]["template_id"] = template_id
    return db["videos"][k]


# === ETAPAS ===

def iniciar_etapa(data: str, canal: str, etapa: str,
                   template: str = "", template_id: str = ""):
    """Marca etapa como iniciada. Salva 'inicio' (ISO timestamp).
    Idempotente: se ja foi setado, nao sobrescreve (preserva tempo total real).
    etapa: 'roteiro' | 'narracao' | 'render'
    """
    with _lock:
        db = _load()
        v = _ensure_video(db, data, canal, template, template_id)
        et = v.setdefault(etapa, {"status": "pendente"})
        if not et.get("inicio"):
            et["inicio"] = _now()
            et["status"] = "rodando"
        _save(db)


def registrar_roteiro(data: str, canal: str, status: str,
                       provider: str = "", fallback: bool = False,
                       chars: int = 0, erro: str = "",
                       template: str = "", template_id: str = ""):
    """Registra resultado da etapa roteiro. Marca 'fim' = agora."""
    with _lock:
        db = _load()
        v = _ensure_video(db, data, canal, template, template_id)
        et = v.get("roteiro", {})
        # Preserva inicio se ja existia (de iniciar_etapa)
        inicio = et.get("inicio")
        v["roteiro"] = {
            "status": status,
            "provider": provider,
            "fallback": fallback,
            "chars": chars,
            "timestamp": _now(),
            "fim": _now(),
        }
        if inicio:
            v["roteiro"]["inicio"] = inicio
            try:
                from datetime import datetime as _dt
                d_inicio = _dt.fromisoformat(inicio)
                d_fim = _dt.fromisoformat(_now())
                v["roteiro"]["duracao_s"] = round((d_fim - d_inicio).total_seconds(), 1)
            except Exception:
                pass
        if erro:
            v["roteiro"]["erro"] = erro
        _save(db)


def registrar_narracao(data: str, canal: str, status: str,
                        provider: str = "", voice_id: str = "",
                        fallback: bool = False, chunks: int = 0,
                        path: str = "", erro: str = "",
                        template: str = "", template_id: str = ""):
    """Registra resultado da etapa narracao. Marca 'fim'."""
    with _lock:
        db = _load()
        v = _ensure_video(db, data, canal, template, template_id)
        et = v.get("narracao", {})
        inicio = et.get("inicio")
        v["narracao"] = {
            "status": status,
            "provider": provider,
            "voice_id": voice_id,
            "fallback": fallback,
            "chunks": chunks,
            "timestamp": _now(),
            "fim": _now(),
        }
        if inicio:
            v["narracao"]["inicio"] = inicio
            try:
                from datetime import datetime as _dt
                d_inicio = _dt.fromisoformat(inicio)
                d_fim = _dt.fromisoformat(_now())
                v["narracao"]["duracao_s"] = round((d_fim - d_inicio).total_seconds(), 1)
            except Exception:
                pass
        if path:
            v["narracao"]["path"] = path
        if erro:
            v["narracao"]["erro"] = erro
        _save(db)


def registrar_render(data: str, canal: str, status: str,
                      local_storage: str = "local", path: str = "",
                      tamanho_mb: float = 0, erro: str = "",
                      template: str = "", template_id: str = ""):
    """Registra resultado da etapa render. Marca 'fim'."""
    with _lock:
        db = _load()
        v = _ensure_video(db, data, canal, template, template_id)
        et = v.get("render", {})
        inicio = et.get("inicio")
        v["render"] = {
            "status": status,
            "local_storage": local_storage,
            "path": path,
            "tamanho_mb": round(tamanho_mb, 1),
            "timestamp": _now(),
            "fim": _now(),
        }
        if inicio:
            v["render"]["inicio"] = inicio
            try:
                from datetime import datetime as _dt
                d_inicio = _dt.fromisoformat(inicio)
                d_fim = _dt.fromisoformat(_now())
                v["render"]["duracao_s"] = round((d_fim - d_inicio).total_seconds(), 1)
            except Exception:
                pass
        if erro:
            v["render"]["erro"] = erro
        _save(db)


# === CONSULTA ===

def listar_videos(data: str = "", canal: str = "", apenas_erros: bool = False) -> list:
    """Retorna lista de videos ordenada por (data desc, canal asc).

    Filtros opcionais: data (YYYY-MM-DD), canal (tag), apenas_erros.
    """
    with _lock:
        db = _load()
    items = list(db["videos"].values())
    if data:
        items = [v for v in items if v.get("data") == data]
    if canal:
        items = [v for v in items if v.get("canal") == canal]
    if apenas_erros:
        def has_err(v):
            return any(v.get(et, {}).get("status") == "erro" for et in ("roteiro", "narracao", "render"))
        items = [v for v in items if has_err(v)]
    items.sort(key=lambda v: (v.get("data", ""), v.get("canal", "")), reverse=True)
    return items


def obter_video(data: str, canal: str) -> dict | None:
    with _lock:
        db = _load()
    return db["videos"].get(_key(data, canal))


def limpar():
    """Zera o banco (usado apenas pelo reset total)."""
    with _lock:
        _save({"videos": {}})


def resumo() -> dict:
    """Contagem agregada por data e status."""
    with _lock:
        db = _load()
    por_data = {}
    for v in db["videos"].values():
        d = v.get("data", "?")
        if d not in por_data:
            por_data[d] = {"total": 0, "render_ok": 0, "render_erro": 0, "narr_fallback": 0, "storage_drive": 0}
        por_data[d]["total"] += 1
        r = v.get("render", {})
        if r.get("status") == "ok":
            por_data[d]["render_ok"] += 1
            if r.get("local_storage") == "google_drive":
                por_data[d]["storage_drive"] += 1
        elif r.get("status") == "erro":
            por_data[d]["render_erro"] += 1
        if v.get("narracao", {}).get("fallback"):
            por_data[d]["narr_fallback"] += 1
    return {"por_data": por_data, "total_videos": len(db["videos"])}


def historico_data(data: str) -> list:
    """Retorna detalhamento por canal de uma data, com tempos por etapa.
    Aceita YYYY-MM-DD ou DD/MM/YYYY.
    """
    if "/" in data:
        parts = data.split("/")
        if len(parts) == 3:
            data = f"{parts[2]}-{parts[1]}-{parts[0]}"
    with _lock:
        db = _load()
    out = []
    for v in db["videos"].values():
        if v.get("data") != data:
            continue
        rot = v.get("roteiro", {}) or {}
        nar = v.get("narracao", {}) or {}
        ren = v.get("render", {}) or {}
        # Tempo total = max(fim) - min(inicio) entre as 3 etapas
        ts = []
        for et in (rot, nar, ren):
            if et.get("inicio"):
                ts.append(("inicio", et["inicio"]))
            if et.get("fim"):
                ts.append(("fim", et["fim"]))
        total_s = None
        if ts:
            try:
                from datetime import datetime as _dt
                inicios = [_dt.fromisoformat(t[1]) for t in ts if t[0] == "inicio"]
                fins    = [_dt.fromisoformat(t[1]) for t in ts if t[0] == "fim"]
                if inicios and fins:
                    total_s = round((max(fins) - min(inicios)).total_seconds(), 1)
            except Exception:
                pass
        out.append({
            "canal": v.get("canal"),
            "template": v.get("template"),
            "roteiro": {
                "status": rot.get("status"),
                "provider": rot.get("provider"),
                "fallback": bool(rot.get("fallback")),
                "chars": rot.get("chars"),
                "duracao_s": rot.get("duracao_s"),
                "inicio": rot.get("inicio"),
                "fim": rot.get("fim"),
            },
            "narracao": {
                "status": nar.get("status"),
                "provider": nar.get("provider"),
                "voice_id": nar.get("voice_id"),
                "fallback": bool(nar.get("fallback")),
                "chunks": nar.get("chunks"),
                "duracao_s": nar.get("duracao_s"),
                "inicio": nar.get("inicio"),
                "fim": nar.get("fim"),
            },
            "render": {
                "status": ren.get("status"),
                "path": ren.get("path"),
                "tamanho_mb": ren.get("tamanho_mb"),
                "duracao_s": ren.get("duracao_s"),
                "inicio": ren.get("inicio"),
                "fim": ren.get("fim"),
            },
            "total_s": total_s,
        })
    out.sort(key=lambda v: v.get("canal") or "")
    return out


def datas_disponiveis() -> list:
    """Lista datas (YYYY-MM-DD) que tem registros, ordenadas desc."""
    with _lock:
        db = _load()
    datas = sorted({v.get("data") for v in db["videos"].values() if v.get("data")}, reverse=True)
    return datas
