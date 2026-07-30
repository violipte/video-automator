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
    O_CREAT|O_EXCL e' atomico no Windows/NTFS. Lock velho (>2h) = orfao, e' roubado."""

    def __init__(self, canal: str):
        LOCK_DIR.mkdir(parents=True, exist_ok=True)
        self.path = LOCK_DIR / f"{canal}.lock"
        self.fd = None

    def __enter__(self):
        for _ in range(2):
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
                return None  # ocupado por outro upload do mesmo canal
        return None

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


def _aplicar_thumb(config: dict, repo: Path, canal_yt: str, video_id: str, thumb: Path):
    """set_thumbnail no video ja subido. Best-effort: falha aqui NAO derruba nada
    (o video ja esta no ar/agendado — thumb e' cosmetica e resolve-se depois)."""
    if not video_id:
        _log("thumb NAO aplicada: video_id nao veio no stdout do upload_one")
        return
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
        else:
            _log(f"thumb NAO aplicada (rc={r.returncode}): {(r.stderr or r.stdout or '')[-200:]}")
    except Exception as e:
        _log(f"thumb NAO aplicada ({type(e).__name__}: {e})")


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
        try:
            import urllib.request
            body = json.dumps({"row": row, "col": col, "youtube_video_id": video_id,
                               "youtube_publish_at": res.get("publish_at") or pub,
                               "youtube_url": f"https://youtube.com/watch?v={video_id}",
                               "upload_status": "scheduled" if res.get("ok") else "incompleto"}).encode()
            req = urllib.request.Request(f"{vps}/api/upload/mark", data=body,
                                         headers={"Content-Type": "application/json"})
            urllib.request.urlopen(req, timeout=30).read()
            _log("grid marcado (scheduled)" if res.get("ok") else "grid marcado (INCOMPLETO)")
        except Exception as e:
            _log(f"marca do grid falhou: {type(e).__name__}: {e}")
    except Exception as e:
        _log(f"reconciliacao falhou ({type(e).__name__}: {e}) — video ja esta no ar")


def disparar(config: dict, alias: str, canal_idx: int, data_pasta: str,
             video_path: str, titulo_esperado: str = "") -> dict:
    """Dispara o upload de UM video recem-renderizado.

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


def _disparar_interno(config, alias, canal_idx, data_pasta, video_path, titulo_esperado):
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
            return {"ok": True, "skip": "ja uploaded", "erro": None, "stdout": ""}
        _log(f"{alias} {data_pasta}: video {vid_existente} existe mas status="
             f"{cel.get('upload_status')!r} — RECONCILIANDO (sem re-subir)")
        thumb_r = None
        if cfg.get("thumb_gen", True):
            try:
                import thumb_pipeline
                thumb_r = thumb_pipeline.gerar(alias, data_pasta,
                                               timeout_seg=int(cfg.get("thumb_timeout", 900)))
            except Exception:
                pass
        _reconciliar(config, Path(cfg.get("repo") or ""), canal_yt, alias, row, col,
                     data_pasta, vid_existente, thumb_r, slot, tz)
        return {"ok": True, "skip": "reconciliado", "erro": None, "stdout": ""}

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
    lock = _LockCanal(canal_yt)
    with lock as got:
        if got is None:
            _log(f"SKIP {alias}: canal {canal_yt} com upload em andamento (lock)")
            return {"ok": False, "skip": "canal ocupado", "erro": None, "stdout": ""}

        repo = Path(cfg.get("repo") or "F:/Canal Dark/Apps Rapidos/drive-to-youtube")
        script = repo / "upload_one.py"
        py = repo / "venv" / "Scripts" / "python.exe"
        cmd = [str(py), "-u", str(script),
               "--canal", canal_yt, "--alias", alias,
               "--row", str(row), "--col", str(col),
               "--data", data_pasta, "--mp4", str(vp)]

        if cfg.get("dry_run", True):
            _log(f"DRY-RUN {alias} {data_pasta} row={row} col={col} "
                 f"slot={slot} {tz} | titulo='{tit_grid[:50]}'")
            _log(f"DRY-RUN cmd: {' '.join(cmd)}")
            return {"ok": True, "skip": "dry_run", "erro": None, "stdout": " ".join(cmd)}

        if not script.exists():
            _abortar(config, alias, data_pasta, f"upload_one.py nao existe em {repo}")
            return {"ok": False, "erro": "upload_one.py ausente", "skip": None, "stdout": ""}

        # === THUMB: gera AQUI (fim do render, ANTES do upload) ===
        # Nano Banana (0 cred) + revisor Gemini. REGRA DE OURO: se falhar,
        # thumb_pipeline devolve None e o upload segue SEM thumb —
        # publicacao e' prioridade, thumb resolve-se depois.
        thumb_path = None
        if cfg.get("thumb_gen", True):
            try:
                import thumb_pipeline
                thumb_path = thumb_pipeline.gerar(alias, data_pasta,
                                                  timeout_seg=int(cfg.get("thumb_timeout", 900)))
                _log(f"thumb: {thumb_path.name if thumb_path else 'NENHUMA (sobe sem thumb)'}")
            except Exception as e_th:
                _log(f"thumb_gen excecao ({type(e_th).__name__}: {e_th}) — sobe sem thumb")

        env = dict(os.environ, CHANNEL_ALIAS=canal_yt, PYTHONUTF8="1")
        _log(f"UPLOAD {alias} {data_pasta} row={row} col={col} slot={slot} {tz}")
        p = subprocess.run(cmd, cwd=str(repo), env=env, capture_output=True, text=True, encoding="utf-8", errors="replace",
                           timeout=int(cfg.get("timeout_seg", 3600)))
        out = (p.stdout or "").strip()
        if p.returncode != 0:
            # DEAD-LETTER: pode ter subido e morrido depois (foi o que houve em
            # 06/08). Se ha video_id no stdout/grid, reconcilia; senao registra
            # "falhou" pro upload_daily_check re-tentar — nunca some em silencio.
            vid = _video_id_de(out) or _video_id_do_grid(vps, row, col)
            if vid:
                _log(f"rc={p.returncode} MAS existe video {vid} — reconciliando")
                _reconciliar(config, repo, canal_yt, alias, row, col, data_pasta,
                             vid, thumb_path, slot, tz)
                return {"ok": True, "skip": "rc!=0 mas reconciliado", "erro": None, "stdout": out}
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
                     _video_id_de(out), thumb_path, slot, tz)
        return {"ok": True, "skip": None, "erro": None, "stdout": out}
