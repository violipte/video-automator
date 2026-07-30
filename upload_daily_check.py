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
    ap.add_argument("--canais", default="", help="CSV; vazio = todos do SLOT_MAP")
    a = ap.parse_args()

    cfg = _cfg()
    repo = Path((cfg.get("upload_trigger") or {}).get("repo")
                or "F:/Canal Dark/Apps Rapidos/drive-to-youtube")
    filtro = {c.strip().upper() for c in a.canais.split(",") if c.strip()}

    g = _grid()
    linhas, colunas, celulas = g["linhas"], g["colunas"], g["celulas"]
    col_de = {(c.get("nome") or "").strip().upper(): i for i, c in enumerate(colunas)}
    agora = datetime.now(timezone.utc)

    falhou, incompleto, nao_publicou, ok = [], [], [], 0

    for alias, (canal_yt, tz, slot) in ut.SLOT_MAP.items():
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
         f"nao_publicou={len(nao_publicou)} publicados_ok={ok}")
    for t in falhou:
        _log(f"  FALHOU     {t[0]} {t[4]} ({t[8]})")
    for t in incompleto:
        _log(f"  INCOMPLETO {t[0]} {t[4]} vid={t[5]}")
    for t in nao_publicou:
        _log(f"  NAO PUBLICOU {t[0]} {t[4]} vid={t[5]} privacy={t[9]}")

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

    # 2) falhou -> re-dispara o upload completo (o MP4 precisa existir)
    for alias, canal_yt, row, col, data_iso, vid, tz, slot, _ in falhou:
        mp4 = Path(cfg.get("export_base", "F:/Canal Dark/Automator Exports")) / data_iso / \
            "Videos" / f"{alias}_{data_iso.replace('-','')}_01.mp4"
        if not mp4.exists():
            _log(f"  {alias} {data_iso}: MP4 ausente — precisa re-renderizar antes")
            continue
        _log(f"re-tentando upload {alias} {data_iso}...")
        r = ut.disparar(cfg, alias=alias, canal_idx=col, data_pasta=data_iso,
                        video_path=str(mp4))
        _log(f"  -> ok={r.get('ok')} skip={r.get('skip')} erro={r.get('erro')}")


if __name__ == "__main__":
    main()
