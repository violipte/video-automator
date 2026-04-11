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
_lock = threading.RLock()  # RLock pois adicionar_log chama _salvar

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
    """Atualiza estado de um canal específico. Thread-safe."""
    with _lock:
        if 0 <= index < len(_state.get("canais", [])):
            _state["canais"][index].update(kwargs)
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
    """Marca produção como finalizada."""
    _state["ativo"] = False
    _state["cancelado"] = cancelado
    concluidos = sum(1 for c in _state.get("canais", []) if c.get("etapa") == "concluido")
    erros = sum(1 for c in _state.get("canais", []) if c.get("etapa") == "erro")
    pulados = sum(1 for c in _state.get("canais", []) if c.get("etapa") in ("pulado", "aguardando"))
    _state["concluidos"] = concluidos
    _state["erros"] = erros
    _state["pulados"] = pulados
    tempo = time.time() - (_state.get("inicio") or time.time())
    status = "CANCELADO" if cancelado else "CONCLUÍDO"
    adicionar_log(f"{status} | {concluidos} OK | {erros} erros | {pulados} pulados | {tempo:.0f}s total")
    _salvar()


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
