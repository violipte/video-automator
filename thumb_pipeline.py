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


# ------------------------------------------------- Calendar (fonte dos modos)
# REGRA (Piter 31/07): os 3 modos do Prompt Calendar (normal/llm/compose) sao
# usados e precisam funcionar AQUI exatamente como funcionam LA. Por isso o
# pipeline fala com o PROPRIO Calendar (:8889) — templates live, llm-generate
# (keys/cache do lado dele) e compose (fontes/bg do lado dele). Nada e'
# reimplementado; o backup local so' segura a barra se o servico cair.
CALENDAR = "http://85.239.243.215:8889"

# canal do grid -> nome do template no Calendar (excecoes; default = o proprio alias)
TEMPLATE_DE = {"CO1": "CO", "CO2": "CO"}


def _template_do_calendar(nome: str) -> dict:
    """Template LIVE do Calendar (edicao la vale aqui na hora); fallback local."""
    try:
        with urllib.request.urlopen(f"{CALENDAR}/api/templates", timeout=15) as r:
            d = json.loads(r.read().decode("utf-8"))
    except Exception as e:
        _log(f"Calendar indisponivel ({type(e).__name__}) — usando backup local")
        d = json.loads(TEMPLATES.read_text(encoding="utf-8"))
    items = d if isinstance(d, list) else (d.get("templates") or list(d.values()))
    t = next((x for x in items if isinstance(x, dict)
              and (x.get("name") or x.get("nome")) == nome), None)
    if not t:
        raise RuntimeError(f"template '{nome}' nao existe no Calendar")
    return t


def _modo(t: dict) -> str:
    if t.get("compose"):
        return "compose"
    return (t.get("mode") or "normal").strip().lower()


def _marcar_prompt(p: str, marca: str) -> str:
    """Marcador unico no inicio: o veo_driver casa card<->prompt pelos 70
    primeiros chars — sem isso o casamento TROCA os arquivos (visto em 05/08)."""
    return (f"PROMPT: {marca} " + p[len("PROMPT: "):]) if p.startswith("PROMPT: ") else f"{marca} {p}"


def _lote_normal(canal: str, t: dict, thumb_txt: str, data_br: str, n: int) -> list:
    """Modo NORMAL: substituicao mecanica. Pool de cenas quando existe; sem
    pool = cena EMBUTIDA no proprio prompt (ASH/CO3/CO4) — repete o prompt e a
    variacao vem do sampling do Nano Banana."""
    cima, baixo = _split2(thumb_txt)
    base = t.get("prompt") or ""
    v = t.get("vars") or {}
    cenas = list(v.get("cena") or [])
    chars = list(v.get("character") or [])
    character = chars[0] if chars else ""
    if cenas:
        escolhidas = random.sample(cenas, min(n, len(cenas)))
    else:
        escolhidas = [None] * n           # cena ja embutida no prompt base
    dd, mm = data_br[:2], data_br[3:5]
    lote = []
    for i, cena in enumerate(escolhidas, 1):
        p = base
        if cena is not None:
            p = p.replace("[CENA]", cena)
        p = (p.replace("[CHARACTER]", character)
              .replace("[TEXTO DE CIMA]", cima).replace("[TEXTO DE BAIXO]", baixo))
        lote.append({"tipo": "imagem", "arquivo": f"{canal}_{dd}{mm}_v{i}.jpg",
                     "prompt": _marcar_prompt(p, f"[{canal} {dd}-{mm} v{i}]")})
    _log(f"normal: {len(lote)} prompts ({'pool ' + str(len(cenas)) if cenas else 'cena embutida'}) "
         f"| linha1={cima!r} linha2={baixo!r}")
    return lote


def _lote_llm(canal: str, tpl_nome: str, look: dict, data_br: str, n: int) -> list:
    """Modo LLM: o Calendar compoe o prompt final (template = system prompt,
    titulo+thumb = user; keys em llm-keys.json DO LADO DELE). 1a chamada usa o
    cache do Calendar; as demais vao com force=true — temperatura 0.8 da a
    variacao entre as N. Split de subtitulo (\\n) e' do Calendar, nao daqui."""
    dd, mm = data_br[:2], data_br[3:5]
    lote = []
    for i in range(1, n + 1):
        body = json.dumps({"canal": canal, "data": data_br,
                           "titulo": look.get("titulo") or "",
                           "thumb": look.get("thumb") or "",
                           "template_name": tpl_nome,
                           "force": i > 1}).encode()
        req = urllib.request.Request(f"{CALENDAR}/api/llm-generate", data=body,
                                     headers={"Content-Type": "application/json"})
        with urllib.request.urlopen(req, timeout=180) as r:
            res = json.loads(r.read().decode("utf-8"))
        p = (res.get("prompt") or "").strip()
        if not p:
            raise RuntimeError(f"llm-generate voltou vazio (v{i})")
        lote.append({"tipo": "imagem", "arquivo": f"{canal}_{dd}{mm}_v{i}.jpg",
                     "prompt": _marcar_prompt(p, f"[{canal} {dd}-{mm} v{i}]")})
        _log(f"llm v{i}: prompt de {len(p)} chars"
             + (" (cache do Calendar)" if res.get("cached") else f" ({res.get('elapsed_s')}s)"))
    return lote


def _compose_thumb(canal: str, tpl_nome: str, thumb_txt: str, data_br: str, out: Path):
    """Modo COMPOSE: o Calendar monta o PNG final (bg+fonte+gradiente do lado
    dele) — sem Nano Banana e sem revisor (deterministico). Devolve o Path."""
    txt = (thumb_txt or "").replace("\r\n", "\n").strip()
    if "\n" in txt:
        line1, line2 = (s.strip() for s in txt.split("\n", 1))
    else:
        line1, line2 = txt, ""
    body = json.dumps({"canal": canal, "data": data_br, "line1": line1,
                       "line2": line2, "template_name": tpl_nome}).encode()
    req = urllib.request.Request(f"{CALENDAR}/api/compose-thumb", data=body,
                                 headers={"Content-Type": "application/json"})
    with urllib.request.urlopen(req, timeout=60) as r:
        png = r.read()
    if len(png) < 10_000:
        raise RuntimeError(f"compose voltou {len(png)} bytes")
    dd, mm = data_br[:2], data_br[3:5]
    p = out / f"{canal}_{dd}{mm}_compose.png"
    p.write_bytes(png)
    _log(f"compose: PNG pronto ({len(png) // 1024}KB) — sem Nano Banana/revisor")
    return p


def _publicar_em_exports(canal: str, data_iso: str, thumb: Path):
    """Copia a escolhida pra pasta do dia nos Exports — canal SEM upload
    automatico pega a thumb pronta ali pro upload manual (Piter 31/07)."""
    try:
        import shutil
        dest_dir = Path("F:/Canal Dark/Automator Exports") / data_iso / "Thumbs"
        dest_dir.mkdir(parents=True, exist_ok=True)
        dest = dest_dir / f"{canal}{thumb.suffix}"
        shutil.copy2(str(thumb), str(dest))
        _log(f"thumb copiada pra {dest}")
    except Exception as e:
        _log(f"copia pros Exports falhou ({type(e).__name__}: {e}) — thumb segue em {thumb}")


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
    """Gera a thumb do tema NO MODO DO CANAL (normal/llm/compose, igual ao
    Calendar), revisa quando ha variantes e devolve o Path da melhor — ja
    copiada pra Automator Exports/{data}/Thumbs/ (upload manual pega la).
    None se qualquer coisa falhar. NUNCA levanta: thumb nao bloqueia publicacao.

    canal: alias do grid (ex 'ENO2', 'CO1') · data_iso: 'YYYY-MM-DD'
    """
    try:
        y, m, d = data_iso.split("-")
        data_br = f"{d}/{m}/{y}"
        out = RAIZ / "veo_clips" / "thumbs" / f"{data_iso}_{canal}"
        out.mkdir(parents=True, exist_ok=True)

        look = _lookup(canal, data_br)
        if not look.get("ok"):
            raise RuntimeError(f"lookup falhou: {str(look)[:120]}")
        thumb_txt = (look.get("thumb") or "").strip()
        if not thumb_txt:
            raise RuntimeError("célula sem texto de thumb")

        tpl_nome = TEMPLATE_DE.get(canal.upper(), canal)
        t = _template_do_calendar(tpl_nome)
        modo = _modo(t)
        _log(f"{canal} {data_br}: modo {modo.upper()} (template {tpl_nome})")

        # COMPOSE: o Calendar devolve o PNG final — nao passa por Nano Banana.
        if modo == "compose":
            p = _compose_thumb(canal, tpl_nome, thumb_txt, data_br, out)
            _publicar_em_exports(canal, data_iso, p)
            return p

        if modo == "llm":
            lote = _lote_llm(canal, tpl_nome, look, data_br, n)
        else:
            lote = _lote_normal(canal, t, thumb_txt, data_br, n)
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
        best = _revisar(imgs)
        if best:
            _publicar_em_exports(canal, data_iso, best)
        return best
    except Exception as e:
        _log(f"FALHOU ({type(e).__name__}: {e}) — upload segue SEM thumb (regra de ouro)")
        return None


if __name__ == "__main__":
    canal = sys.argv[1] if len(sys.argv) > 1 else "ENO2"
    data = sys.argv[2] if len(sys.argv) > 2 else "2026-08-06"
    best = gerar(canal, data)
    print(f"\n>>> MELHOR: {best}")
