"""
Métricas persistentes de TTS (Minimax via ai33.pro + Inworld fallback).

Cada chamada TTS gera entradas em logs/tts-events.jsonl:
  {ts, tag, provider, event, ...campos extras}

Eventos:
  - "start"    : iniciou a chamada
  - "ok"       : sucesso (com duration_s e chars)
  - "timeout"  : timeout (duration_s)
  - "error"    : erro de API (erro)
  - "fallback" : marcado quando provider muda do primário pro fallback

Uso (no narrator.py / narrator_inworld.py):
    import tts_metrics
    tts_metrics.evento(tag="CON", provider="minimax", event="start", chars=24478)
    ...
    tts_metrics.evento(tag="CON", provider="minimax", event="timeout", duration_s=312)

Endpoint GET /api/tts/health agrega últimas N horas.
"""
import json
import os
import threading
import time
from datetime import datetime, timedelta
from pathlib import Path

LOGS_DIR = Path(__file__).parent / "logs"
LOGS_DIR.mkdir(parents=True, exist_ok=True)
TTS_LOG_FILE = LOGS_DIR / "tts-events.jsonl"

_lock = threading.Lock()


def evento(tag: str, provider: str, event: str, **extras):
    """Registra um evento TTS. Thread-safe, append atômico em JSONL."""
    rec = {
        "ts": datetime.now().isoformat(timespec="seconds"),
        "tag": tag or "?",
        "provider": provider or "?",
        "event": event,
        **extras,
    }
    line = json.dumps(rec, ensure_ascii=False)
    with _lock:
        try:
            with open(TTS_LOG_FILE, "a", encoding="utf-8") as f:
                f.write(line + "\n")
        except Exception:
            pass  # nunca quebra a produção por causa de log


def carregar_eventos(horas: float = 24.0):
    """Carrega eventos das últimas N horas (default 24h)."""
    if not TTS_LOG_FILE.exists():
        return []
    cutoff = datetime.now() - timedelta(hours=horas)
    out = []
    with _lock:
        try:
            with open(TTS_LOG_FILE, "r", encoding="utf-8") as f:
                for line in f:
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        rec = json.loads(line)
                        ts = datetime.fromisoformat(rec.get("ts", ""))
                        if ts >= cutoff:
                            out.append(rec)
                    except Exception:
                        continue
        except Exception:
            return []
    return out


def health(horas: float = 24.0) -> dict:
    """Retorna agregado das últimas N horas:
       {janela_h, total_iniciados, sucesso_minimax, fallback_inworld, taxa_fallback_pct,
        timeouts_minimax, p50_minimax_s, p50_inworld_s, ultimos: [...últimos 20 eventos]}
    """
    eventos = carregar_eventos(horas=horas)
    if not eventos:
        return {
            "janela_h": horas, "total_iniciados": 0, "sucesso_minimax": 0,
            "fallback_inworld": 0, "taxa_fallback_pct": 0.0,
            "timeouts_minimax": 0, "p50_minimax_s": None, "p50_inworld_s": None,
            "ultimos": [],
        }

    starts_mini = [e for e in eventos if e.get("provider") == "minimax" and e.get("event") == "start"]
    ok_mini = [e for e in eventos if e.get("provider") == "minimax" and e.get("event") == "ok"]
    timeouts_mini = [e for e in eventos if e.get("provider") == "minimax" and e.get("event") == "timeout"]
    err_mini = [e for e in eventos if e.get("provider") == "minimax" and e.get("event") == "error"]

    ok_inworld = [e for e in eventos if e.get("provider") == "inworld" and e.get("event") == "ok"]
    err_inworld = [e for e in eventos if e.get("provider") == "inworld" and e.get("event") == "error"]
    fallbacks = [e for e in eventos if e.get("event") == "fallback" or e.get("fallback_from")]

    durations_mini = [e.get("duration_s", 0) for e in ok_mini if e.get("duration_s")]
    durations_inw = [e.get("duration_s", 0) for e in ok_inworld if e.get("duration_s")]

    def p50(lst):
        if not lst:
            return None
        s = sorted(lst)
        return round(s[len(s) // 2], 1)

    total = len(starts_mini)
    fb_count = len(ok_inworld)  # cada Inworld OK é um fallback (Inworld só é chamado como fallback)
    taxa_fb = (fb_count / total * 100) if total else 0

    # Por canal/dia (últimos 7 dias)
    por_dia = {}
    for e in eventos:
        dia = e.get("ts", "")[:10]
        por_dia.setdefault(dia, {"ok_minimax": 0, "fallback_inworld": 0, "timeouts": 0})
        if e.get("provider") == "minimax" and e.get("event") == "ok":
            por_dia[dia]["ok_minimax"] += 1
        elif e.get("provider") == "minimax" and e.get("event") == "timeout":
            por_dia[dia]["timeouts"] += 1
        elif e.get("provider") == "inworld" and e.get("event") == "ok":
            por_dia[dia]["fallback_inworld"] += 1

    return {
        "janela_h": horas,
        "total_iniciados": total,
        "sucesso_minimax": len(ok_mini),
        "fallback_inworld": fb_count,
        "taxa_fallback_pct": round(taxa_fb, 1),
        "timeouts_minimax": len(timeouts_mini),
        "errors_minimax": len(err_mini),
        "p50_minimax_s": p50(durations_mini),
        "p50_inworld_s": p50(durations_inw),
        "por_dia": por_dia,
        "ultimos": eventos[-20:],
    }
