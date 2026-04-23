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

def registrar_roteiro(data: str, canal: str, status: str,
                       provider: str = "", fallback: bool = False,
                       chars: int = 0, erro: str = "",
                       template: str = "", template_id: str = ""):
    """Registra resultado da etapa roteiro.

    status: 'ok' | 'erro' | 'pendente'
    provider: 'claude_cli' | 'claude' | 'gemini' | 'gpt' | ...
    fallback: True se provider nao era o primario
    """
    with _lock:
        db = _load()
        v = _ensure_video(db, data, canal, template, template_id)
        v["roteiro"] = {
            "status": status,
            "provider": provider,
            "fallback": fallback,
            "chars": chars,
            "timestamp": _now(),
        }
        if erro:
            v["roteiro"]["erro"] = erro
        _save(db)


def registrar_narracao(data: str, canal: str, status: str,
                        provider: str = "", voice_id: str = "",
                        fallback: bool = False, chunks: int = 0,
                        path: str = "", erro: str = "",
                        template: str = "", template_id: str = ""):
    """Registra resultado da etapa narracao.

    provider: 'minimax_clone' | 'elevenlabs' | 'inworld'
    fallback: True se foi usado o fallback do template (Inworld apos Minimax falhar)
    """
    with _lock:
        db = _load()
        v = _ensure_video(db, data, canal, template, template_id)
        v["narracao"] = {
            "status": status,
            "provider": provider,
            "voice_id": voice_id,
            "fallback": fallback,
            "chunks": chunks,
            "timestamp": _now(),
        }
        if path:
            v["narracao"]["path"] = path
        if erro:
            v["narracao"]["erro"] = erro
        _save(db)


def registrar_render(data: str, canal: str, status: str,
                      local_storage: str = "local", path: str = "",
                      tamanho_mb: float = 0, erro: str = "",
                      template: str = "", template_id: str = ""):
    """Registra resultado da etapa render.

    local_storage: 'local' (disco F:) | 'google_drive'
    """
    with _lock:
        db = _load()
        v = _ensure_video(db, data, canal, template, template_id)
        v["render"] = {
            "status": status,
            "local_storage": local_storage,
            "path": path,
            "tamanho_mb": round(tamanho_mb, 1),
            "timestamp": _now(),
        }
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
