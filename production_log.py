"""
Log persistente de produção completa.
Salva estado em disco para sobreviver a F5/restart.
O Monitor lê daqui para mostrar status em tempo real.
"""

import json
import threading
import time
from pathlib import Path
from datetime import datetime

BASE_DIR = Path(__file__).parent
LOG_FILE = BASE_DIR / "production_state.json"
HISTORICO_DATAS_FILE = BASE_DIR / "historico_datas.json"  # lista de datas finalizadas
_lock = threading.RLock()  # RLock pois adicionar_log chama _salvar
_hist_lock = threading.Lock()

# Estado da produção completa (persistente)
_state = {
    "ativo": False,
    "data_ref": "",
    "data_idx": None,          # índice da linha no temas (para auto-resume)
    "ordem_colunas": None,     # ordem de produção (para auto-resume)
    "inicio": None,
    "total_canais": 0,
    "canal_atual": 0,
    "canais": [],  # lista de canais com status individual
    "log": [],     # log detalhado de cada ação
    "concluidos": 0,
    "erros": 0,
    "pulados": 0,
    "cancelado": False,
}


def _carregar():
    global _state
    if LOG_FILE.exists():
        try:
            with open(LOG_FILE, "r", encoding="utf-8") as f:
                _state = json.load(f)
        except Exception:
            pass


def _salvar():
    with _lock:
        try:
            with open(LOG_FILE, "w", encoding="utf-8") as f:
                json.dump(_state, f, ensure_ascii=False, default=str)
        except Exception:
            pass


def iniciar(data_ref: str, canais: list, data_idx: int = None, ordem_colunas: list = None):
    """Inicia uma nova produção completa."""
    global _state
    _state = {
        "ativo": True,
        "data_ref": data_ref,
        "data_idx": data_idx,
        "ordem_colunas": ordem_colunas,
        "inicio": time.time(),
        "total_canais": len(canais),
        "canal_atual": 0,
        "canais": [
            {
                "tag": c.get("tag", ""),
                "template": c.get("template", ""),
                "etapa": "aguardando",  # aguardando, roteiro, narracao, video, concluido, erro, pulado
                "etapa_detalhe": "",
                "progresso": 0,
                "inicio": None,
                "fim": None,
                "roteiro_chars": 0,
                "narracao_path": "",
                "video_path": "",
                "erro": "",
            }
            for c in canais
        ],
        "log": [{"ts": datetime.now().isoformat(), "msg": f"Produção iniciada: {data_ref} | {len(canais)} canais"}],
        "concluidos": 0,
        "erros": 0,
        "pulados": 0,
        "cancelado": False,
    }
    _salvar()


def atualizar_canal(index: int, **kwargs):
    """Atualiza estado de um canal específico. Thread-safe.

    NOTA: 'inicio' é PRESERVADO depois de setado pela primeira vez.
    Isso garante que `fim - inicio` = tempo total do canal, não da última etapa.
    Se quiser resetar (ex: retry), passe explicitamente inicio=None.
    """
    with _lock:
        if 0 <= index < len(_state.get("canais", [])):
            cel = _state["canais"][index]
            # Preserva inicio original se já foi setado (a menos que explicitamente None)
            if "inicio" in kwargs and kwargs["inicio"] is not None and cel.get("inicio"):
                kwargs.pop("inicio")
            cel.update(kwargs)
            _state["canal_atual"] = index
            _salvar()


def adicionar_log(msg: str):
    """Adiciona entrada no log. Thread-safe."""
    with _lock:
        _state.setdefault("log", []).append({"ts": datetime.now().isoformat(), "msg": msg})
        if len(_state["log"]) > 500:
            _state["log"] = _state["log"][-500:]
        _salvar()


def finalizar(cancelado: bool = False):
    """Marca produção como finalizada e registra em historico_datas.json."""
    _state["ativo"] = False
    _state["cancelado"] = cancelado
    concluidos = sum(1 for c in _state.get("canais", []) if c.get("etapa") == "concluido")
    erros = sum(1 for c in _state.get("canais", []) if c.get("etapa") == "erro")
    pulados = sum(1 for c in _state.get("canais", []) if c.get("etapa") in ("pulado", "aguardando"))
    _state["concluidos"] = concluidos
    _state["erros"] = erros
    _state["pulados"] = pulados
    inicio_ts = _state.get("inicio") or time.time()
    fim_ts = time.time()
    tempo = fim_ts - inicio_ts
    _state["fim"] = fim_ts
    _state["duracao_seg"] = tempo
    status = "CANCELADO" if cancelado else "CONCLUÍDO"
    adicionar_log(f"{status} | {concluidos} OK | {erros} erros | {pulados} pulados | {tempo:.0f}s total ({tempo/60:.1f}min)")

    # Persistir no historico_datas.json (append)
    try:
        with _hist_lock:
            hist = []
            if HISTORICO_DATAS_FILE.exists():
                try:
                    with open(HISTORICO_DATAS_FILE, "r", encoding="utf-8") as f:
                        hist = json.load(f)
                except Exception:
                    hist = []
            hist.append({
                "data_ref": _state.get("data_ref", ""),
                "data_idx": _state.get("data_idx"),
                "inicio_ts": inicio_ts,
                "inicio_iso": datetime.fromtimestamp(inicio_ts).isoformat(),
                "fim_ts": fim_ts,
                "fim_iso": datetime.fromtimestamp(fim_ts).isoformat(),
                "duracao_seg": tempo,
                "total_canais": _state.get("total_canais", 0),
                "concluidos": concluidos,
                "erros": erros,
                "pulados": pulados,
                "cancelado": cancelado,
            })
            # Limita a 200 entradas
            if len(hist) > 200:
                hist = hist[-200:]
            with open(HISTORICO_DATAS_FILE, "w", encoding="utf-8") as f:
                json.dump(hist, f, ensure_ascii=False, indent=2, default=str)
    except Exception:
        pass

    _salvar()


def obter_historico_datas(limit: int = 50) -> list:
    """Retorna ultimas N datas processadas, mais recente primeiro."""
    try:
        with _hist_lock:
            if not HISTORICO_DATAS_FILE.exists():
                return []
            with open(HISTORICO_DATAS_FILE, "r", encoding="utf-8") as f:
                hist = json.load(f)
            # Mais recente primeiro
            return list(reversed(hist[-limit:]))
    except Exception:
        return []


def obter_estado() -> dict:
    """Retorna estado atual (para o Monitor)."""
    _carregar()  # sempre ler do disco (caso outro processo atualizou)
    s = dict(_state)
    # Calcular tempo decorrido
    if s.get("inicio") and s.get("ativo"):
        s["tempo_decorrido"] = time.time() - s["inicio"]
    elif s.get("inicio"):
        # Calcular do último canal finalizado
        fins = [c.get("fim") for c in s.get("canais", []) if c.get("fim")]
        s["tempo_decorrido"] = (max(fins) if fins else time.time()) - s["inicio"]
    else:
        s["tempo_decorrido"] = 0
    return s


# Carregar estado existente na inicialização
_carregar()
