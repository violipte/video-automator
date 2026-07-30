"""Gera a thumbnail do tema no Nano Banana (Flow) e escolhe a melhor via revisor.

Roda LOCAL, chamado pelo upload_trigger APÓS o render e ANTES do upload.
Provado 30/07: 4 thumbs ENO2 em 124s, 0 créditos, 0 falhas.

CADEIA:
  API de Temas (titulo/thumb)  ->  template do Prompt Calendar (mode=normal,
  pool de cenas)  ->  N prompts  ->  Nano Banana 2 · 16:9 (Playwright/Flow)
  ->  pick_best_thumb (Gemini vision: rejeita typo E glitch)  ->  a melhor.

⚠️ REGRA DE OURO (Piter 30/07): **THUMB NUNCA BLOQUEIA PUBLICAÇÃO.** Qualquer
falha aqui devolve None e o upload segue SEM thumb (resolve-se depois). Por isso
tudo é try/except e nada levanta pra cima.

3 venvs diferentes -> subprocess:
  - veo_venv                : Playwright (gera no Flow)
  - drive-to-youtube/venv   : requests (revisor Gemini)
  - este (Python314)        : orquestra
"""
import json
import os
import random
import subprocess
import sys
import time
import urllib.request
from pathlib import Path

RAIZ = Path("F:/Canal Dark/Aplicativo de Edição")
AQUI = Path(__file__).parent
VEO_PY = Path("F:/Canal Dark/veo_venv/Scripts/python.exe")
DTY = Path("F:/Canal Dark/Apps Rapidos/drive-to-youtube")
DTY_PY = DTY / "venv" / "Scripts" / "python.exe"
TEMPLATES = DTY / "prompt-mixer-backup.json"   # mesmo arquivo do Prompt Calendar
API_TEMAS = "http://85.239.243.215:8500"
N_VARIANTES = 4


def _log(m):
    print(f"[thumb_pipeline] {m}", flush=True)


# ---------------------------------------------------------------- lock GLOBAL
# O `chrome_profile` do Flow e' UM SO. O lock do upload_trigger e' POR CANAL —
# entao 2 canais renderizando juntos (sao 4 render workers) chamariam este
# modulo ao mesmo tempo e subiriam 2 Playwright no MESMO perfil: perfil
# corrompido = perde o login do Flow e a thumb quebra pra TODOS os canais.
# Aqui o lock e' GLOBAL (1 geracao por vez na maquina) e ESPERA a vez, em vez
# de desistir — a thumb pode atrasar, mas nao pode corromper o perfil.
_LOCK = Path(os.environ.get("TEMP", ".")) / "automator_upload_locks" / "flow_chrome.lock"
_LOCK_STALE = 1800   # 30min: geracao de 4 thumbs leva ~2-3min; mais que isso e' orfao


class _LockFlow:
    def __init__(self, espera_max=900):
        self.espera_max = espera_max
        self.fd = None

    def __enter__(self):
        _LOCK.parent.mkdir(parents=True, exist_ok=True)
        t0 = time.time()
        avisou = False
        while time.time() - t0 < self.espera_max:
            try:
                self.fd = os.open(str(_LOCK), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
                os.write(self.fd, f"{os.getpid()} {time.time()}".encode())
                return self
            except FileExistsError:
                try:
                    idade = time.time() - _LOCK.stat().st_mtime
                except OSError:
                    idade = 0
                if idade > _LOCK_STALE:
                    _log(f"lock do Flow orfao ({idade/60:.0f}min) — roubando")
                    _LOCK.unlink(missing_ok=True)
                    continue
                if not avisou:
                    _log("outro canal esta gerando thumb — aguardando a vez do Chrome/Flow")
                    avisou = True
                time.sleep(10)
        _log(f"nao consegui o lock do Flow em {self.espera_max}s — sobe SEM thumb")
        return None

    def __exit__(self, *exc):
        try:
            if self.fd is not None:
                os.close(self.fd)
                _LOCK.unlink(missing_ok=True)
        except OSError:
            pass


def _split2(s: str):
    """thumb vem em 1 linha -> 2 linhas no espaço mais próximo do meio (regra do Calendar)."""
    s = " ".join((s or "").split())
    if " " not in s:
        return s, ""
    meio = len(s) / 2
    corte = min((i for i, c in enumerate(s) if c == " "), key=lambda i: abs(i - meio))
    return s[:corte].strip(), s[corte:].strip()


def _lookup(canal: str, data_br: str) -> dict:
    url = f"{API_TEMAS}/api/temas/lookup?canal={canal}&data={data_br}"
    with urllib.request.urlopen(url, timeout=40) as r:
        return json.loads(r.read().decode("utf-8"))


def _montar_lote(canal: str, data_br: str, n: int) -> list:
    """N prompts = template do canal + N cenas distintas do pool + textos da célula."""
    look = _lookup(canal, data_br)
    if not look.get("ok"):
        raise RuntimeError(f"lookup falhou: {str(look)[:120]}")
    thumb_txt = (look.get("thumb") or "").strip()
    if not thumb_txt:
        raise RuntimeError("célula sem texto de thumb")
    cima, baixo = _split2(thumb_txt)

    d = json.loads(TEMPLATES.read_text(encoding="utf-8"))
    items = d if isinstance(d, list) else (d.get("templates") or list(d.values()))
    t = next((x for x in items if isinstance(x, dict)
              and (x.get("name") or x.get("nome")) == canal), None)
    if not t:
        raise RuntimeError(f"template '{canal}' não existe no prompt-mixer-backup.json")
    base = t.get("prompt") or ""
    v = t.get("vars") or {}
    cenas = list(v.get("cena") or [])
    chars = list(v.get("character") or [])
    if not cenas:
        raise RuntimeError(f"template '{canal}' sem pool de cena")

    escolhidas = random.sample(cenas, min(n, len(cenas)))
    character = chars[0] if chars else ""
    dd, mm = data_br[:2], data_br[3:5]
    lote = []
    for i, cena in enumerate(escolhidas, 1):
        p = (base.replace("[CENA]", cena).replace("[CHARACTER]", character)
                 .replace("[TEXTO DE CIMA]", cima).replace("[TEXTO DE BAIXO]", baixo))
        # MARCADOR ÚNICO no início: o veo_driver casa card<->prompt pelos 70
        # primeiros chars. O template é igual nas N variantes (a cena muda no
        # meio) -> sem isso o casamento TROCA os arquivos (visto em 05/08).
        marca = f"[{canal} {dd}-{mm} v{i}]"
        p = (f"PROMPT: {marca} " + p[len("PROMPT: "):]) if p.startswith("PROMPT: ") else f"{marca} {p}"
        lote.append({"tipo": "imagem", "arquivo": f"{canal}_{dd}{mm}_v{i}.jpg", "prompt": p})
    _log(f"{len(lote)} prompts | linha1={cima!r} linha2={baixo!r}")
    return lote


def _revisar(paths: list):
    """pick_best_thumb (Gemini vision) no venv do drive-to-youtube.

    Devolve o Path da melhor, ou **None** se TODAS foram reprovadas (typo/glitch)
    — nota de corte: melhor publicar sem thumb do que com thumb quebrada.
    Se o revisor em si falhar (quota/rede), cai pra 1ª (fail-safe do lado deles)."""
    code = (
        "import sys,json;sys.path.insert(0,r'%s');"
        "from pathlib import Path;from lib.thumb_picker import pick_best_thumb;"
        "ps=[Path(x) for x in sys.argv[1:]];b,m=pick_best_thumb(ps);"
        "print('__PICK__'+json.dumps({'best':str(b),'meta':m},ensure_ascii=False))"
    ) % str(DTY)
    r = subprocess.run([str(DTY_PY), "-c", code] + [str(p) for p in paths],
                       cwd=str(DTY), capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=300,
                       env=dict(os.environ, PYTHONUTF8="1"))
    for ln in (r.stdout or "").splitlines():
        if ln.startswith("__PICK__"):
            o = json.loads(ln[len("__PICK__"):])
            m = o["meta"]
            _log(f"revisor: escolheu {Path(o['best']).name} | {str(m.get('reason'))[:70]}")
            ruins = set(m.get("typo_options") or []) | set(m.get("glitch_options") or [])
            if m.get("typo_options"):
                _log(f"  descartadas (typo): {m['typo_options']}")
            if m.get("glitch_options"):
                _log(f"  descartadas (glitch): {m['glitch_options']}")
            # NOTA DE CORTE: se o revisor reprovou TODAS, nao publicar nenhuma.
            if len(ruins) >= len(paths):
                _log(f"  TODAS as {len(paths)} reprovadas — sobe SEM thumb")
                return None
            return Path(o["best"])
    _log(f"revisor falhou (rc={r.returncode}) — usando a 1ª. err={(r.stderr or '')[-200:]}")
    return paths[0]


def gerar(canal: str, data_iso: str, timeout_seg: int = 900, n: int = N_VARIANTES):
    """Gera N thumbs, revisa e devolve o Path da melhor. None se qualquer coisa falhar.

    canal: alias do grid (ex 'ENO2') · data_iso: 'YYYY-MM-DD'
    NUNCA levanta: thumb não pode bloquear publicação.
    """
    try:
        y, m, d = data_iso.split("-")
        data_br = f"{d}/{m}/{y}"
        out = RAIZ / "veo_clips" / "thumbs" / f"{data_iso}_{canal}"
        out.mkdir(parents=True, exist_ok=True)

        lote = _montar_lote(canal, data_br, n)
        lote_f = out / "_lote.json"
        lote_f.write_text(json.dumps(lote, ensure_ascii=False, indent=2), encoding="utf-8")

        # PROJETO POR DATA (esteira, Piter 30/07): todos os canais do dia usam o
        # MESMO projeto do Flow — a sessao/config persiste, e o marcador unico
        # [CANAL dd-mm vN] no inicio de cada prompt impede o driver de casar
        # card de um canal com prompt de outro.
        import esteira
        proj = esteira.flow_proj_da_data(data_iso)

        # LOCK GLOBAL: 1 geracao por vez na maquina (chrome_profile e' unico)
        with _LockFlow() as lk:
            if lk is None:
                return None       # nao conseguiu a vez -> sobe sem thumb
            _log(f"gerando {n} thumbs no Nano Banana (projeto "
                 f"{proj[:12] + '…' if proj else 'NOVO'}) -> {out}")
            cmd = [str(VEO_PY), str(AQUI / "thumb_gen" / "thumb_nano.py"),
                   str(lote_f), str(out), "2"]
            if proj:
                cmd.append(proj)
            r = subprocess.run(cmd, capture_output=True, text=True, encoding="utf-8",
                               errors="replace", timeout=timeout_seg,
                               env=dict(os.environ, PYTHONUTF8="1"))
        for ln in (r.stdout or "").splitlines()[-6:]:
            _log(f"  driver| {ln.strip()}")
        # captura o id do projeto ("projeto: <uuid>" vem do flow_driver) pra
        # proxima thumb do MESMO dia reusar. Projeto morto/apagado no Flow:
        # o driver falha, a gente detecta 0 imagens e zera o registro (abaixo).
        for ln in (r.stdout or "").splitlines():
            ln = ln.strip()
            if ln.startswith("projeto: "):
                esteira.flow_proj_gravar(data_iso, ln.split("projeto: ", 1)[1].strip())
                break

        imgs = sorted(p for p in out.glob(f"{canal}_*.jpg") if p.stat().st_size > 20_000)
        if not imgs:
            _log(f"NENHUMA imagem gerada (rc={r.returncode}) — upload segue SEM thumb")
            if proj:
                # projeto reusado pode ter morrido no Flow — zera o registro pra
                # proxima tentativa do dia criar um projeto novo em vez de
                # bater no mesmo projeto quebrado pra sempre.
                esteira.flow_proj_gravar(data_iso, "")
            return None
        _log(f"{len(imgs)} imagens geradas")
        return _revisar(imgs)
    except Exception as e:
        _log(f"FALHOU ({type(e).__name__}: {e}) — upload segue SEM thumb (regra de ouro)")
        return None


if __name__ == "__main__":
    canal = sys.argv[1] if len(sys.argv) > 1 else "ENO2"
    data = sys.argv[2] if len(sys.argv) > 2 else "2026-08-06"
    best = gerar(canal, data)
    print(f"\n>>> MELHOR: {best}")
