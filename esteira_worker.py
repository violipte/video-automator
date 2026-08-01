"""Consumidor da esteira (formato PUXADO, Piter 30/07).

    render (4 workers) -> [fila] -> THUMB -> [fila] -> UPLOAD -> [fila] -> PIN -> done
                                    1 por vez         paralelo entre       paralelo por
                                    (Flow/Chrome)     canais, serial       canal, MAX 5
                                                      DENTRO do canal      (AdsPower)

Threads deste processo:
  - thumb_loop : consome etapa='thumb' em FIFO, UM por vez (chrome_profile do
                 Flow e' unico). Projeto do Flow reusado POR DATA (thumb_pipeline).
  - upload_loop: agrupa etapa='upload' por canal YouTube; 1 thread por canal
                 (CO3/CO4 = mesmo canal = mesma thread). Canais diferentes sobem
                 em paralelo — cada um agenda na PROPRIA data+slot.
  - pin_loop   : espelho do upload — 1 thread por canal, PARALELO com cap
                 global pin_concurrency = 5 (regra Piter 31/07, validada com
                 teste real: 5 profiles + automacao CDP, 0 erros). O AdsPower
                 aguenta varios profiles abertos; as blindagens da automacao
                 sao o throttle da Local API + lock por profile + este cap.
                 upload_verify --ensure-pin.

REGRAS DE OURO (Piter 30/07): thumb e pin NUNCA seguram a esteira.
  thumb falhou -> thumb_status='pendente', tema SEGUE pro upload sem thumb.
  pin falhou 2x -> pin_status='pendente', video CONTINUA agendado.
  Retry dos pendentes = upload_daily_check (1x/dia).

Roda pelo worker_watchdog (re-sobe se morrer). Heartbeat em _esteira/ — o
render_worker so enfileira se o heartbeat estiver fresco; senao cai no fluxo
inline legado (nada fica preso se este processo morrer).
"""
import json
import re
import threading
import time
import urllib.request
from datetime import datetime
from pathlib import Path

import esteira
import upload_trigger as ut

AQUI = Path(__file__).parent
CICLO_SEG = 15
# JANELA DE PROCESSAMENTO do YouTube (evidencia 01/08, screenshot NPD 05/08):
# ate ~20-40min pos-upload o watch mostra "We're processing this video" e NEM
# RENDERIZA a secao de comentarios -> pin impossivel (falhava com timeout/menu
# vazio). Mesma descoberta do youtube-publish-app (delay 12-15min pos-upload).
# 1a tentativa em T+12min; retries a cada 15min; 3 tentativas = cobre T+42min.
PIN_DELAY_POS_UPLOAD_SEG = 720
PIN_MAX_TENTATIVAS = 3
PIN_RETRY_SEG = 900

# Erro TRANSITORIO de upload (rede/proxy/lock) -> re-tenta AQUI com backoff em
# vez de dead-letter direto (absorvido do youtube-publish-app, espec C2/C6 do
# amigo do Piter). Sem isto, um blip de proxy custava 1 DIA (daily check).
# NAO entram: "journal: estado indeterminado" (precisa C1/humano), mp4/titulo
# invalidos (re-tentar nao conserta) e rc!=0 do upload_one (o journal 'subindo'
# bloqueia retry cego de qualquer forma — e' a protecao anti-duplicata).
UPLOAD_TRANSIENT_RE = re.compile(
    r"canal ocupado|timeout|timed out|10060|10054|ECONN|EAI_AGAIN|urlopen|"
    r"network|proxy|socket|HTTP.?5\d\d|\b429\b|temporar", re.I)
UPLOAD_MAX_TENTATIVAS = 4
UPLOAD_BACKOFF_SEG = (120, 240, 480, 900)   # 2 -> 4 -> 8 -> 15 min


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


def _recarregar(t: dict, etapa: str):
    """Re-le a tarefa do disco e confirma que AINDA esta na etapa esperada.

    Fecha a corrida do salvar(): tarefa done e' escrita em feitas/ E DEPOIS
    apagada de tarefas/ — um scan que caiu nessa janela le o arquivo velho e
    re-processaria a tarefa (visto no teste do pool 31/07: pin duplo do ASH).
    Na janela os DOIS arquivos existem, entao a checagem decisiva e': se ha um
    registro em feitas/ com o MESMO 'criado', ESTA instancia ja terminou.
    ('criado' novo = tarefa re-enfileirada de verdade — essa pode rodar.)
    None = alguem ja processou; o chamador desiste em silencio."""
    nome = f"{t['alias'].upper()}_{t['data']}.json"
    ativo = esteira.TAREFAS / nome
    try:
        atual = json.loads(ativo.read_text(encoding="utf-8"))
    except (OSError, json.JSONDecodeError):
        return None                       # ja movida/apagada
    if atual.get("etapa") != etapa:
        return None
    feita = esteira.FEITAS / nome
    if feita.exists():
        try:
            f = json.loads(feita.read_text(encoding="utf-8"))
            if f.get("criado") == atual.get("criado"):
                return None               # mesma instancia ja concluida (janela do move)
        except (OSError, json.JSONDecodeError):
            pass
    return atual


# ================================================================ THUMB (fila 1)
def _thumb_task(cfg: dict, t: dict, fila_n: int = 1):
    """Processa UMA tarefa do estagio thumb. Sempre avanca pra 'upload'."""
    t = _recarregar(t, "thumb")
    if t is None:
        return
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
    """Processa UM upload. ok -> etapa 'pin'; falha -> 'falha' (dead-letter).
    Canal FORA do piloto de upload: a thumb ja foi gerada (e copiada pros
    Exports) — aqui o tema fecha como thumb_only, sem subir nada (Piter 31/07:
    thumb pra todos, upload automatico so' pros canais do piloto)."""
    t = _recarregar(t, "upload")
    if t is None:
        return
    canais_up = [c.strip().upper() for c in
                 ((cfg.get("upload_trigger") or {}).get("canais") or [])]
    if canais_up and t["alias"].upper() not in canais_up:
        t["etapa"], t["nota"] = "done", "thumb_only (canal fora do upload automatico)"
        # limpa o "fila:thumb" da celula — sem isso o badge ficava preso pra
        # sempre no grid (visto na auditoria 05/08). Upload manual = sem badge.
        _marcar(cfg, t, upload_status="")
        _log(f"UPLOAD {t['alias']} {t['data']}: fora do piloto — thumb_only, DONE")
        esteira.salvar(t)
        return
    _log(f"UPLOAD {t['alias']} {t['data']} (fila={fila_n})")
    _marcar(cfg, t, upload_status="fila:upload")
    r = ut.disparar_esteira(cfg, t["alias"], t["data"], t["video_path"],
                            thumb_path=t.get("thumb_path") or None,
                            titulo_esperado=t.get("titulo", ""))
    if r.get("ok"):
        t["video_id"] = r.get("video_id") or ""
        t["etapa"] = "pin"
        # pin so DEPOIS da janela de processamento do YouTube (ver constante)
        t["nao_antes"] = time.time() + PIN_DELAY_POS_UPLOAD_SEG
        _log(f"  upload OK ({t['video_id'] or 'id no grid'}) -> pin em "
             f"{PIN_DELAY_POS_UPLOAD_SEG // 60}min (janela de processamento)")
        # pendencia de thumb fica REGISTRADA no grid pro daily re-tentar
        if t.get("thumb_status") == "pendente":
            _marcar(cfg, t, thumb_status="pendente")
    else:
        erro = r.get("erro") or "upload falhou"
        t["erro"] = erro
        n = int((t.get("tentativas") or {}).get("upload", 0)) + 1
        t.setdefault("tentativas", {})["upload"] = n
        if UPLOAD_TRANSIENT_RE.search(erro) and n < UPLOAD_MAX_TENTATIVAS:
            espera = UPLOAD_BACKOFF_SEG[min(n - 1, len(UPLOAD_BACKOFF_SEG) - 1)]
            t["nao_antes"] = time.time() + espera
            _log(f"  upload falhou TRANSITORIO ({erro[:60]}) — re-tento em "
                 f"{espera // 60}min (tentativa {n}/{UPLOAD_MAX_TENTATIVAS})")
        else:
            # dead-letter ja registrado pelo trigger (falhou:*); daily re-tenta
            t["etapa"] = "falha"
            _log(f"  upload FALHOU terminal: {erro}")
    esteira.salvar(t)


def _uploads_prontos() -> list:
    """etapa='upload' cujo backoff (nao_antes) ja venceu."""
    agora = time.time()
    return [t for t in esteira.pendentes("upload")
            if float(t.get("nao_antes") or 0) <= agora]


def _upload_canal(canal_yt: str):
    """Drena TODOS os uploads pendentes deste canal, FIFO, e sai."""
    try:
        while True:
            fila = [t for t in _uploads_prontos()
                    if ut.slot_map(_config()).get(t["alias"], ("?",))[0] == canal_yt]
            if not fila:
                return
            _upload_task(_config(), fila[0], len(fila))
    finally:
        with _canais_lock:
            _canais_ativos.discard(canal_yt)


def upload_loop():
    while True:
        try:
            for t in _uploads_prontos():
                canal_yt = ut.slot_map(_config()).get(t["alias"], (None,))[0]
                if not canal_yt:
                    t["erro"], t["etapa"] = "alias sem slot (cadastro do canal)", "falha"
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
    t = _recarregar(t, "pin")
    if t is None:
        return
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
    row, col = _row_col_cache(cfg, t)     # pin SEMPRE pelo commentID do grid
    res = ut.rodar_pin(cfg, t["alias"], t["data"], vid, row=row, col=col)
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


# PARALELIZACAO (Piter 31/07): o AdsPower aguenta varios profiles ABERTOS —
# quem precisava de blindagem era a automacao (throttle da Local API +
# lock por profile no verify + cap global pin_slot). Com as blindagens no
# lugar, o pin espelha o upload: 1 thread por CANAL (CO3/CO4 = mesmo canal =
# mesmo profile = mesma thread), cap global de pin_concurrency (default 3).
_pins_ativos: set = set()
_pins_lock = threading.Lock()


def _pins_prontos() -> list:
    agora = time.time()
    return [t for t in esteira.pendentes("pin")
            if float(t.get("nao_antes") or 0) <= agora]


def _pin_canal(canal_yt: str):
    """Drena TODOS os pins pendentes deste canal, FIFO, e sai."""
    try:
        while True:
            fila = [t for t in _pins_prontos()
                    if ut.slot_map(_config()).get(t["alias"], ("?",))[0] == canal_yt]
            if not fila:
                return
            _pin_task(_config(), fila[0], len(fila))
    finally:
        with _pins_lock:
            _pins_ativos.discard(canal_yt)


def pin_loop():
    while True:
        try:
            # REGRA (Piter 31/07, validada com teste real 5/5): pin PARALELO,
            # maximo 5 por vez. O cap le do config a cada ciclo (muda sem restart).
            cap = int((_config().get("upload_trigger") or {}).get("pin_concurrency", 5))
            for t in _pins_prontos():
                canal_yt = ut.slot_map(_config()).get(t["alias"], (None,))[0]
                if not canal_yt:
                    continue          # _pin_task marca pendente na sua vez
                with _pins_lock:
                    if canal_yt in _pins_ativos or len(_pins_ativos) >= cap:
                        continue      # canal ja em pin, ou cap global cheio
                    _pins_ativos.add(canal_yt)
                threading.Thread(target=_pin_canal, args=(canal_yt,),
                                 daemon=True, name=f"pin-{canal_yt}").start()
        except Exception as e:
            _log(f"pin_loop erro ({type(e).__name__}: {e}) — continua")
        time.sleep(CICLO_SEG)


# ================================================================ main
def _instancia_unica():
    """Lock de instancia (C7 do youtube-publish-app): 2 esteira_workers vivos
    (ex.: um manual + um do watchdog) pegariam a MESMA tarefa ao mesmo tempo.
    O_CREAT|O_EXCL atomico; lock de processo morto (heartbeat frio) e' roubado."""
    import os
    import sys
    lock = esteira.DIR / "worker.lock"
    esteira.DIR.mkdir(parents=True, exist_ok=True)
    for _ in range(2):
        try:
            fd = os.open(str(lock), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
            os.close(fd)
            return
        except FileExistsError:
            if esteira.worker_vivo(120):     # dono do lock esta batendo coracao
                _log("ja existe um esteira_worker VIVO — saindo (instancia unica)")
                sys.exit(0)
            _log("lock de instancia orfao (heartbeat frio) — assumindo")
            lock.unlink(missing_ok=True)
    _log("nao consegui o lock de instancia — saindo")
    sys.exit(1)


def main():
    _instancia_unica()
    _log("=== esteira_worker up ===")
    for fn in (thumb_loop, upload_loop, pin_loop):
        threading.Thread(target=fn, daemon=True, name=fn.__name__).start()
    ultimo_prune = 0.0
    try:
        while True:
            esteira.bater_coracao()
            if time.time() - ultimo_prune > 86400:
                esteira.limpar_feitas(30)
                ultimo_prune = time.time()
            time.sleep(10)
    finally:
        (esteira.DIR / "worker.lock").unlink(missing_ok=True)


if __name__ == "__main__":
    main()
