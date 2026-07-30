# -*- coding: utf-8 -*-
"""RECONCILIADOR pos-upload: garante o estado final e CONSERTA o que faltou.

Roda SEMPRE depois do upload_one (mesmo quando ele diz "ok"). Le o estado REAL
no YouTube, compara com o desejado e completa os passos ausentes. Idempotente:
o que ja esta certo, nao mexe.

POR QUE EXISTE (piloto ENO2 06/08, 30/07): o upload_one morreu no meio (bug de
encoding no subprocess) DEPOIS da playlist e ANTES do comentario -> o video
ficou unlisted, sem CTA e sem agendamento, e eu tive que consertar na mao.
Com este modulo isso se resolve sozinho.

ESTADO DESEJADO
  1. thumb aplicada (se o pipeline gerou uma)
  2. na playlist do canal
  3. comentario (CTA do ebook) postado E fixado
  4. privacyStatus=private + publishAt = data-tema + slot do alias
  5. grid marcado (video_id, publish_at, status=scheduled)

⚠️ ORDEM OBRIGATORIA: o YouTube NAO aceita comentario em video PRIVATE. Entao,
se faltar comentario num video ja agendado: volta pra UNLISTED -> comenta ->
pina -> REAGENDA. (Aprendido na marra no piloto.)

Roda no venv do drive-to-youtube (tem as libs/creds); e' invocado pelo
upload_trigger via subprocess. Saida: linha __VERIFY__<json>.
"""
import argparse
import json
import sys
from datetime import datetime, timezone
from pathlib import Path

sys.path.insert(0, ".")   # cwd = repo do drive-to-youtube


def _p(m):
    print(f"[verify] {m}", flush=True)


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--video-id", required=True)
    ap.add_argument("--publish-utc", required=True, help="ISO8601 UTC do publishAt desejado")
    ap.add_argument("--thumb", default="")
    ap.add_argument("--no-pin", action="store_true")
    a = ap.parse_args()

    from config import ADSPOWER_PROFILE_ID, ADSPOWER_YT_LANG, PUBLISH_TZ
    from lib import youtube
    from lib.comment import build_comment

    vid = a.video_id
    pub_utc = datetime.fromisoformat(a.publish_utc.replace("Z", "+00:00"))
    if pub_utc.tzinfo is None:
        pub_utc = pub_utc.replace(tzinfo=timezone.utc)
    try:
        from zoneinfo import ZoneInfo
        pub_local = pub_utc.astimezone(ZoneInfo(PUBLISH_TZ))
    except Exception:
        pub_local = pub_utc

    consertos, y = [], youtube.youtube()

    def estado():
        it = y.videos().list(part="status", id=vid).execute()["items"][0]
        return it["status"].get("privacyStatus"), it["status"].get("publishAt")

    def n_comentarios():
        """Conta comentarios REAIS via commentThreads().list().

        ⚠️ NUNCA usar statistics.commentCount: em video private/unlisted recente
        ele volta 0 mesmo havendo comentarios -> no piloto isso me fez postar 3
        DUPLICATAS do CTA. Em private a listagem da 403 -> devolve None
        ('desconhecido'), e quem chama trata como "nao sei, checar em unlisted".
        """
        try:
            r = y.commentThreads().list(part="snippet", videoId=vid, maxResults=10).execute()
            return len(r.get("items", []))
        except Exception:
            return None   # private/desabilitado -> indeterminado

    priv, pub_at = estado()
    ncom = n_comentarios()
    _p(f"estado inicial: privacy={priv} publishAt={pub_at} comments={ncom}")

    # --- 1) thumb ---
    if a.thumb and Path(a.thumb).exists():
        try:
            youtube.set_thumbnail(vid, Path(a.thumb))
            consertos.append("thumb")
        except Exception as e:
            _p(f"thumb falhou: {type(e).__name__}: {str(e)[:90]}")

    # --- 2) playlist (idempotente do lado deles) ---
    try:
        youtube.add_to_playlist(vid)
        consertos.append("playlist")
    except Exception as e:
        msg = str(e)[:90]
        if "duplicate" not in msg.lower():
            _p(f"playlist: {type(e).__name__}: {msg}")

    # --- 3) comentario + pin (exige NAO-private) ---
    # ncom None = indeterminado (private bloqueia a listagem). So da pra saber —
    # e so da pra postar — com o video fora de private.
    if ncom is None or ncom == 0:
        reagendar = False
        try:
            if priv == "private":
                _p("private -> unlisted (unica forma de LISTAR e de postar)")
                youtube.set_unlisted(vid)
                reagendar = True
                import time as _t
                _t.sleep(4)               # propagacao do YouTube
                ncom = n_comentarios()
                _p(f"comentarios REAIS (agora visiveis): {ncom}")
            if ncom == 0:
                texto = build_comment(pub_local)
                cid = youtube.post_comment(vid, texto)
                consertos.append("comentario")
                _p(f"comentario postado: {cid}")
                if not a.no_pin:
                    try:
                        from lib import adspower
                        from lib import pin as pin_mod
                        cdp = adspower.start_profile(ADSPOWER_PROFILE_ID)
                        try:
                            pin_mod.pin_comment(cdp, vid, cid, lang=ADSPOWER_YT_LANG)
                            consertos.append("pin")
                        finally:
                            adspower.stop_profile(ADSPOWER_PROFILE_ID)
                    except Exception as e:
                        _p(f"pin falhou (comentario POSTADO): {type(e).__name__}: {str(e)[:90]}")
            else:
                _p("comentario ja existia")
        except Exception as e:
            _p(f"comentario falhou: {type(e).__name__}: {str(e)[:110]}")
        finally:
            if reagendar:
                pub_at = None  # forca reagendar abaixo

    # --- 4) agendamento (private + publishAt) ---
    priv, pub_at2 = estado()
    alvo = pub_utc.strftime("%Y-%m-%dT%H:%M:%SZ")
    if priv != "private" or not pub_at2 or pub_at2[:16] != alvo[:16]:
        if pub_utc <= datetime.now(timezone.utc):
            _p(f"publishAt {alvo} JA PASSOU — nao agendo (fica {priv})")
        else:
            try:
                youtube.set_private_and_schedule(vid, pub_utc)
                consertos.append("agendamento")
            except Exception as e:
                _p(f"agendamento falhou: {type(e).__name__}: {str(e)[:90]}")

    priv, pub_at = estado()
    ok = (priv == "private" and bool(pub_at))
    _p(f"estado final: privacy={priv} publishAt={pub_at} | consertos={consertos}")
    print("__VERIFY__" + json.dumps(
        {"ok": ok, "privacy": priv, "publish_at": pub_at, "consertos": consertos},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
