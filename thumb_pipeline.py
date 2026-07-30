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


def _revisar(paths: list) -> Path:
    """pick_best_thumb (Gemini vision) no venv do drive-to-youtube. Fallback: 1ª."""
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
            if m.get("typo_options"):
                _log(f"  descartadas (typo): {m['typo_options']}")
            if m.get("glitch_options"):
                _log(f"  descartadas (glitch): {m['glitch_options']}")
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

        # projeto NOVO dedicado por (data, canal) — sem --proj o driver cria um
        _log(f"gerando {n} thumbs no Nano Banana (projeto novo) -> {out}")
        r = subprocess.run([str(VEO_PY), str(AQUI / "thumb_gen" / "thumb_nano.py"),
                            str(lote_f), str(out), "2"],
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout_seg,
                           env=dict(os.environ, PYTHONUTF8="1"))
        for ln in (r.stdout or "").splitlines()[-6:]:
            _log(f"  driver| {ln.strip()}")

        imgs = sorted(p for p in out.glob(f"{canal}_*.jpg") if p.stat().st_size > 20_000)
        if not imgs:
            _log(f"NENHUMA imagem gerada (rc={r.returncode}) — upload segue SEM thumb")
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
