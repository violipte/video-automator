"""Rede de seguranca diaria do upload: varre o grid e resolve pendencias.

Roda 1x/dia (cron/agendador). Tres coisas:

  1. `falhou:*`   -> RE-TENTA o upload (dead-letter). Sem isso uma falha
                     transitoria (proxy, 5xx) perde o video em silencio.
  2. `incompleto` -> RECONCILIA (thumb/playlist/CTA/pin/agendamento que faltou).
                     Nao re-sobe: o video ja existe, so completa.
  3. `scheduled` com publishAt no passado -> CONFERE se virou `public` de fato.
                     Se nao virou (agendamento perdido, bloqueio, strike),
                     avisa no Telegram — antes so se descobriria olhando o canal.

Uso:
    python upload_daily_check.py             # so relata (dry-run)
    python upload_daily_check.py --apply     # corrige de verdade
    python upload_daily_check.py --apply --canais ENO2,NPD
"""
import argparse
import json
import os
import subprocess
import sys
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, str(Path(__file__).parent))
import upload_trigger as ut   # SLOT_MAP, _publish_utc, _reconciliar, _telegram

VPS = "http://85.239.243.215:8500"


def _log(m):
    print(f"[daily] {m}", flush=True)


def _cfg():
    p = Path(__file__).parent / "worker_config.json"
    return json.loads(p.read_text(encoding="utf-8")) if p.exists() else {}


def _grid():
    with urllib.request.urlopen(f"{VPS}/api/temas", timeout=60) as r:
        return json.loads(r.read().decode("utf-8"))


def _publico(repo: Path, canal_yt: str, video_id: str):
    """(privacy, publishAt) do YouTube. (None, None) se nao der pra ler."""
    code = ("import sys,json;sys.path.insert(0,'.');from lib import youtube;"
            "it=youtube.youtube().videos().list(part='status',id=sys.argv[1])"
            ".execute()['items'][0]['status'];"
            "print('__ST__'+json.dumps({'p':it.get('privacyStatus'),'a':it.get('publishAt')}))")
    try:
        r = subprocess.run([str(repo / "venv" / "Scripts" / "python.exe"), "-c", code, video_id],
                           cwd=str(repo), capture_output=True, text=True, encoding="utf-8",
                           errors="replace", timeout=180,
                           env=dict(os.environ, CHANNEL_ALIAS=canal_yt, PYTHONUTF8="1"))
        for ln in (r.stdout or "").splitlines():
            if ln.startswith("__ST__"):
                o = json.loads(ln[6:])
                return o.get("p"), o.get("a")
    except Exception as e:
        _log(f"  leitura do YouTube falhou: {type(e).__name__}")
    return None, None


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--apply", action="store_true")
    ap.add_argument("--canais", default="", help="CSV; vazio = todos do cadastro (painel/SLOT_MAP)")
    a = ap.parse_args()

    cfg = _cfg()
    repo = Path((cfg.get("upload_trigger") or {}).get("repo")
                or "F:/Canal Dark/Apps Rapidos/drive-to-youtube")
    filtro = {c.strip().upper() for c in a.canais.split(",") if c.strip()}

    g = _grid()
    linhas, colunas, celulas = g["linhas"], g["colunas"], g["celulas"]
    col_de = {(c.get("nome") or "").strip().upper(): i for i, c in enumerate(colunas)}
    agora = datetime.now(timezone.utc)

    # Rede de seguranca: tarefa de canal MANUAL morta como "alias sem slot"
    # (bug do upload_loop corrigido 01/08; worker antigo ainda pode gerar ate'
    # reciclar) — fecha como thumb_only e limpa o badge do grid.
    import glob as _glob
    for _f in _glob.glob(str(Path(__file__).parent / "_esteira" / "*" / "*.json")):
        try:
            _t = json.loads(open(_f, encoding="utf-8").read())
        except Exception:
            continue
        if _t.get("etapa") == "falha" and "sem slot" in str(_t.get("erro", "")):
            _t["etapa"], _t["nota"], _t["erro"] = (
                "done", "thumb_only (canal fora do upload automatico)", "")
            open(_f, "w", encoding="utf-8").write(
                json.dumps(_t, ensure_ascii=False, indent=1))
            if a.apply and _t.get("row", -1) >= 0:
                try:
                    import urllib.request as _ur
                    _body = json.dumps({"row": _t["row"], "col": _t["col"],
                                        "upload_status": ""}).encode()
                    _req = _ur.Request(
                        "http://85.239.243.215:8500/api/upload/mark",
                        data=_body, headers={"Content-Type": "application/json"})
                    _ur.urlopen(_req, timeout=30).read()
                except Exception as _e:
                    _log(f"reparo sem-slot: badge nao limpou ({type(_e).__name__}) — segue")
            _log(f"reparo sem-slot: {_t.get('alias')} {_t.get('data')} -> done thumb_only")

    falhou, incompleto, nao_publicou, ok = [], [], [], 0
    thumb_pend, pin_pend, fila_presa = [], [], []

    for alias, (canal_yt, tz, slot) in ut.slot_map(cfg).items():
        if filtro and alias not in filtro:
            continue
        col = col_de.get(alias)
        if col is None:
            continue
        for row, l in enumerate(linhas):
            cel = celulas.get(f"{row}_{col}") or {}
            st = (cel.get("upload_status") or "").strip()
            if not st:
                continue
            data_br = (l.get("data") or "").strip()
            try:
                d, m, y = data_br.split("/")
                data_iso = f"{y}-{m}-{d}"
            except ValueError:
                continue
            vid = cel.get("youtube_video_id") or ""
            item = (alias, canal_yt, row, col, data_iso, vid, tz, slot, st)

            # Pendencias da esteira: thumb/pin nunca bloquearam a publicacao
            # (regra Piter 30/07) — o video esta agendado e ISSO aqui fecha o resto.
            if (cel.get("thumb_status") or "") == "pendente":
                thumb_pend.append(item)
            if (cel.get("pin_status") or "") == "pendente":
                pin_pend.append(item)
            if st.startswith("fila:"):
                fila_presa.append(item)   # esteira_worker parado? (so alerta)

            if st.startswith("falhou"):
                falhou.append(item)
            elif st == "incompleto":
                incompleto.append(item)
            elif st == "scheduled":
                pub = cel.get("youtube_publish_at") or ""
                try:
                    quando = datetime.fromisoformat(pub.replace("Z", "+00:00"))
                except ValueError:
                    continue
                if quando < agora:          # ja deveria ter publicado
                    priv, _ = _publico(repo, canal_yt, vid) if vid else (None, None)
                    if priv == "public":
                        ok += 1
                    elif priv:
                        nao_publicou.append(item + (priv,))

    _log(f"falhou={len(falhou)} incompleto={len(incompleto)} "
         f"nao_publicou={len(nao_publicou)} publicados_ok={ok} "
         f"thumb_pend={len(thumb_pend)} pin_pend={len(pin_pend)} fila_presa={len(fila_presa)}")
    for t in falhou:
        _log(f"  FALHOU     {t[0]} {t[4]} ({t[8]})")
    for t in incompleto:
        _log(f"  INCOMPLETO {t[0]} {t[4]} vid={t[5]}")
    for t in nao_publicou:
        _log(f"  NAO PUBLICOU {t[0]} {t[4]} vid={t[5]} privacy={t[9]}")
    for t in thumb_pend:
        _log(f"  THUMB PEND {t[0]} {t[4]} vid={t[5]}")
    for t in pin_pend:
        _log(f"  PIN PEND   {t[0]} {t[4]} vid={t[5]}")
    for t in fila_presa:
        _log(f"  FILA PRESA {t[0]} {t[4]} status={t[8]} (esteira_worker parado?)")
    if fila_presa:
        ut._telegram(cfg, "⚠️ Temas presos em fila:* ha mais de um dia — "
                          "esteira_worker parado?\n" +
                     "\n".join(f"{t[0]} {t[4]} ({t[8]})" for t in fila_presa[:10]))

    if nao_publicou:
        ut._telegram(cfg, "🚨 Videos que NAO publicaram na data:\n" +
                     "\n".join(f"{t[0]} {t[4]} {t[5]} ({t[9]})" for t in nao_publicou[:10]))

    if not a.apply:
        _log("[DRY RUN] --apply pra corrigir.")
        return

    # 1) incompletos -> reconcilia (nao re-sobe)
    for alias, canal_yt, row, col, data_iso, vid, tz, slot, _ in incompleto:
        _log(f"reconciliando {alias} {data_iso}...")
        ut._reconciliar(cfg, repo, canal_yt, alias, row, col, data_iso, vid, None, slot, tz)

    # 1b) JOURNAL orfao -> grid ficou pra tras da realidade
    # O journal local sabe que o video subiu mesmo quando o POST /api/upload/mark
    # nao passou. Sem esta varredura, a divergencia so apareceria num re-render.
    for jf in sorted(ut.JOURNAL_DIR.glob("*.json")) if ut.JOURNAL_DIR.exists() else []:
        try:
            j = json.loads(jf.read_text(encoding="utf-8"))
        except Exception:
            _log(f"  journal ilegivel: {jf.name}")
            continue
        if (j.get("estado") or "") != "subiu":
            continue                       # 'confirmado' = ok; 'subindo' = olho humano
        al, di, vid = j.get("alias") or "", j.get("data") or "", j.get("video_id") or ""
        if filtro and al not in filtro:
            continue
        col = col_de.get(al)
        slot_info = ut.slot_map(cfg).get(al)
        if col is None or not slot_info or not vid:
            continue
        canal_yt, tz, slot = slot_info
        try:
            y, m, d = di.split("-")
            row = next(i for i, l in enumerate(linhas)
                       if (l.get("data") or "").strip() == f"{d}/{m}/{y}")
        except (StopIteration, ValueError):
            _log(f"  journal {jf.name}: data {di} nao esta no grid")
            continue
        cel = celulas.get(f"{row}_{col}") or {}
        if cel.get("youtube_video_id") == vid and (cel.get("upload_status") or "") == "scheduled":
            ut._journal_escrever(al, di, "confirmado", vid)   # grid alcancou; fecha
            continue
        _log(f"  JOURNAL ORFAO {al} {di} vid={vid} (grid: {cel.get('youtube_video_id')!r} "
             f"{cel.get('upload_status')!r})")
        if a.apply:
            ut._reconciliar(cfg, repo, canal_yt, al, row, col, di, vid, None, slot, tz)

    def _mark(row, col, **campos):
        """Patch da celula via /api/upload/mark. Best-effort."""
        try:
            body = json.dumps({"row": row, "col": col, **campos}).encode()
            req = urllib.request.Request(f"{VPS}/api/upload/mark", data=body,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30).read()
        except Exception as e:
            _log(f"  mark falhou: {type(e).__name__}: {e}")

    # 1c) THUMB pendente -> re-gera + aplica (a publicacao nunca esperou por ela)
    for alias, canal_yt, row, col, data_iso, vid, tz, slot, _ in thumb_pend:
        if not vid:
            continue
        _log(f"re-tentando THUMB {alias} {data_iso}...")
        th = None
        try:
            import thumb_pipeline
            th = thumb_pipeline.gerar(alias, data_iso)
        except Exception as e:
            _log(f"  thumb gen falhou: {type(e).__name__}")
        if th and ut._aplicar_thumb(cfg, repo, canal_yt, vid, th):
            _mark(row, col, thumb_status="ok")
            _log("  thumb pendente RESOLVIDA")
        else:
            _log("  thumb segue pendente (proxima rodada re-tenta)")

    # 1d) PIN pendente -> ensure-pin (unlisted -> pina -> reagenda).
    # row/col vao junto: o pin e' SEMPRE pelo commentID do grid (regra Piter
    # 31/07) — video pode ja estar PUBLICO aqui, e "o mais recente" fixaria
    # comentario de espectador.
    for alias, canal_yt, row, col, data_iso, vid, tz, slot, _ in pin_pend:
        if not vid:
            continue
        _log(f"re-tentando PIN {alias} {data_iso}...")
        res = ut.rodar_pin(cfg, alias, data_iso, vid, row=row, col=col)
        if res.get("pinned"):
            _mark(row, col, pin_status="ok")
            _log("  pin pendente RESOLVIDO")
        else:
            _log("  pin segue pendente (proxima rodada re-tenta)")

    # 2) falhou -> re-dispara o upload completo (o MP4 precisa existir)
    for alias, canal_yt, row, col, data_iso, vid, tz, slot, _ in falhou:
        # glob em vez de _01 fixo: um re-render vira _02 e o sufixo fixo nunca
        # o acharia (item 10 da auditoria 30/07). Pega o MAIS RECENTE.
        pasta = Path(cfg.get("export_base", "F:/Canal Dark/Automator Exports")) / data_iso / "Videos"
        candidatos = sorted(pasta.glob(f"{alias}_{data_iso.replace('-','')}_*.mp4"),
                            key=lambda p: p.stat().st_mtime, reverse=True)
        mp4 = candidatos[0] if candidatos else None
        if not mp4:
            _log(f"  {alias} {data_iso}: MP4 ausente — precisa re-renderizar antes")
            continue
        _log(f"re-tentando upload {alias} {data_iso}...")
        r = ut.disparar(cfg, alias=alias, canal_idx=col, data_pasta=data_iso,
                        video_path=str(mp4))
        _log(f"  -> ok={r.get('ok')} skip={r.get('skip')} erro={r.get('erro')}")


if __name__ == "__main__":
    main()
