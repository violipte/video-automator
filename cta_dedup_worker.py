# -*- coding: utf-8 -*-
"""Worker do checkup de CTA (roda no venv do drive-to-youtube, cwd=repo,
CHANNEL_ALIAS no env). UM video por chamada.

Modos (argv[4]):
  checar        unlisted -> lista CTAs proprios (autor==canal + prefixo do
                build_comment) -> badge de fixado via watch (browser, leitura)
                -> alvo = fixado > cid_grid > mais antigo -> APAGA as outras
                copias -> NAO reagenda (deixa unlisted pro pin, 1 flip so).
  so-reagendar  apenas private+publishAt (fallback do driver).

REGRA DURA (Piter 01/08): video com commentID ja definido NUNCA pode ganhar
outro CTA — este worker e' o executor da limpeza; o guard de postagem mora no
upload_verify (find-before-post com backoff).

stdout: __CHECKUP__{json}
"""
import json
import os
import re
import sys
import time
from datetime import datetime

sys.path.insert(0, ".")
from zoneinfo import ZoneInfo

from config import PUBLISH_TZ
from lib import adspower, supabase, youtube
from lib.comment import build_comment

VID, PUB = sys.argv[1], sys.argv[2]
CID_GRID = sys.argv[3] if len(sys.argv) > 3 else ""
MODO = sys.argv[4] if len(sys.argv) > 4 else "checar"

out = {"vid": VID, "modo": MODO, "alvo": "", "fixado": "", "apagados": [],
       "n_cta": 0, "reagendado": False}


def _reagendar(y):
    for _ in range(3):
        try:
            youtube.set_private_and_schedule(
                VID, datetime.fromisoformat(PUB.replace("Z", "+00:00")))
            time.sleep(3)
            st = y.videos().list(part="status", id=VID).execute()["items"][0]["status"]
            if st.get("privacyStatus") == "private" and st.get("publishAt"):
                return True
        except Exception:
            time.sleep(8)
    return False


y = youtube.youtube()
if MODO == "so-reagendar":
    out["reagendado"] = _reagendar(y)
    print("__CHECKUP__" + json.dumps(out, ensure_ascii=False))
    raise SystemExit(0)

youtube.set_unlisted(VID)
time.sleep(4)

# CTAs proprios: autor == o canal E texto comeca com o build_comment do dia
# (45 chars normalizados — mesma assinatura do upload_verify)
pub_local = datetime.fromisoformat(PUB.replace("Z", "+00:00")).astimezone(ZoneInfo(PUBLISH_TZ))
assin = " ".join(build_comment(pub_local).lower().split())[:45]
meu = y.channels().list(part="id", mine=True).execute()["items"][0]["id"]
meus = []
for tent in range(3):                     # lag de indexacao pos-flip
    r = y.commentThreads().list(part="snippet", videoId=VID, maxResults=50,
                                order="time").execute()
    meus = []
    for it in r.get("items") or []:
        sn = ((it.get("snippet") or {}).get("topLevelComment") or {}).get("snippet") or {}
        autor = ((sn.get("authorChannelId") or {}).get("value")) or ""
        txt = " ".join((sn.get("textOriginal") or "").lower().split())
        if autor == meu and txt.startswith(assin):
            meus.append({"id": it.get("id"), "pub": sn.get("publishedAt", "")})
    if meus:
        break
    time.sleep(20)
out["n_cta"] = len(meus)
ids = [m["id"] for m in meus]

# qual esta FIXADO? badge no watch (leitura apenas, browser do canal)
fixado = ""
if len(ids) > 1:
    try:
        pid = (supabase.get_canal(os.environ["CHANNEL_ALIAS"]) or {}).get("adspower_profile_id")
        cdp = adspower.start_profile(pid)
        from playwright.sync_api import sync_playwright
        with sync_playwright() as p:
            b = p.chromium.connect_over_cdp(cdp)
            page = b.contexts[0].new_page()
            try:
                page.goto(f"https://www.youtube.com/watch?v={VID}", timeout=45000,
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
                        const fix = !!(th.innerText.match(/Fixado por|Pinned by/i));
                        return {cid: m[1] || '', fix: fix};
                    });
                }''')
                for t in info:
                    if t.get("fix") and t.get("cid") in ids:
                        fixado = t["cid"]
                        break
            finally:
                page.close()
    except Exception as e:
        out["badge_erro"] = type(e).__name__
out["fixado"] = fixado

alvo = fixado or (CID_GRID if CID_GRID in ids else "") or (
    sorted(meus, key=lambda m: m["pub"])[0]["id"] if meus else "")
out["alvo"] = alvo
for m in meus:
    if m["id"] != alvo:
        try:
            y.comments().delete(id=m["id"]).execute()
            out["apagados"].append(m["id"])
        except Exception as e:
            out.setdefault("erros", []).append(f"{m['id']}:{type(e).__name__}")

# NAO reagenda: o pin (verify) reagenda no fim; driver tem fallback so-reagendar.
print("__CHECKUP__" + json.dumps(out, ensure_ascii=False))
