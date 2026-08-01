# -*- coding: utf-8 -*-
"""CHECKUP DE CTA (regra dura, Piter 01/08): garante por video agendado —
  1 CTA exatamente (copias extras APAGADAS, mantendo a fixada > cid do grid >
  a mais antiga), grid com o commentID certo, e pin PROVADO (pos-prova Unpin).

Uso:
  python cta_checkup.py --rows 118-122            # padrao: aplica
  python cta_checkup.py --rows 118 --canais ENO   # recorte

Desenho anti-reprocessamento: UM ciclo de flip por video (o worker de dedup
deixa unlisted; o pin reagenda no fim; fallback so-reagendar garante o
agendamento em qualquer erro). Pausas aleatorias (pool, nao cravadas).
Pula tarefas em voo na esteira (upload/pin do proprio worker).
"""
import argparse
import glob
import json
import os
import random
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import upload_trigger as ut

AQUI = Path(__file__).parent
WORKER = AQUI / "cta_dedup_worker.py"


def _cfg():
    return json.loads((AQUI / "worker_config.json").read_text(encoding="utf-8"))


def _em_voo():
    s = set()
    for f in glob.glob(str(AQUI / "_esteira" / "tarefas" / "*.json")):
        try:
            t = json.loads(open(f, encoding="utf-8").read())
            s.add((t.get("alias"), t.get("data")))
        except Exception:
            pass
    return s


def _marcar(vps, row, col, **campos):
    body = json.dumps({"row": row, "col": col, **campos}).encode()
    req = urllib.request.Request(f"{vps}/api/upload/mark", data=body,
                                 headers={"Content-Type": "application/json"})
    urllib.request.urlopen(req, timeout=30).read()


def _worker(repo, canal_yt, vid, pub, cid, modo):
    p = subprocess.run(
        [str(repo / "venv" / "Scripts" / "python.exe"), "-u", str(WORKER),
         vid, pub, cid, modo],
        cwd=str(repo), capture_output=True, text=True, encoding="utf-8",
        errors="replace", timeout=420,
        env=dict(os.environ, CHANNEL_ALIAS=canal_yt, PYTHONUTF8="1"))
    for ln in (p.stdout or "").splitlines():
        if ln.startswith("__CHECKUP__"):
            return json.loads(ln[len("__CHECKUP__"):])
    return {}


def checar_video(cfg, vps, repo, row, col, alias, data_iso, vid, cid):
    """Retorna 'ok' | 'pendente' | 'erro:<x>'. Nunca levanta."""
    canal_yt, tz, slot = ut.slot_map(cfg)[alias]
    pub = ut._publish_utc(data_iso, tz, slot)

    res = _worker(repo, canal_yt, vid, pub, cid, "checar")
    if not res:
        _worker(repo, canal_yt, vid, pub, "", "so-reagendar")
        return "erro:worker"
    alvo = res.get("alvo") or cid
    if res.get("apagados"):
        print(f"  dedup: apagados {res['apagados']} (mantido {alvo})", flush=True)
        time.sleep(random.uniform(60, 120))     # indexacao pos-delete
    if alvo and alvo != cid:
        try:
            _marcar(vps, row, col, youtube_comment_id=alvo)
            print(f"  grid cid -> {alvo}", flush=True)
        except Exception:
            pass

    if res.get("fixado") and res["fixado"] == alvo:
        # ja esta fixado no alvo — so fecha o agendamento
        r2 = _worker(repo, canal_yt, vid, pub, "", "so-reagendar")
        if r2.get("reagendado"):
            _marcar(vps, row, col, pin_status="ok")
            return "ok"
        return "erro:reagendar"

    pin = {}
    try:
        pin = ut.rodar_pin(cfg, alias, data_iso, vid, row, col)
    except Exception as e:
        print(f"  rodar_pin EXC {type(e).__name__}", flush=True)
    if not pin.get("publish_at"):
        _worker(repo, canal_yt, vid, pub, "", "so-reagendar")
        print("  fallback: agendamento garantido", flush=True)
    if pin.get("pinned"):
        _marcar(vps, row, col, pin_status="ok")
        return "ok"
    _marcar(vps, row, col, pin_status="pendente")
    return "pendente"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--rows", required=True, help="ex: 118-122 ou 120")
    ap.add_argument("--canais", default="", help="CSV de aliases; vazio = todos")
    a = ap.parse_args()
    lo, _, hi = a.rows.partition("-")
    rows = list(range(int(lo), int(hi or lo) + 1))
    filtro = {c.strip().upper() for c in a.canais.split(",") if c.strip()}

    cfg = _cfg()
    vps = (cfg.get("vps_url") or "http://85.239.243.215:8500").strip()
    repo = Path((cfg.get("upload_trigger") or {}).get("repo")
                or "F:/Canal Dark/Apps Rapidos/drive-to-youtube")

    with urllib.request.urlopen(f"{vps}/api/temas", timeout=40) as r:
        d = json.loads(r.read().decode())
    cols = {i: (c.get("nome") or "").strip() for i, c in enumerate(d["colunas"])}
    linhas = d.get("linhas") or []
    smap = ut.slot_map(cfg)

    placar = {"ok": [], "pendente": [], "erro": [], "pulado": []}
    for row in rows:
        data_iso = ""
        try:
            bruto = (linhas[row].get("data") or "").strip()   # "05/08/2026" (BR)
            if "/" in bruto:
                dd, mm, yy = bruto.split("/")
                data_iso = f"{yy}-{mm}-{dd}"
            else:
                data_iso = bruto                              # ja ISO
        except Exception:
            pass
        for i, alias in cols.items():
            if filtro and alias.upper() not in filtro:
                continue
            cel = d["celulas"].get(f"{row}_{i}") or {}
            vid = cel.get("youtube_video_id")
            if not vid or alias not in smap:
                continue
            dt = data_iso or cel.get("data") or ""
            if not dt:
                placar["erro"].append(f"{alias} row{row}: sem data")
                continue
            if (alias, dt) in _em_voo():
                placar["pulado"].append(f"{alias} {dt[5:]}")
                continue
            print(f"\n=== {alias} {dt[5:]} ({vid}) ===", flush=True)
            r = checar_video(cfg, vps, repo, row, i, alias, dt, vid,
                             cel.get("youtube_comment_id") or "")
            chave = r.split(":")[0] if r.startswith("erro") else r
            placar[chave if chave in placar else "erro"].append(f"{alias} {dt[5:]}")
            print(f"  -> {r}", flush=True)
            time.sleep(random.uniform(4, 11))

    print("\n===== PLACAR CHECKUP =====", flush=True)
    for k, v in placar.items():
        if v:
            print(f"{k}: {len(v)} {v}", flush=True)


if __name__ == "__main__":
    main()
