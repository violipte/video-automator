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
    ap.add_argument("--ensure-pin", action="store_true",
                    help="estagio PIN da esteira: fixa o CTA mesmo quando o comentario "
                         "JA existia (o fluxo normal so pina comentario recem-postado). "
                         "Exige unlisted -> acha o comentario -> pina -> reagenda.")
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
    # --ensure-pin (estagio PIN da esteira): entra aqui MESMO com comentario
    # existente, acha o id dele e fixa. REGRA (Piter 30/07): pin falhou ->
    # 'pin_falhou' no resultado e o video SEGUE agendado; retry e' de quem chamou.
    pinned = False
    if ncom is None or ncom == 0 or a.ensure_pin:
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
            cid = None
            if not ncom:                  # 0 ou None (comentarios desabilitados dao None)
                texto = build_comment(pub_local)
                cid = youtube.post_comment(vid, texto)
                consertos.append("comentario")
                _p(f"comentario postado: {cid}")
            else:
                _p("comentario ja existia")
                if a.ensure_pin:
                    # id do thread == id do comentario top-level (e' o que o pin usa)
                    try:
                        r = y.commentThreads().list(part="id", videoId=vid,
                                                    maxResults=1, order="time").execute()
                        itens = r.get("items") or []
                        cid = itens[0]["id"] if itens else None
                        _p(f"comentario existente: {cid}")
                    except Exception as e:
                        _p(f"nao achei o id do comentario: {type(e).__name__}: {str(e)[:90]}")
            if cid and not a.no_pin:
                try:
                    # pin_slot(1): serializa com qualquer outro pin da maquina
                    # (esteira ja e' fila unica; isto protege do daily check junto)
                    from lib.pinlock import pin_slot
                    from lib import adspower
                    from lib import pin as pin_mod
                    with pin_slot(n_slots=1):
                        cdp = adspower.start_profile(ADSPOWER_PROFILE_ID)
                        try:
                            pin_mod.pin_comment(cdp, vid, cid, lang=ADSPOWER_YT_LANG)
                            consertos.append("pin")
                            pinned = True
                        finally:
                            adspower.stop_profile(ADSPOWER_PROFILE_ID)
                except Exception as e:
                    _p(f"pin falhou (comentario OK, video segue): {type(e).__name__}: {str(e)[:90]}")
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

    # --- 5) CONFIRMACAO com backoff ---
    # `videos.list` do YouTube tem consistencia EVENTUAL: logo depois de um
    # update ele ainda devolve o estado ANTIGO. Ler uma vez so (o que este
    # trecho fazia) gerou FALSO NEGATIVO no ENO2 07/08: o video estava
    # private+agendado certinho, o verify leu 'unlisted/None' e marcou
    # `incompleto` -> a checagem diaria ia "reconciliar" um video 100% pronto,
    # mexendo na privacidade dele a toa. So desiste depois de reconferir.
    ja_passou = pub_utc <= datetime.now(timezone.utc)

    def bateu(p, pa):
        if ja_passou:
            return p == "public"          # devia ter publicado sozinho
        return p == "private" and bool(pa) and pa[:16] == alvo[:16]

    priv, pub_at = estado()
    if not bateu(priv, pub_at):
        import time as _t
        for espera in (3, 5, 8, 12, 20):
            _p(f"ainda nao propagou (privacy={priv} publishAt={pub_at}) — reconfere em {espera}s")
            _t.sleep(espera)
            priv, pub_at = estado()
            if bateu(priv, pub_at):
                break

    ok = bateu(priv, pub_at)
    _p(f"estado final: privacy={priv} publishAt={pub_at} ok={ok} pinned={pinned} "
       f"| consertos={consertos}")
    print("__VERIFY__" + json.dumps(
        {"ok": ok, "privacy": priv, "publish_at": pub_at, "consertos": consertos,
         "pinned": pinned},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
