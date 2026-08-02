"""Rastreador PRECISO de tempo de produção por data.

Observa production_state.json (thread daemon) e mantém tempos_producao.json com,
por data:
  - tempo_total_seg (wall-clock): 1º início -> fim real. INCLUI travamentos e
    o tempo parada entre restarts. NÃO zera com restart — é "a soma".
  - tempo_ativo_seg: só trabalho real (desconta travado + downtime).
  - tempo_travado_seg: quanto ficou parada (wedge / processo fora do ar).
  - sessoes: cada período contínuo vivo (mostra restarts).

Robusto a: restart do serviço (nova sessão, mantém 1º início), crash no meio
(fecha sessão pelo último heartbeat), travamento com processo vivo (detecta log
parado > STALL) e processo fora do ar (gap entre ticks > GAP_MAX vira nova sessão).
"""
import json
import threading
import time
from pathlib import Path
from datetime import datetime

BASE = Path(__file__).parent
STATE_FILE = BASE / "production_state.json"
LEDGER_FILE = BASE / "tempos_producao.json"

HB = 20         # intervalo do heartbeat (s)
GAP_MAX = 90    # gap entre ticks > isso = processo esteve fora do ar/congelado -> nova sessão
STALL = 600     # sem NENHUM avanço (log+canais) por > isso (processo vivo) = travado. 10min
                # cobre um render/narração silencioso; wedge real (nada progride) passa disso.

_lock = threading.Lock()
_thread = None
_running = False


def _load():
    if LEDGER_FILE.exists():
        for _ in range(2):
            try:
                with open(LEDGER_FILE, "r", encoding="utf-8") as f:
                    return json.load(f)
            except Exception:
                time.sleep(0.05)
    return {}


def _save(d):
    tmp = LEDGER_FILE.with_suffix(".tmp")
    try:
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(d, f, ensure_ascii=False, indent=2, default=str)
        tmp.replace(LEDGER_FILE)
    except Exception:
        pass


def _read_state():
    for _ in range(2):
        try:
            with open(STATE_FILE, "r", encoding="utf-8") as f:
                return json.load(f)
        except Exception:
            time.sleep(0.05)
    return {}


def _iso(ts):
    return datetime.fromtimestamp(ts).isoformat(timespec="seconds")


def _log_marker(st):
    """Marcador de progresso. Muda = houve atividade. Inclui o ESTADO dos canais
    (etapa + progresso + detalhe) além do log, pra captar render/narração em
    andamento (que atualizam progresso mas logam pouco) e não contar como travado."""
    log = st.get("log") or []
    ult = log[-1].get("ts") if log else ""
    canais = st.get("canais", [])
    conc = st.get("concluidos")
    if conc is None:
        conc = sum(1 for c in canais if c.get("etapa") == "concluido")
    # assinatura do estado dos canais (progresso de render/narração muda aqui)
    sig = "".join(
        f"{c.get('etapa','')}~{c.get('progresso','')}~{str(c.get('etapa_detalhe',''))[:30]};"
        for c in canais
    )
    return f"{len(log)}|{ult}|{conc}|{hash(sig)}"


def _sessao_aberta(rec):
    for s in rec["sessoes"]:
        if s.get("fim_ts") is None:
            return s
    return None


def _fechar(rec, ts):
    s = _sessao_aberta(rec)
    if s:
        s["fim_ts"] = ts
        s["fim_iso"] = _iso(ts)


def _recalc(rec):
    ativo = 0.0
    travado = 0.0
    ends = []
    for s in rec["sessoes"]:
        fim = s.get("fim_ts") or s.get("ultimo_hb_ts") or s["inicio_ts"]
        span = max(0.0, fim - s["inicio_ts"])
        st_seg = s.get("stall_seg", 0.0)
        ativo += max(0.0, span - st_seg)
        travado += st_seg
        ends.append(fim)
    fim_geral = max(ends) if ends else rec["inicio_ts"]
    downtime = max(0.0, (fim_geral - rec["inicio_ts"]) - sum(
        (s.get("fim_ts") or s.get("ultimo_hb_ts") or s["inicio_ts"]) - s["inicio_ts"] for s in rec["sessoes"]))
    rec["tempo_total_seg"] = round(max(0.0, fim_geral - rec["inicio_ts"]), 1)
    rec["tempo_ativo_seg"] = round(ativo, 1)
    rec["tempo_travado_seg"] = round(travado + downtime, 1)
    rec["n_sessoes"] = len(rec["sessoes"])
    rec["fim_iso"] = _iso(fim_geral)


def _nova_sessao(rec, agora, marker):
    rec["sessoes"].append({
        "inicio_ts": agora, "inicio_iso": _iso(agora),
        "ultimo_hb_ts": agora, "fim_ts": None,
        "ultimo_progresso_ts": agora, "marker": marker, "stall_seg": 0.0,
    })


def _tick():
    st = _read_state()
    ativo = bool(st.get("ativo"))
    data_ref = st.get("data_ref") or ""
    agora = time.time()
    marker = _log_marker(st)

    with _lock:
        d = _load()

        # data que tem sessão aberta atualmente (se houver)
        aberta_ref = None
        for k, rec in d.items():
            if _sessao_aberta(rec):
                aberta_ref = k
                break

        if not (ativo and data_ref):
            # produção parada -> fecha sessão aberta
            if aberta_ref:
                _fechar(d[aberta_ref], agora)
                _recalc(d[aberta_ref])
                _save(d)
            return

        # mudou de data -> fecha a anterior
        if aberta_ref and aberta_ref != data_ref:
            _fechar(d[aberta_ref], agora)
            _recalc(d[aberta_ref])
            aberta_ref = None

        rec = d.get(data_ref)
        if not rec:
            rec = {"data_ref": data_ref, "inicio_ts": agora, "inicio_iso": _iso(agora), "sessoes": []}
            d[data_ref] = rec

        s = _sessao_aberta(rec)
        if s is None:
            _nova_sessao(rec, agora, marker)
        else:
            gap = agora - s.get("ultimo_hb_ts", agora)
            if gap > GAP_MAX:
                # processo esteve fora do ar/congelado -> fecha sessão antiga e abre nova
                _fechar(rec, s.get("ultimo_hb_ts", agora))
                _nova_sessao(rec, agora, marker)
            else:
                if marker != s.get("marker"):
                    s["marker"] = marker
                    s["ultimo_progresso_ts"] = agora
                else:
                    # sem progresso: se já passou STALL desde o último avanço, conta como travado
                    if agora - s.get("ultimo_progresso_ts", agora) > STALL:
                        s["stall_seg"] = s.get("stall_seg", 0.0) + gap
                s["ultimo_hb_ts"] = agora

        _recalc(rec)
        _save(d)


def _loop():
    global _running
    while _running:
        try:
            _tick()
        except Exception:
            pass
        time.sleep(HB)


def iniciar_tracker():
    """Sobe a thread daemon do rastreador (idempotente)."""
    global _thread, _running
    if _running:
        return
    _running = True
    _thread = threading.Thread(target=_loop, daemon=True, name="tempo_tracker")
    _thread.start()


def resumo(limit: int = 60) -> list:
    """Ledger por data, mais recente primeiro, com tempos formatados."""
    d = _load()
    itens = []
    for ref, rec in d.items():
        itens.append({
            "data_ref": ref,
            "inicio": rec.get("inicio_iso"),
            "fim": rec.get("fim_iso"),
            "tempo_total_min": round((rec.get("tempo_total_seg") or 0) / 60, 1),
            "tempo_ativo_min": round((rec.get("tempo_ativo_seg") or 0) / 60, 1),
            "tempo_travado_min": round((rec.get("tempo_travado_seg") or 0) / 60, 1),
            "n_sessoes": rec.get("n_sessoes", 0),
            "em_andamento": _sessao_aberta(rec) is not None,
        })
    itens.sort(key=lambda x: x.get("inicio") or "", reverse=True)
    return itens[:limit]
