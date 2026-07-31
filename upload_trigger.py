"""Gatilho de upload EVENT-DRIVEN: render termina -> dispara upload DAQUELE tema.

Plug do handoff drive-to-youtube (Notion 3a5770cbb8de8188825ef2819dc6dc3b).
Roda LOCAL, chamado pelo render_worker.py logo apos o MP4 ser validado.

POR QUE: o upload em batch usa "proximo slot livre" e nao sabe qual tema e' de
qual dia -> tema escorrega de dia, thumb trocada, duplicata/gap (caso CO3/CO4).
Aqui a identidade (canal/alias/row=data-tema/col) sai TRAVADA do render, e o
upload_one.py agenda em data_tema + slot do alias (nunca "proximo livre").

SEGURANCA (o gatilho NUNCA pode quebrar o render nem postar errado):
  - Feature flag OFF por default; opt-in por canal (piloto: ENO2).
  - Qualquer excecao e' engolida e logada — render ja terminou, upload e' extra.
  - Checagens: MP4 existe/tamanho, titulo do grid vs esperado, ja-uploaded (no-op).
    Falhou qualquer uma -> ABORTA + alerta Telegram. Melhor nao postar do que errado.
  - Lock por CANAL (arquivo, multi-processo): 4 render workers, mas AdsPower abre
    1 profile por vez e o proxy e' pesado -> 1 upload por canal de cada vez.

Config em worker_config.json:
  "upload_trigger": {
      "enabled": false,          # master switch
      "canais": ["ENO2"],        # so estes aliases disparam (piloto)
      "dry_run": true,           # true = so loga o comando, NAO sobe
      "repo": "F:/Canal Dark/Apps Rapidos/drive-to-youtube",
      "timeout_seg": 3600
  }
"""
import json
import os
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path

# alias -> (canal_alias do drive-to-youtube, timezone, slot HH:MM)
# Fonte: handoff §4. CO3/CO4 = MESMO canal, 2 aliases, 2 slots (mapa TRAVADO
# aqui pra matar o embaralhamento: CO3=01:00 AM, CO4=13:00 PM).
SLOT_MAP = {
    "CON":  ("CON",  "America/Phoenix",     "12:40"),
    "CO3":  ("CO3",  "America/Phoenix",     "01:00"),
    "CO4":  ("CO3",  "America/Phoenix",     "13:00"),
    "NARC": ("NARC", "America/New_York",    "12:45"),
    "NPD":  ("NPD",  "America/Phoenix",     "13:30"),
    "ASH":  ("ASH",  "America/New_York",    "15:00"),
    "EOA":  ("EOA",  "America/Los_Angeles", "12:15"),
    "EN2":  ("EN2",  "UTC",                 "16:45"),
    "ENO2": ("ENO2", "UTC",                 "15:00"),
    "ENO":  ("ENO",  "America/Sao_Paulo",   "14:15"),
}

LOCK_DIR = Path(os.environ.get("TEMP", ".")) / "automator_upload_locks"
LOCK_STALE_SEG = 7200  # 2h: lock mais velho que isso e' orfao (worker morreu)

# JOURNAL write-ahead: registro LOCAL de "estou subindo / subi", ao lado do
# processo que sobe. Existe porque a protecao anti-duplicata nao pode depender
# de uma escrita pela REDE: se o POST /api/upload/mark falha DEPOIS do upload ter
# dado certo, o grid fica sem video_id, a proxima passada nao ve nada e RE-SOBE.
# Estados: subindo -> subiu -> confirmado.
JOURNAL_DIR = Path(os.environ.get("TEMP", ".")) / "automator_upload_journal"


def _journal_path(alias: str, data_iso: str) -> Path:
    return JOURNAL_DIR / f"{alias}_{data_iso}.json"


def _journal_ler(alias: str, data_iso: str) -> dict:
    try:
        p = _journal_path(alias, data_iso)
        if p.exists():
            return json.loads(p.read_text(encoding="utf-8"))
    except Exception as e:
        _log(f"journal ilegivel ({type(e).__name__}) — tratando como vazio")
    return {}


def _journal_escrever(alias: str, data_iso: str, estado: str, video_id: str = ""):
    """Grava o estado ANTES/DEPOIS do upload. Falhar aqui nao derruba o fluxo,
    mas perde a rede anti-duplicata -> loga alto."""
    try:
        JOURNAL_DIR.mkdir(parents=True, exist_ok=True)
        p = _journal_path(alias, data_iso)
        atual = _journal_ler(alias, data_iso)
        atual.update({"alias": alias, "data": data_iso, "estado": estado,
                      "ts": datetime.now().isoformat(timespec="seconds")})
        if video_id:
            atual["video_id"] = video_id
        tmp = p.with_suffix(".tmp")
        tmp.write_text(json.dumps(atual, ensure_ascii=False), encoding="utf-8")
        os.replace(str(tmp), str(p))
    except Exception as e:
        _log(f"ATENCAO: journal NAO gravado ({estado}): {type(e).__name__}: {e}")


def _log(msg: str):
    print(f"[upload_trigger] {msg}", flush=True)


def _cfg(config: dict) -> dict:
    return (config or {}).get("upload_trigger") or {}


def _telegram(config: dict, texto: str):
    """Alerta de abort. Best-effort: falhar aqui nao pode derrubar nada."""
    try:
        tok = (config.get("telegram_token") or "").strip()
        chat = (config.get("telegram_chat_id") or "").strip()
        if not tok or not chat:
            return
        import urllib.request
        import urllib.parse
        data = urllib.parse.urlencode({"chat_id": chat, "text": texto[:3900]}).encode()
        urllib.request.urlopen(
            f"https://api.telegram.org/bot{tok}/sendMessage", data=data, timeout=15
        ).read()
    except Exception as e:
        _log(f"WARN telegram falhou: {e}")


def _abortar(config: dict, alias: str, data_tema: str, motivo: str):
    """Nao sobe + avisa. 'Melhor nao postar do que postar errado' (handoff §5)."""
    _log(f"ABORTADO {alias} {data_tema}: {motivo}")
    _telegram(config, f"🚫 Upload ABORTADO\n{alias} {data_tema}\nMotivo: {motivo}")


class _LockCanal:
    """Lock por canal entre PROCESSOS (os 4 render workers sao processos separados).
    O_CREAT|O_EXCL e' atomico no Windows/NTFS. Lock velho (>2h) = orfao, e' roubado.

    ESPERA a vez (default 45min) em vez de desistir na hora. Desistir era o furo
    #1 da auditoria de 30/07: CO3 e CO4 sao o MESMO canal YouTube — terminando
    juntos, um levava skip silencioso, a celula ficava sem upload_status e
    nenhuma rede de seguranca via o video. Upload de ~1.9GB leva 10-20min, entao
    quem chega segundo espera o primeiro e sobe em seguida."""

    def __init__(self, canal: str, espera_max: int = 2700):
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOCK_DIR / f"{canal}.lock"
        self.espera_max = espera_max
        self.fd = None

    def __enter__(self):
        t0 = time.time()
        avisou = False
        while True:
            try:
                self.fd = os.open(str(self.path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"{os.getpid()} {time.time()}".encode())
                return self
            except FileExistsError:
                try:
                    idade = time.time() - self.path.stat().st_mtime
                except OSError:
                    idade = 0
                if idade > LOCK_STALE_SEG:
                    _log(f"lock orfao ({idade/60:.0f}min) — roubando: {self.path.name}")
                    self.path.unlink(missing_ok=True)
                    continue
                if time.time() - t0 >= self.espera_max:
                    return None      # quem chamou registra dead-letter (nunca silencio)
                if not avisou:
                    _log(f"canal {self.path.stem} com upload em andamento — esperando a vez")
                    avisou = True
                time.sleep(20)

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                os.close(self.fd)
                self.path.unlink(missing_ok=True)
        except OSError:
            pass


def _buscar_celula(vps_url: str, row: int, col: int, timeout: int = 40) -> dict:
    """Le a celula do grid (fonte de verdade) pra validar titulo + ja-uploaded."""
    import urllib.request
    with urllib.request.urlopen(f"{vps_url.rstrip('/')}/api/temas", timeout=timeout) as r:
        grid = json.loads(r.read().decode("utf-8"))
    return (grid.get("celulas") or {}).get(f"{row}_{col}") or {}


def _row_col(vps_url: str, data_iso: str, alias: str, timeout: int = 40) -> tuple:
    """(row, col) do grid a partir de (data_tema, alias). (-1,-1) se nao achar.

    ⚠️ A COL VEM DO **NOME DA COLUNA** no grid, NUNCA do `canal_idx` do job:
    `canal_idx` e' o indice do canal na ORDEM DA PRODUCAO (produzir so o ENO2
    da canal_idx=0!), nao a coluna do grid. Confundir os dois fez o upload de
    29/07 ler titulo/thumb da BASE (col 0) e publicar no ENO2 com nicho errado.
    """
    import urllib.request
    with urllib.request.urlopen(f"{vps_url.rstrip('/')}/api/temas", timeout=timeout) as r:
        grid = json.loads(r.read().decode("utf-8"))
    alvo = datetime.strptime(data_iso, "%Y-%m-%d").strftime("%d/%m/%Y")
    row = -1
    for i, l in enumerate(grid.get("linhas") or []):
        if (l.get("data") or "").strip() == alvo:
            row = i
            break
    col = -1
    a = (alias or "").strip().upper()
    for j, c in enumerate(grid.get("colunas") or []):
        if (c.get("nome") or "").strip().upper() == a:
            col = j
            break
    return row, col


def _video_id_de(stdout: str) -> str:
    """Extrai video_id do JSON que o upload_one imprime na ULTIMA linha."""
    for ln in reversed((stdout or "").strip().splitlines()):
        ln = ln.strip()
        if ln.startswith("{") and "video_id" in ln:
            try:
                return json.loads(ln).get("video_id") or ""
            except Exception:
                continue
    return ""


def _aplicar_thumb(config: dict, repo: Path, canal_yt: str, video_id: str, thumb: Path) -> bool:
    """set_thumbnail no video ja subido. Best-effort: falha aqui NAO derruba nada
    (o video ja esta no ar/agendado — thumb e' cosmetica e resolve-se depois).
    Retorna True se o YouTube aceitou (o daily usa pra limpar a pendencia)."""
    if not video_id:
        _log("thumb NAO aplicada: video_id nao veio no stdout do upload_one")
        return False
    try:
        py = repo / "venv" / "Scripts" / "python.exe"
        code = ("import sys;sys.path.insert(0,'.');from pathlib import Path;"
                "from lib import youtube;youtube.set_thumbnail(sys.argv[1], Path(sys.argv[2]));"
                "print('THUMB_OK')")
        r = subprocess.run([str(py), "-c", code, video_id, str(thumb)],
                           cwd=str(repo), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                           env=dict(os.environ, CHANNEL_ALIAS=canal_yt, PYTHONUTF8="1"))
        if "THUMB_OK" in (r.stdout or ""):
            _log(f"thumb aplicada em {video_id}: {thumb.name}")
            return True
        _log(f"thumb NAO aplicada (rc={r.returncode}): {(r.stderr or r.stdout or '')[-200:]}")
    except Exception as e:
        _log(f"thumb NAO aplicada ({type(e).__name__}: {e})")
    return False


def _marcar_falha(config: dict, row: int, col: int, motivo: str):
    """DEAD-LETTER: registra a falha no grid pro upload_daily_check re-tentar.
    Sem isso, uma falha transitoria (proxy, 5xx) perde o video em silencio."""
    try:
        import urllib.request
        vps = (config.get("vps_url") or "").strip()
        body = json.dumps({"row": row, "col": col,
                           "upload_status": f"falhou:{motivo[:40]}"}).encode()
        req = urllib.request.Request(f"{vps}/api/upload/mark", data=body,
                                     headers={"Content-Type": "application/json"})
        urllib.request.urlopen(req, timeout=30).read()
        _log(f"dead-letter registrado: falhou:{motivo[:40]}")
    except Exception as e:
        _log(f"dead-letter falhou: {type(e).__name__}: {e}")


def _publish_utc(data_iso: str, tz_nome: str, slot: str) -> str:
    """publishAt DETERMINISTICO = data-tema + slot do alias, no TZ do canal -> ISO UTC."""
    from datetime import timezone as _tz
    try:
        from zoneinfo import ZoneInfo
        tzi = ZoneInfo(tz_nome)
    except Exception:
        tzi = _tz.utc
    hh, mm = (int(x) for x in slot.split(":"))
    local = datetime.strptime(data_iso, "%Y-%m-%d").replace(hour=hh, minute=mm, tzinfo=tzi)
    return local.astimezone(_tz.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _video_id_do_grid(vps: str, row: int, col: int) -> str:
    """Fallback: se o stdout se perdeu, o upload_one ja marcou o grid — leia de la."""
    try:
        return (_buscar_celula(vps, row, col) or {}).get("youtube_video_id") or ""
    except Exception:
        return ""


def _reconciliar(config, repo: Path, canal_yt: str, alias: str, row: int, col: int,
                 data_iso: str, video_id: str, thumb: Path, slot: str, tz: str):
    """Roda o upload_verify: completa o que faltou e marca o grid. Nunca levanta."""
    try:
        vps = (config.get("vps_url") or "").strip()
        if not video_id:                     # stdout perdido -> pega do grid
            video_id = _video_id_do_grid(vps, row, col)
            if video_id:
                _log(f"video_id recuperado do grid: {video_id}")
        if not video_id:
            _abortar(config, alias, data_iso, "sem video_id (stdout e grid vazios) — verificar manual")
            return
        pub = _publish_utc(data_iso, tz, slot)
        py = repo / "venv" / "Scripts" / "python.exe"
        cmd = [str(py), "-u", str(Path(__file__).parent / "upload_verify.py"),
               "--video-id", video_id, "--publish-utc", pub]
        if thumb:
            cmd += ["--thumb", str(thumb)]
        r = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=900,
                           env=dict(os.environ, CHANNEL_ALIAS=canal_yt, PYTHONUTF8="1"))
        res = {}
        for ln in (r.stdout or "").splitlines():
            if ln.startswith("[verify]"):
                _log(ln)
            elif ln.startswith("__VERIFY__"):
                res = json.loads(ln[len("__VERIFY__"):])
        if res.get("consertos"):
            _log(f"AUTO-CORRIGIDO: {res['consertos']}")
        if not res.get("ok"):
            _telegram(config, f"⚠️ {alias} {data_iso} ({video_id}): estado final INCOMPLETO "
                              f"({res.get('privacy')}, publishAt={res.get('publish_at')})")
        # marca o grid (patch atomico) com o estado final.
        # ⚠️ "scheduled" SO quando 100% confirmado (private + publishAt): esse status
        # e' o que faz o gatilho fazer no-op na proxima passada. Se marcar cedo demais,
        # um video incompleto nunca mais e' consertado; se nao marcar, arrisca DUPLICATA.
        # "incompleto" = video no ar mas faltando algo -> proxima passada RECONCILIA
        # (nao re-sobe, porque o video_id ja esta gravado).
        # RETRY: uma unica tentativa era o furo do risco #7 — um blip de rede aqui
        # deixava o grid sem video_id com o video JA no ar, e a passada seguinte
        # duplicaria. O journal local ja cobre isso, mas insistir aqui evita que a
        # divergencia chegue a existir.
        import urllib.request
        status_final = "scheduled" if res.get("ok") else "incompleto"
        campos = {"row": row, "col": col, "youtube_video_id": video_id,
                  "youtube_publish_at": res.get("publish_at") or pub,
                  "youtube_url": f"https://youtube.com/watch?v={video_id}",
                  "upload_status": status_final}
        # commentID do CTA persiste no grid: REGRA (Piter 31/07) — pin e' SEMPRE
        # pelo commentID (&lc=<id> poe o comentario em highlight), nunca "o mais
        # recente". Gravado aqui, qualquer retry futuro pina o certo pra sempre.
        if res.get("comment_id"):
            campos["youtube_comment_id"] = res["comment_id"]
        body = json.dumps(campos).encode()
        marcou = False
        for tentativa, espera in enumerate((0, 3, 8, 20, 45), 1):
            if espera:
                time.sleep(espera)
            try:
                req = urllib.request.Request(f"{vps}/api/upload/mark", data=body,
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=30).read()
                marcou = True
                _log(f"grid marcado ({status_final})" + (f" na tentativa {tentativa}" if tentativa > 1 else ""))
                break
            except Exception as e:
                _log(f"marca do grid falhou (tentativa {tentativa}/5): {type(e).__name__}: {e}")
        if marcou:
            # So agora o journal pode dizer "confirmado": grid e YouTube concordam.
            if status_final == "scheduled":
                _journal_escrever(alias, data_iso, "confirmado", video_id)
        else:
            # O video ESTA no ar e o grid nao sabe. O journal (estado 'subiu') e' o
            # que impede a proxima passada de duplicar; a checagem diaria ressincroniza.
            _log("ATENCAO: grid NAO marcado apos 5 tentativas — journal segura a duplicata")
            _telegram(config, f"⚠️ {alias} {data_iso}: video {video_id} NO AR mas o grid nao "
                              f"aceitou a marca (5 tentativas). O journal impede duplicata; "
                              f"a checagem diaria ressincroniza.")
    except Exception as e:
        _log(f"reconciliacao falhou ({type(e).__name__}: {e}) — video ja esta no ar")


def rodar_pin(config: dict, alias: str, data_iso: str, video_id: str,
              row: int = -1, col: int = -1) -> dict:
    """Estagio PIN da esteira: fixa o CTA de um video JA agendado.

    REGRA (Piter 31/07): pin e' SEMPRE pelo commentID — lido do grid
    (youtube_comment_id) e passado ao verify, que navega &lc=<id> (comentario
    em highlight). Sem id no grid (celula antiga), o verify resolve 1x por
    autor+texto e o id volta persistido.

    upload_verify --ensure-pin faz: unlisted -> pina -> reagenda -> confirma.
    Retorna o dict do __VERIFY__ ({"pinned": bool, ...}); {} se nem rodou.
    NUNCA levanta — pin nao pode derrubar nada (video ja esta agendado)."""
    try:
        cfg = _cfg(config)
        repo = Path(cfg.get("repo") or "F:/Canal Dark/Apps Rapidos/drive-to-youtube")
        canal_yt, tz, slot = SLOT_MAP[alias]
        pub = _publish_utc(data_iso, tz, slot)
        vps = (config.get("vps_url") or "").strip()
        comment_id = ""
        if row >= 0 and col >= 0 and vps:
            try:
                comment_id = (_buscar_celula(vps, row, col) or {}).get("youtube_comment_id") or ""
            except Exception:
                pass
        py = repo / "venv" / "Scripts" / "python.exe"
        cmd = [str(py), "-u", str(Path(__file__).parent / "upload_verify.py"),
               "--video-id", video_id, "--publish-utc", pub, "--ensure-pin"]
        if comment_id:
            cmd += ["--comment-id", comment_id]
        # PIN_CONCURRENCY: cap GLOBAL de pins simultaneos (pinlock, entre
        # processos). REGRA (Piter 31/07): pin paralelo, MAXIMO 5 por vez —
        # validado com teste real (5 profiles + automacao CDP, 0 erros).
        pin_cc = str(int(cfg.get("pin_concurrency", 5)))
        r = subprocess.run(cmd, cwd=str(repo), capture_output=True, text=True,
                           encoding="utf-8", errors="replace", timeout=1200,
                           env=dict(os.environ, CHANNEL_ALIAS=canal_yt,
                                    PYTHONUTF8="1", PIN_CONCURRENCY=pin_cc))
        res = {}
        for ln in (r.stdout or "").splitlines():
            if ln.startswith("[verify]"):
                _log(ln)
            elif ln.startswith("__VERIFY__"):
                res = json.loads(ln[len("__VERIFY__"):])
        # celula antiga: o verify resolveu o commentID por autor+texto -> persiste
        # agora, pra TODO pin futuro ja vir com o id certo do grid.
        cid_novo = res.get("comment_id")
        if cid_novo and cid_novo != comment_id and row >= 0 and col >= 0 and vps:
            try:
                import urllib.request
                body = json.dumps({"row": row, "col": col,
                                   "youtube_comment_id": cid_novo}).encode()
                req = urllib.request.Request(f"{vps}/api/upload/mark", data=body,
                                             headers={"Content-Type": "application/json"})
                urllib.request.urlopen(req, timeout=30).read()
                _log(f"commentID persistido no grid: {cid_novo}")
            except Exception as e:
                _log(f"persistencia do commentID falhou ({type(e).__name__}) — segue")
        return res
    except Exception as e:
        _log(f"rodar_pin falhou ({type(e).__name__}: {e}) — video segue agendado")
        return {}


def disparar(config: dict, alias: str, canal_idx: int, data_pasta: str,
             video_path: str, titulo_esperado: str = "") -> dict:
    """Dispara o upload de UM video recem-renderizado (fluxo INLINE legado:
    gera thumb aqui e o upload_one pina).

    Chamado pelo render_worker apos validar o MP4. NUNCA levanta excecao.
    Retorna {"ok":bool, "skip":str|None, "erro":str|None, "stdout":str}.
    """
    try:
        return _disparar_interno(config, alias, canal_idx, data_pasta,
                                 video_path, titulo_esperado)
    except Exception as e:
        # Render ja terminou com sucesso; falha aqui e' extra, nao pode propagar.
        _log(f"EXCECAO (engolida, render OK): {type(e).__name__}: {e}")
        try:
            _telegram(config, f"⚠️ upload_trigger excecao {alias} {data_pasta}: {e}")
        except Exception:
            pass
        return {"ok": False, "erro": str(e), "skip": None, "stdout": ""}


def disparar_esteira(config: dict, alias: str, data_pasta: str, video_path: str,
                     thumb_path=None, titulo_esperado: str = "") -> dict:
    """Versao pro esteira_worker (estagio UPLOAD da esteira puxada):
      - thumb ja veio do estagio anterior (ou veio None = sobe sem thumb);
      - --no-pin: o PIN e' o estagio seguinte, em fila global (AdsPower).
    Mesmas protecoes do fluxo inline (journal, grid, locks). NUNCA levanta.
    Retorna tambem "video_id" quando disponivel."""
    try:
        return _disparar_interno(config, alias, -1, data_pasta, video_path,
                                 titulo_esperado, thumb_pre=(thumb_path or ""),
                                 no_pin=True)
    except Exception as e:
        _log(f"EXCECAO esteira (engolida): {type(e).__name__}: {e}")
        try:
            _telegram(config, f"⚠️ esteira upload excecao {alias} {data_pasta}: {e}")
        except Exception:
            pass
        return {"ok": False, "erro": str(e), "skip": None, "stdout": ""}


def _disparar_interno(config, alias, canal_idx, data_pasta, video_path, titulo_esperado,
                      thumb_pre=None, no_pin=False):
    cfg = _cfg(config)
    if not cfg.get("enabled"):
        return {"ok": False, "skip": "trigger desabilitado", "erro": None, "stdout": ""}

    alias = (alias or "").strip().upper()
    canais_on = [c.strip().upper() for c in (cfg.get("canais") or [])]
    if canais_on and alias not in canais_on:
        return {"ok": False, "skip": f"{alias} fora do piloto", "erro": None, "stdout": ""}

    if alias not in SLOT_MAP:
        _abortar(config, alias, data_pasta, f"alias sem slot definido (SLOT_MAP)")
        return {"ok": False, "erro": "alias sem slot", "skip": None, "stdout": ""}
    canal_yt, tz, slot = SLOT_MAP[alias]

    # --- checagem 1: MP4 existe e tem tamanho ---
    vp = Path(video_path)
    if not vp.exists() or vp.stat().st_size < 1_000_000:
        _abortar(config, alias, data_pasta, f"MP4 ausente/pequeno: {video_path}")
        return {"ok": False, "erro": "mp4 invalido", "skip": None, "stdout": ""}

    vps = (config.get("vps_url") or "").strip()

    # --- identidade: row = data-tema, col = COLUNA DO ALIAS no grid ---
    # canal_idx NAO serve como col (e' indice da ordem de producao) — ver _row_col.
    row, col = _row_col(vps, data_pasta, alias)
    if row < 0:
        _abortar(config, alias, data_pasta, "data nao encontrada no grid (row)")
        return {"ok": False, "erro": "row nao achada", "skip": None, "stdout": ""}
    if col < 0:
        _abortar(config, alias, data_pasta, f"coluna '{alias}' nao encontrada no grid")
        return {"ok": False, "erro": "col nao achada", "skip": None, "stdout": ""}
    if canal_idx != col:
        _log(f"NOTA: canal_idx={canal_idx} != col_grid={col} (usando col_grid, correto)")

    cel = _buscar_celula(vps, row, col)

    # --- checagem 1b: o MP4 e' DESTE alias? (barreira anti-troca barata) ---
    # O nome e' <ALIAS>_<YYYYMMDD>_NN.mp4. Se o prefixo nao bate, alguem passou
    # o video errado -> nao sobe (publicar video trocado e' pior que nao publicar).
    if not vp.name.upper().startswith(alias + "_"):
        _abortar(config, alias, data_pasta, f"MP4 nao pertence a {alias}: {vp.name}")
        return {"ok": False, "erro": "mp4 de outro canal", "skip": None, "stdout": ""}

    # --- checagem 2: idempotencia ---
    # ATENCAO (risco #7): ter video_id NAO significa "terminado". Se o fluxo
    # morreu no meio, o video existe mas pode estar sem CTA/thumb/agendamento.
    #   status "scheduled" => 100% confirmado -> no-op (protege de DUPLICATA)
    #   status != scheduled => video no ar porem INCOMPLETO -> NAO re-sobe,
    #                          apenas RECONCILIA (completa o que falta)
    vid_existente = cel.get("youtube_video_id")
    if vid_existente:
        if (cel.get("upload_status") or "") == "scheduled":
            _log(f"SKIP {alias} {data_pasta}: ja concluido ({vid_existente})")
            return {"ok": True, "skip": "ja uploaded", "erro": None, "stdout": "",
                    "video_id": vid_existente}
        _log(f"{alias} {data_pasta}: video {vid_existente} existe mas status="
             f"{cel.get('upload_status')!r} — RECONCILIANDO (sem re-subir)")
        thumb_r = None
        if thumb_pre is not None:
            thumb_r = thumb_pre or None       # esteira ja resolveu a thumb
        elif cfg.get("thumb_gen", True):
            try:
                import thumb_pipeline
                thumb_r = thumb_pipeline.gerar(alias, data_pasta,
                                               timeout_seg=int(cfg.get("thumb_timeout", 900)))
            except Exception:
                pass
        _reconciliar(config, Path(cfg.get("repo") or ""), canal_yt, alias, row, col,
                     data_pasta, vid_existente, thumb_r, slot, tz)
        return {"ok": True, "skip": "reconciliado", "erro": None, "stdout": "",
                "video_id": vid_existente}

    # --- checagem 2b: JOURNAL LOCAL (anti-duplicata que nao depende da rede) ---
    # Chegar aqui significa que o GRID nao tem video_id. Mas o grid e' escrito
    # pela rede: se o mark falhou depois de um upload OK, o video ESTA no ar e o
    # grid nao sabe. Sem esta checagem, a proxima passada duplicaria.
    jr = _journal_ler(alias, data_pasta)
    est_j = (jr or {}).get("estado") or ""
    if est_j in ("subiu", "confirmado"):
        vid_j = jr.get("video_id") or ""
        _log(f"JOURNAL diz que {alias} {data_pasta} JA SUBIU ({vid_j or 'sem id'}) "
             f"mas o grid nao tem video_id — a marcacao se perdeu. NAO re-subo; reconcilio.")
        _reconciliar(config, Path(cfg.get("repo") or ""), canal_yt, alias, row, col,
                     data_pasta, vid_j, None, slot, tz)
        return {"ok": True, "skip": "journal: ja subiu (grid ressincronizado)",
                "erro": None, "stdout": "", "video_id": vid_j}
    if est_j == "subindo":
        # O processo anterior morreu ENTRE o inicio e o fim do upload. Nao da pra
        # saber se o YouTube recebeu. Subir agora pode DUPLICAR; nao subir apenas
        # atrasa. Escolha deliberada: nao sobe, marca dead-letter e pede olho humano.
        _marcar_falha(config, row, col, "upload anterior morreu no meio")
        _abortar(config, alias, data_pasta,
                 f"upload anterior morreu NO MEIO (journal={_journal_path(alias, data_pasta).name}). "
                 f"Confira o canal: se o video NAO esta la, apague o journal e re-dispare.")
        return {"ok": False, "erro": "journal: estado indeterminado", "skip": None, "stdout": ""}

    # --- checagem 3: titulo do grid bate com o que foi renderizado ---
    tit_grid = (cel.get("titulo") or "").strip()
    if not tit_grid:
        _abortar(config, alias, data_pasta, f"celula {row}_{col} sem titulo no grid")
        return {"ok": False, "erro": "sem titulo", "skip": None, "stdout": ""}
    if titulo_esperado and tit_grid.lower() != titulo_esperado.strip().lower():
        _abortar(config, alias, data_pasta,
                 f"titulo divergente!\ngrid: {tit_grid[:70]}\nrender: {titulo_esperado[:70]}")
        return {"ok": False, "erro": "titulo divergente", "skip": None, "stdout": ""}

    # --- lock por canal (CO3/CO4 compartilham o canal E o profile AdsPower) ---
    # O lock ESPERA a vez. Se mesmo assim estourar (45min), NAO some em silencio:
    # dead-letter no grid pro upload_daily_check re-tentar amanha.
    lock = _LockCanal(canal_yt)
    with lock as got:
        if got is None:
            _marcar_falha(config, row, col, "lock do canal ocupado alem do timeout")
            _abortar(config, alias, data_pasta,
                     f"canal {canal_yt} ocupado por >45min — dead-letter registrado")
            return {"ok": False, "erro": "canal ocupado (dead-letter)", "skip": None, "stdout": ""}

        repo = Path(cfg.get("repo") or "F:/Canal Dark/Apps Rapidos/drive-to-youtube")
        script = repo / "upload_one.py"
        py = repo / "venv" / "Scripts" / "python.exe"
        # --publish-utc: FONTE UNICA de horario e' o SLOT_MAP daqui. Antes o
        # upload_one derivava dos PUBLISH_SLOTS do Supabase — duas fontes pra
        # mesma decisao; divergindo, o verify "consertava" o horario toda vez.
        cmd = [str(py), "-u", str(script),
               "--canal", canal_yt, "--alias", alias,
               "--row", str(row), "--col", str(col),
               "--data", data_pasta, "--mp4", str(vp),
               "--publish-utc", _publish_utc(data_pasta, tz, slot)]
        if no_pin:
            cmd.append("--no-pin")   # esteira: PIN e' estagio proprio, fila global

        if cfg.get("dry_run", True):
            _log(f"DRY-RUN {alias} {data_pasta} row={row} col={col} "
                 f"slot={slot} {tz} | titulo='{tit_grid[:50]}'")
            _log(f"DRY-RUN cmd: {' '.join(cmd)}")
            return {"ok": True, "skip": "dry_run", "erro": None, "stdout": " ".join(cmd)}

        if not script.exists():
            _abortar(config, alias, data_pasta, f"upload_one.py nao existe em {repo}")
            return {"ok": False, "erro": "upload_one.py ausente", "skip": None, "stdout": ""}

        # === THUMB ===
        # Esteira: veio pronta do estagio anterior (thumb_pre; "" = sem thumb).
        # Inline legado: gera aqui. REGRA DE OURO nos dois casos: falhou ->
        # sobe SEM thumb, publicacao e' prioridade, resolve-se depois.
        thumb_path = None
        if thumb_pre is not None:
            thumb_path = Path(thumb_pre) if thumb_pre else None
        elif cfg.get("thumb_gen", True):
            try:
                import thumb_pipeline
                thumb_path = thumb_pipeline.gerar(alias, data_pasta,
                                                  timeout_seg=int(cfg.get("thumb_timeout", 900)))
                _log(f"thumb: {thumb_path.name if thumb_path else 'NENHUMA (sobe sem thumb)'}")
            except Exception as e_th:
                _log(f"thumb_gen excecao ({type(e_th).__name__}: {e_th}) — sobe sem thumb")

        env = dict(os.environ, CHANNEL_ALIAS=canal_yt, PYTHONUTF8="1")
        _log(f"UPLOAD {alias} {data_pasta} row={row} col={col} slot={slot} {tz}")
        # WRITE-AHEAD: "estou subindo" ANTES de chamar. Se este processo morrer
        # no meio, a proxima passada ve 'subindo' e NAO re-sobe as cegas.
        _journal_escrever(alias, data_pasta, "subindo")
        p = subprocess.run(cmd, cwd=str(repo), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=int(cfg.get("timeout_seg", 3600)))
        out = (p.stdout or "").strip()
        _vid_out = _video_id_de(out)
        if _vid_out:
            _journal_escrever(alias, data_pasta, "subiu", _vid_out)
        if p.returncode != 0:
            # DEAD-LETTER: pode ter subido e morrido depois (foi o que houve em
            # 06/08). Se ha video_id no stdout/grid, reconcilia; senao registra
            # "falhou" pro upload_daily_check re-tentar — nunca some em silencio.
            vid = _video_id_de(out) or _video_id_do_grid(vps, row, col)
            if vid:
                _journal_escrever(alias, data_pasta, "subiu", vid)
                _log(f"rc={p.returncode} MAS existe video {vid} — reconciliando")
                _reconciliar(config, repo, canal_yt, alias, row, col, data_pasta,
                             vid, thumb_path, slot, tz)
                return {"ok": True, "skip": "rc!=0 mas reconciliado", "erro": None,
                        "stdout": out, "video_id": vid}
            _marcar_falha(config, row, col, f"rc={p.returncode}")
            _abortar(config, alias, data_pasta,
                     f"upload_one rc={p.returncode}: {(p.stderr or out)[-300:]}")
            return {"ok": False, "erro": f"rc={p.returncode}", "skip": None, "stdout": out}

        _log(f"UPLOAD OK {alias} {data_pasta}: {out[-200:]}")

        # === RECONCILIACAO (auto-correcao) ===
        # SEMPRE roda, mesmo com rc=0: o upload_one pode ter morrido no meio e
        # ainda assim deixar o video no ar (piloto 06/08 -> ficou unlisted, sem
        # CTA e sem agendamento). O verify le o estado REAL no YouTube e
        # completa thumb/playlist/comentario/pin/agendamento que faltarem.
        _reconciliar(config, repo, canal_yt, alias, row, col, data_pasta,
                     _vid_out, thumb_path, slot, tz)
        return {"ok": True, "skip": None, "erro": None, "stdout": out,
                "video_id": _vid_out or _video_id_do_grid(vps, row, col)}
