"""
Backlog Temas - fila sequencial de temas candidatos com 2 ponteiros (Status Geral e CO*).

Storage: backlog_temas.json
Schema:
{
  "itens": [
    {
      "id": "bk_xxxxxxxx",
      "data": "DD/MM/YYYY",
      "titulo": "...",
      "texto_thumb": "",
      "link": "https://youtube.com/watch?v=...",
      "video_id": "abc123XYZ_-",
      "geral": "" | "Ok",   // ponteiro Geral (canais nao-CO)
      "co": "" | "Ok",      // ponteiro CO
      "criado_em": "2026-05-04T12:34:56"
    }
  ]
}

Migration: itens com campo legado 'status' sao migrados pra 'geral' on-load.

Datas: formato DD/MM/YYYY pra bater com o grid de Temas.
Sequencial: nova adicao recebe `ultima_data + 1 dia`, ou hoje se vazio.
"""
import json
import re
import secrets
import threading
from datetime import date, datetime, timedelta
from pathlib import Path

BASE_DIR = Path(__file__).parent
BACKLOG_FILE = BASE_DIR / "backlog_temas.json"
_lock = threading.RLock()


# Aceita youtube.com/watch?v=, youtu.be/, embed/, /v/, e shorts/
_VIDEO_ID_RE = re.compile(
    r"(?:youtube\.com/(?:watch\?(?:[^&]*&)*v=|embed/|v/|shorts/)|youtu\.be/)"
    r"([a-zA-Z0-9_-]{11})"
)


def _extrair_video_id(url: str) -> str | None:
    """Extrai videoId de URLs do YouTube. Retorna None se nao for link valido."""
    if not url or not isinstance(url, str):
        return None
    m = _VIDEO_ID_RE.search(url.strip())
    return m.group(1) if m else None


def _carregar() -> dict:
    if not BACKLOG_FILE.exists():
        return {"itens": []}
    try:
        with open(BACKLOG_FILE, "r", encoding="utf-8") as f:
            data = json.load(f)
        if not isinstance(data, dict) or "itens" not in data:
            return {"itens": []}
        # Migration: status -> geral (renomeado pra ficar mais claro)
        for it in data.get("itens", []):
            if "status" in it and "geral" not in it:
                it["geral"] = it.pop("status")
            elif "status" in it and "geral" in it:
                # ambos existem, manter geral (mais recente) e remover status legado
                it.pop("status", None)
        return data
    except Exception:
        return {"itens": []}


def _salvar(data: dict):
    with _lock:
        try:
            with open(BACKLOG_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, ensure_ascii=False, indent=2)
        except Exception:
            pass


def _gerar_id() -> str:
    return f"bk_{secrets.token_hex(4)}"


def _proxima_data_sequencial(itens: list) -> str:
    """Ultima data do backlog + 1 dia. Vazio = hoje. Formato DD/MM/YYYY."""
    datas = []
    for it in itens or []:
        d = it.get("data", "")
        try:
            datas.append(datetime.strptime(d, "%d/%m/%Y").date())
        except Exception:
            continue

    if not datas:
        return date.today().strftime("%d/%m/%Y")
    proxima = max(datas) + timedelta(days=1)
    return proxima.strftime("%d/%m/%Y")


def adicionar(link: str = "", titulo: str = "", texto_thumb: str = "", data: str = None) -> dict:
    """Adiciona item. Retorna o item criado ou {'erro': '...'} em caso de erro.

    Aceita 2 modos:
    - Com link YT: extrai video_id, permite enrich (oEmbed + OCR).
    - Sem link (tema manual): exige titulo. video_id e link ficam vazios.
    """
    link = (link or "").strip()
    titulo_clean = (titulo or "").strip()

    # Regras de validacao
    if not link and not titulo_clean:
        return {"erro": "Forneça pelo menos um link YouTube OU um título manual"}

    video_id = ""
    if link:
        video_id = _extrair_video_id(link) or ""
        if not video_id:
            return {"erro": "Link invalido. Use youtube.com/watch?v=..., youtu.be/... ou deixe link vazio e preencha o título manual"}

    with _lock:
        store = _carregar()
        itens = store.setdefault("itens", [])

        # Se data foi informada explicitamente, valida formato
        if data:
            try:
                datetime.strptime(data, "%d/%m/%Y")
            except Exception:
                return {"erro": "Data invalida. Use formato DD/MM/YYYY"}
            data_final = data
        else:
            data_final = _proxima_data_sequencial(itens)

        novo = {
            "id": _gerar_id(),
            "data": data_final,
            "titulo": titulo_clean,
            "texto_thumb": (texto_thumb or "").strip(),
            "link": link,
            "video_id": video_id,
            "geral": "",
            "co": "",
            "criado_em": datetime.now().isoformat(timespec="seconds"),
            "manual": not bool(link),  # marca como tema manual (sem link)
        }
        itens.append(novo)
        _salvar(store)
        return novo


def listar(geral: str = None, co: str = None, incluir_concluidos: bool = True) -> list:
    """Lista itens ordenados por data ASC.

    geral / co: '' (pendente), 'Ok' (concluido), ou None (qualquer)
    incluir_concluidos: False oculta apenas itens com Geral=Ok E CO=Ok (ambos)
    """
    store = _carregar()
    itens = list(store.get("itens", []))

    if geral is not None:
        itens = [it for it in itens if (it.get("geral") or "") == geral]
    if co is not None:
        itens = [it for it in itens if (it.get("co") or "") == co]
    if not incluir_concluidos:
        itens = [it for it in itens
                 if not (it.get("geral") == "Ok" and it.get("co") == "Ok")]

    def _ord(it):
        try:
            return datetime.strptime(it.get("data", ""), "%d/%m/%Y").date()
        except Exception:
            return date.max
    itens.sort(key=_ord)
    return itens


def atualizar(item_id: str, **campos) -> dict | None:
    """Atualiza item. Aceita: titulo, texto_thumb, data, geral, co.
    Retorna item atualizado ou None se nao achou."""
    permitidos = {"titulo", "texto_thumb", "data", "geral", "co"}
    # Aceitar 'status' como alias legado de 'geral'
    if "status" in campos and "geral" not in campos:
        campos["geral"] = campos.pop("status")
    campos_validos = {k: v for k, v in campos.items() if k in permitidos}
    if not campos_validos:
        return None

    # Validar formato de data se foi alterada
    if "data" in campos_validos and campos_validos["data"]:
        try:
            datetime.strptime(campos_validos["data"], "%d/%m/%Y")
        except Exception:
            return {"erro": "Data invalida. Use formato DD/MM/YYYY"}

    # Normalizar geral/co (so aceita '' ou 'Ok')
    for k in ("geral", "co"):
        if k in campos_validos:
            v = (campos_validos[k] or "").strip()
            campos_validos[k] = "Ok" if v.lower() == "ok" else ""

    with _lock:
        store = _carregar()
        for it in store.get("itens", []):
            if it.get("id") == item_id:
                it.update(campos_validos)
                _salvar(store)
                return it
    return None


def remover(item_id: str) -> bool:
    with _lock:
        store = _carregar()
        itens = store.get("itens", [])
        novo = [it for it in itens if it.get("id") != item_id]
        if len(novo) == len(itens):
            return False
        store["itens"] = novo
        _salvar(store)
        return True


def obter(item_id: str) -> dict | None:
    for it in _carregar().get("itens", []):
        if it.get("id") == item_id:
            return it
    return None
