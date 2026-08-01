# -*- coding: utf-8 -*-
"""Dedup de CTA em videos PUBLICADOS (sem NENHUM flip de privacidade!).

Roda no venv do drive-to-youtube (cwd=repo, CHANNEL_ALIAS no env), UM canal por
chamada; recebe no stdin JSON {"videos": [{"vid": ...}, ...]}.

Por video:
  1. commentThreads.list direto (publico lista sem flip; private da 403 -> pula)
  2. comentarios DO CANAL agrupados por texto normalizado (120 chars) — so'
     grupo com REPETICAO conta (comentario manual/diferente NUNCA e' tocado)
  3. repeticao -> badge no watch (leitura) diz qual esta FIXADO
  4. mantem fixado > mais antigo; apaga as demais copias via API

Saida: uma linha __DEDUP__{json} por video com repeticao (ou erro).
"""
import json
import os
import sys
import time

sys.path.insert(0, ".")
from lib import adspower, supabase, youtube

dados = json.loads(sys.stdin.read())
videos = dados.get("videos") or []

y = youtube.youtube()
meu = y.channels().list(part="id", mine=True).execute()["items"][0]["id"]


def norm(t):
    return " ".join((t or "").lower().split())[:120]


# ---- fase 1: achar videos com CTA repetido (API pura) ----
flagged = []
for v in videos:
    vid = v["vid"]
    try:
        r = y.commentThreads().list(part="snippet", videoId=vid,
                                    maxResults=100, order="time").execute()
    except Exception as e:
        print(f"__SKIP__{vid} {type(e).__name__}", flush=True)   # private/desabilitado
        continue
    meus = []
    for it in r.get("items") or []:
        sn = ((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
        if ((sn.get("authorChannelId") or {}).get("value")) == meu:
            meus.append({"id": it.get("id"), "pub": sn.get("publishedAt", ""),
                         "txt": norm(sn.get("textOriginal"))})
    grupos = {}
    for m in meus:
        grupos.setdefault(m["txt"], []).append(m)
    reps = {t: g for t, g in grupos.items() if len(g) > 1}
    if reps:
        flagged.append({"vid": vid, "reps": reps})
    time.sleep(0.3)

if not flagged:
    print("__FIM__sem repeticoes", flush=True)
    raise SystemExit(0)

# ---- fase 2: badge de fixado (browser, leitura) + delecao ----
pid = (supabase.get_canal(os.environ["CHANNEL_ALIAS"]) or {}).get("adspower_profile_id")
cdp = adspower.start_profile(pid)
from playwright.sync_api import sync_playwright

with sync_playwright() as p:
    b = p.chromium.connect_over_cdp(cdp)
    for f in flagged:
        vid = f["vid"]
        fixado = ""
        page = b.contexts[0].new_page()
        try:
            page.goto(f"https://www.youtube.com/watch?v={vid}", timeout=45000,
                      wait_until="domcontentloaded")
            time.sleep(6)
            from lib.pin import _dismiss_consent
            _dismiss_consent(page)
            for _ in range(20):
                if page.locator("ytd-comment-thread-renderer").count() > 0:
                    break
                page.mouse.wheel(0, 900)
                time.sleep(1.2)
            time.sleep(3)
            info = page.evaluate(r'''() => {
                return [...document.querySelectorAll('ytd-comment-thread-renderer')].map(th => {
                    const a = th.querySelector('a[href*="lc="]');
                    const m = a ? (a.href.match(/lc=([^&]+)/) || []) : [];
                    return {cid: m[1] || '',
                            fix: !!(th.innerText.match(/Fixado por|Pinned by/i))};
                });
            }''')
            for t in info:
                if t.get("fix") and t.get("cid"):
                    fixado = t["cid"]
                    break
        except Exception as e:
            print(f"__DEDUP__" + json.dumps(
                {"vid": vid, "erro": f"badge:{type(e).__name__}"}), flush=True)
            page.close()
            continue
        finally:
            try:
                page.close()
            except Exception:
                pass

        out = {"vid": vid, "fixado": fixado, "apagados": [], "mantidos": []}
        for txt, grupo in f["reps"].items():
            ids = [g["id"] for g in grupo]
            manter = fixado if fixado in ids else sorted(grupo, key=lambda g: g["pub"])[0]["id"]
            out["mantidos"].append(manter + ("(fixado)" if manter == fixado else "(antigo)"))
            for g in grupo:
                if g["id"] != manter:
                    try:
                        y.comments().delete(id=g["id"]).execute()
                        out["apagados"].append(g["id"])
                    except Exception as e:
                        out.setdefault("erros", []).append(f"{g['id']}:{type(e).__name__}")
        print("__DEDUP__" + json.dumps(out, ensure_ascii=False), flush=True)
        time.sleep(2)

print("__FIM__ok", flush=True)
