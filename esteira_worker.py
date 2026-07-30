"""Consumidor da esteira (formato PUXADO, Piter 30/07).

    render (4 workers) -> [fila] -> THUMB -> [fila] -> UPLOAD -> [fila] -> PIN -> done
                                    1 por vez         paralelo entre       1 por vez
                                    (Flow/Chrome)     canais, serial       (AdsPower)
                                                      DENTRO do canal

Threads deste processo:
  - thumb_loop : consome etapa='thumb' em FIFO, UM por vez (chrome_profile do
                 Flow e' unico). Projeto do Flow reusado POR DATA (thumb_pipeline).
  - upload_loop: agrupa etapa='upload' por canal YouTube; 1 thread por canal
                 (CO3/CO4 = mesmo canal = mesma thread). Canais diferentes sobem
                 em paralelo — cada um agenda na PROPRIA data+slot.
  - pin_loop   : consome etapa='pin' em FIFO, UM por vez (AdsPower nao aguenta
                 concorrencia). upload_verify --ensure-pin.

REGRAS DE OURO (Piter 30/07): thumb e pin NUNCA seguram a esteira.
  thumb falhou -> thumb_status='pendente', tema SEGUE pro upload sem thumb.
  pin falhou 2x -> pin_status='pendente', video CONTINUA agendado.
  Retry dos pendentes = upload_daily_check (1x/dia).

Roda pelo worker_watchdog (re-sobe se morrer). Heartbeat em _esteira/ — o
render_worker so enfileira se o heartbeat estiver fresco; senao cai no fluxo
inline legado (nada fica preso se este processo morrer).
"""
import json
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import esteira
import upload_trigger as ut

AQUI = Path(__file__).parent
CICLO_SEG = 15
PIN_MAX_TENTATIVAS = 2
PIN_RETRY_SEG = 600          # espera entre a 1a e a 2a tentativa de pin


def _log(m):
    print(f"[{datetime.now():%H:%M:%S}] {m}", flush=True)


def _config() -> dict:
    """Le worker_config.json FRESCO a cada uso (flags mudam sem restart)."""
    try:
        return json.loads((AQUI / "worker_config.json").read_text(encoding="utf-8"))
    except Exception:
        return {}


def _row_col_cache(cfg: dict, t: dict) -> tuple:
    """(row, col) da tarefa, resolvendo 1x e cacheando NO arquivo da tarefa
    (o /api/temas tem ~4MB; nao vale re-baixar a cada marca de status)."""
    if isinstance(t.get("row"), int) and isinstance(t.get("col"), int) and t["row"] >= 0:
        return t["row"], t["col"]
    vps = (cfg.get("vps_url") or "").strip()
    row, col = ut._row_col(vps, t["data"], t["alias"])
    t["row"], t["col"] = row, col
    esteira.salvar(t)
    return row, col


def _marcar(cfg: dict, t: dict, **campos):
    """Patch atomico da celula (status visivel na grade). Best-effort."""
    try:
        row, col = _row_col_cache(cfg, t)
        if row < 0 or col < 0:
            return
        vps = (cfg.get("vps_url") or "").strip()
        body = json.dumps({"row": row, "col": col, **campos}).encode()
        req = urllib.request.Request(f"{vps}/api/upload/mark", data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
    except Exception as e:
        _log(f"marca {t['alias']} {t['data']} falhou ({type(e).__name__}) — segue")


# ================================================================ THUMB (fila 1)
def _thumb_task(cfg: dict, t: dict, fila_n: int = 1):
    """Processa UMA tarefa do estagio thumb. Sempre avanca pra 'upload'."""
    _log(f"THUMB {t['alias']} {t['data']} (fila={fila_n})")
    _marcar(cfg, t, upload_status="fila:thumb")
    thumb = None
    try:
        import thumb_pipeline
        tcfg = (cfg.get("upload_trigger") or {})
        thumb = thumb_pipeline.gerar(
            t["alias"], t["data"],
            timeout_seg=int(tcfg.get("thumb_timeout", 900)))
    except Exception as e:
        _log(f"  thumb excecao ({type(e).__name__}: {e})")
    if thumb:
        t["thumb_path"], t["thumb_status"] = str(thumb), "ok"
        _log(f"  thumb OK: {Path(thumb).name}")
    else:
        # REGRA DE OURO: nao bloqueia — sobe sem thumb, retry no daily.
        t["thumb_status"] = "pendente"
        _log("  thumb PENDENTE — tema segue pro upload SEM thumb")
    t["etapa"] = "upload"
    esteira.salvar(t)


def thumb_loop():
    while True:
        try:
            fila = esteira.pendentes("thumb")
            if not fila:
                time.sleep(CICLO_SEG)
                continue
            _thumb_task(_config(), fila[0], len(fila))
        except Exception as e:
            _log(f"thumb_loop erro ({type(e).__name__}: {e}) — continua")
            time.sleep(CICLO_SEG)


# ================================================================ UPLOAD (por canal)
_canais_ativos: set = set()
_canais_lock = threading.Lock()


def _upload_task(cfg: dict, t: dict, fila_n: int = 1):
    """Processa UM upload. ok -> etapa 'pin'; falha -> 'falha' (dead-letter)."""
    _log(f"UPLOAD {t['alias']} {t['data']} (fila={fila_n})")
    _marcar(cfg, t, upload_status="fila:upload")
    r = ut.disparar_esteira(cfg, t["alias"], t["data"], t["video_path"],
                            thumb_path=t.get("thumb_path") or None,
                            titulo_esperado=t.get("titulo", ""))
    if r.get("ok"):
        t["video_id"] = r.get("video_id") or ""
        t["etapa"] = "pin"
        _log(f"  upload OK ({t['video_id'] or 'id no grid'}) -> fila do pin")
        # pendencia de thumb fica REGISTRADA no grid pro daily re-tentar
        if t.get("thumb_status") == "pendente":
            _marcar(cfg, t, thumb_status="pendente")
    else:
        # dead-letter ja registrado pelo trigger (falhou:*)
        t["erro"] = r.get("erro") or "upload falhou"
        t["etapa"] = "falha"
        _log(f"  upload FALHOU: {t['erro']}")
    esteira.salvar(t)


def _upload_canal(canal_yt: str):
    """Drena TODOS os uploads pendentes deste canal, FIFO, e sai."""
    try:
        while True:
            fila = [t for t in esteira.pendentes("upload")
                    if ut.SLOT_MAP.get(t["alias"], ("?",))[0] == canal_yt]
            if not fila:
                return
            _upload_task(_config(), fila[0], len(fila))
    finally:
        with _canais_lock:
            _canais_ativos.discard(canal_yt)


def upload_loop():
    while True:
        try:
            for t in esteira.pendentes("upload"):
                canal_yt = ut.SLOT_MAP.get(t["alias"], (None,))[0]
                if not canal_yt:
                    t["erro"], t["etapa"] = "alias sem SLOT_MAP", "falha"
                    esteira.salvar(t)
                    continue
                with _canais_lock:
                    if canal_yt in _canais_ativos:
                        continue          # ja tem thread drenando este canal
                    _canais_ativos.add(canal_yt)
                threading.Thread(target=_upload_canal, args=(canal_yt,),
                                 daemon=True, name=f"up-{canal_yt}").start()
        except Exception as e:
            _log(f"upload_loop erro ({type(e).__name__}: {e}) — continua")
        time.sleep(CICLO_SEG)


# ================================================================ PIN (fila 1)
def _pin_task(cfg: dict, t: dict, fila_n: int = 1):
    """Processa UMA tarefa do estagio pin. Nunca segura o tema alem de
    PIN_MAX_TENTATIVAS — video ja esta agendado (regra de ouro)."""
    agora = time.time()
    vid = t.get("video_id") or ""
    if not vid:
        vps = (cfg.get("vps_url") or "").strip()
        row, col = _row_col_cache(cfg, t)
        vid = ut._video_id_do_grid(vps, row, col)
        t["video_id"] = vid
    if not vid:
        t["pin_status"], t["etapa"] = "pendente", "done"
        _marcar(cfg, t, pin_status="pendente")
        _log(f"PIN {t['alias']} {t['data']}: sem video_id — pendente pro daily")
        esteira.salvar(t)
        return
    n = int((t.get("tentativas") or {}).get("pin", 0)) + 1
    t.setdefault("tentativas", {})["pin"] = n
    _log(f"PIN {t['alias']} {t['data']} vid={vid} (tentativa {n}, fila={fila_n})")
    _marcar(cfg, t, upload_status="fila:pin")
    res = ut.rodar_pin(cfg, t["alias"], t["data"], vid)
    if res.get("pinned"):
        t["pin_status"], t["etapa"] = "ok", "done"
        _marcar(cfg, t, upload_status="scheduled", pin_status="ok")
        _log("  pin OK — tema DONE")
    elif n >= PIN_MAX_TENTATIVAS:
        # REGRA DE OURO: video segue agendado; pendencia anotada.
        t["pin_status"], t["etapa"] = "pendente", "done"
        _marcar(cfg, t, upload_status="scheduled", pin_status="pendente")
        ut._telegram(cfg, f"📌 {t['alias']} {t['data']}: pin PENDENTE apos "
                          f"{n} tentativas — video segue agendado; daily re-tenta")
        _log(f"  pin PENDENTE ({n} tentativas) — tema DONE, daily re-tenta")
    else:
        t["nao_antes"] = agora + PIN_RETRY_SEG
        # o verify pode ter deixado o video ok mas sem pin; status volta
        # a refletir o estado real (agendado) enquanto espera o retry
        _marcar(cfg, t, upload_status="scheduled")
        _log(f"  pin falhou — re-tento em {PIN_RETRY_SEG // 60}min")
    esteira.salvar(t)


def pin_loop():
    while True:
        try:
            agora = time.time()
            fila = [t for t in esteira.pendentes("pin")
                    if float(t.get("nao_antes") or 0) <= agora]
            if not fila:
                time.sleep(CICLO_SEG)
                continue
            _pin_task(_config(), fila[0], len(fila))
        except Exception as e:
            _log(f"pin_loop erro ({type(e).__name__}: {e}) — continua")
            time.sleep(CICLO_SEG)


# ================================================================ main
def main():
    _log("=== esteira_worker up ===")
    for fn in (thumb_loop, upload_loop, pin_loop):
        threading.Thread(target=fn, daemon=True, name=fn.__name__).start()
    ultimo_prune = 0.0
    while True:
        esteira.bater_coracao()
        if time.time() - ultimo_prune > 86400:
            esteira.limpar_feitas(30)
            ultimo_prune = time.time()
        time.sleep(10)


if __name__ == "__main__":
    main()
