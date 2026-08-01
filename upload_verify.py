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


from contextlib import contextmanager


@contextmanager
def _lock_profile(profile_id: str, espera_max: int = 1200):
    """Lock POR PROFILE do AdsPower, entre processos. Dois pins no MESMO profile
    ao mesmo tempo se atropelam (o stop de um mata a sessao do outro no meio) —
    CO3/CO4 dividem profile, e o daily check pode rodar junto da esteira.
    Profiles DIFERENTES rodam em paralelo livremente (cap = pin_slot)."""
    import os
    import tempfile
    import time as _t
    p = Path(tempfile.gettempdir()) / f"pin_profile_{profile_id}.lock"
    t0 = _t.time()
    fd = None
    while fd is None:
        try:
            fd = os.open(str(p), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            os.write(fd, str(os.getpid()).encode())
        except FileExistsError:
            try:
                idade = _t.time() - p.stat().st_mtime
            except OSError:
                idade = 0
            if idade > 900:                     # pin leva ~2min; >15min = orfao
                p.unlink(missing_ok=True)
                continue
            if _t.time() - t0 > espera_max:
                raise TimeoutError(f"profile {profile_id} ocupado ha {espera_max}s")
            _t.sleep(5)
    try:
        yield
    finally:
        try:
            os.close(fd)
            p.unlink(missing_ok=True)
        except OSError:
            pass


def _achar_cta_proprio(y, vid: str, texto_esperado: str):
    """Fallback pra celula ANTIGA sem youtube_comment_id: acha o commentID do
    NOSSO CTA. Dupla checagem — autor == o proprio canal E o texto bate com o
    inicio do build_comment (45 chars normalizados). Devolve o thread id (que
    e' o id do comentario top-level, o mesmo que o pin.py usa no &lc=).
    None se nao achar com certeza — melhor nao pinar do que pinar o errado."""
    def norm(s):
        return " ".join((s or "").lower().split())
    assinatura = norm(texto_esperado)[:45]
    if not assinatura:
        return None
    try:
        meu = y.channels().list(part="id", mine=True).execute()["items"][0]["id"]
        r = y.commentThreads().list(part="snippet", videoId=vid,
                                    maxResults=50, order="time").execute()
        for it in r.get("items") or []:
            top = (it.get("snippet") or {}).get("topLevelComment") or {}
            sn = top.get("snippet") or {}
            autor = ((sn.get("authorChannelId") or {}).get("value")) or ""
            if autor == meu and norm(sn.get("textOriginal")).startswith(assinatura):
                return it.get("id")
    except Exception as e:
        _p(f"_achar_cta_proprio falhou: {type(e).__name__}: {str(e)[:90]}")
    return None


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
    ap.add_argument("--comment-id", default="",
                    help="commentID do CTA vindo do grid (youtube_comment_id). REGRA DO "
                         "PITER (31/07): o pin e' SEMPRE pelo commentID — o pin.py navega "
                         "watch?v=X&lc=<id> e fixa o comentario em highlight. NUNCA "
                         "'o mais recente' (fixaria comentario de espectador em video "
                         "ja publico). Sem este arg, resolve 1x por autor+texto e persiste.")
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
    cid = None
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
            if not ncom and not a.comment_id:
                # REGRA DURA (Piter 01/08): NUNCA postar sem antes PROCURAR o
                # CTA por autor+texto com backoff. Contagem NAO decide nada —
                # ela atrasa MINUTOS em video recem-subido, e cada estagio que
                # confiou nela postou de novo (CO3/CO4 09/08 = 3 CTAs iguais:
                # upload_one -> reconcile -> pin, um por estagio).
                import time as _t2
                cid = None
                for _tent in range(3):
                    cid = _achar_cta_proprio(y, vid, build_comment(pub_local))
                    if cid:
                        break
                    _t2.sleep(20)         # lag de indexacao: espera e re-procura
                if cid:
                    _p(f"CTA ja existia (achado por autor+texto, sera persistido): {cid}")
                else:
                    texto = build_comment(pub_local)
                    cid = youtube.post_comment(vid, texto)
                    consertos.append("comentario")
                    _p(f"comentario postado: {cid}")
            elif not ncom and a.comment_id:
                cid = a.comment_id
                _p(f"contagem=0 mas comment_id conhecido ({cid}) — NAO re-posto (lag de indexacao)")
            else:
                _p("comentario ja existia")
                if a.ensure_pin:
                    # 1º: o commentID persistido no grid (caminho normal, sempre certo)
                    if a.comment_id:
                        cid = a.comment_id
                        _p(f"pin pelo commentID do grid: {cid}")
                    else:
                        # fallback UNICO (celula antiga sem youtube_comment_id):
                        # acha o NOSSO CTA — autor tem que ser o proprio canal E o
                        # texto bater com o build_comment. Nunca "o mais recente":
                        # em video ja publico isso fixaria comentario de espectador.
                        cid = _achar_cta_proprio(y, vid, build_comment(pub_local))
                        if cid:
                            _p(f"CTA achado por autor+texto (sera persistido): {cid}")
                        else:
                            _p("CTA proprio NAO encontrado — nao pino as cegas")
            if cid and not a.no_pin:
                try:
                    # PARALELIZACAO (Piter 31/07): o AdsPower aguenta varios
                    # profiles ABERTOS (o operador usa assim manualmente) — quem
                    # precisa de blindagem e' a automacao:
                    #   pin_slot()        -> cap GLOBAL (PIN_CONCURRENCY, 3-5)
                    #   _lock_profile     -> NUNCA 2 automacoes no MESMO profile
                    #                        (CO3/CO4 dividem; daily + esteira)
                    #   ja_estava_aberto  -> se o PITER esta usando o profile,
                    #                        nao fecha na mao dele no final
                    from lib.pinlock import pin_slot
                    from lib import adspower
                    from lib import pin as pin_mod
                    with pin_slot():
                        with _lock_profile(ADSPOWER_PROFILE_ID):
                            ja_estava_aberto = adspower.is_active(ADSPOWER_PROFILE_ID)
                            cdp = adspower.start_profile(ADSPOWER_PROFILE_ID)
                            try:
                                pin_mod.pin_comment(cdp, vid, cid, lang=ADSPOWER_YT_LANG)
                                consertos.append("pin")
                                pinned = True
                            finally:
                                if ja_estava_aberto:
                                    _p("profile ja estava aberto (uso manual?) — NAO fecho")
                                else:
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
    # comment_id sai no resultado pro trigger PERSISTIR no grid (youtube_comment_id):
    # e' ele que garante que qualquer pin futuro use o &lc=<id> certo, pra sempre.
    print("__VERIFY__" + json.dumps(
        {"ok": ok, "privacy": priv, "publish_at": pub_at, "consertos": consertos,
         "pinned": pinned, "comment_id": cid or a.comment_id or None},
        ensure_ascii=False))


if __name__ == "__main__":
    main()
