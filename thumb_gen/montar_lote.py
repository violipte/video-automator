# -*- coding: utf-8 -*-
"""Monta o lote de 4 prompts de thumb (mesma logica do Prompt Calendar, mode=normal):
lookup na API de Temas -> auto-split do thumb em 2 linhas -> template + 4 CENAS do pool.

Uso: python montar_lote_thumb.py ENO2 05/08/2026 <saida.json>
"""
import json, io, sys, random, urllib.request

CANAL = sys.argv[1] if len(sys.argv) > 1 else "ENO2"
DATA = sys.argv[2] if len(sys.argv) > 2 else "05/08/2026"
OUT = sys.argv[3] if len(sys.argv) > 3 else "lote_thumb.json"
N = 4
TPL = r"F:\Canal Dark\Apps Rapidos\drive-to-youtube\prompt-mixer-backup.json"

# --- 1) lookup (mesma API que o Prompt Calendar usa) ---
url = f"http://85.239.243.215:8500/api/temas/lookup?canal={CANAL}&data={DATA}"
with urllib.request.urlopen(url, timeout=40) as r:
    look = json.loads(r.read().decode("utf-8"))
if not look.get("ok"):
    print("ABORT: lookup falhou:", look); sys.exit(2)
titulo = (look.get("titulo") or "").strip()
thumb1 = (look.get("thumb") or "").strip()
print(f"titulo: {titulo[:80]}")
print(f"thumb : {thumb1}")
if not thumb1:
    print("ABORT: sem texto de thumb na celula"); sys.exit(2)

# --- 2) auto-split em 2 linhas no espaco mais proximo do meio (regra do Calendar) ---
def split2(s):
    s = " ".join(s.split())
    if " " not in s:
        return s, ""
    meio = len(s) / 2
    pos = [i for i, c in enumerate(s) if c == " "]
    corte = min(pos, key=lambda i: abs(i - meio))
    return s[:corte].strip(), s[corte:].strip()

cima, baixo = split2(thumb1)
print(f"linha1: {cima!r}\nlinha2: {baixo!r}")

# --- 3) template + pools ---
d = json.load(io.open(TPL, encoding="utf-8"))
items = d if isinstance(d, list) else (d.get("templates") or list(d.values()))
t = next(x for x in items if isinstance(x, dict) and (x.get("name") or x.get("nome")) == CANAL)
base = t["prompt"]
vars_ = t.get("vars") or {}
cenas = list(vars_.get("cena") or [])
chars = list(vars_.get("character") or [])
if not cenas:
    print("ABORT: template sem pool de cena"); sys.exit(2)

random.seed()  # variacao real a cada run
escolhidas = random.sample(cenas, min(N, len(cenas)))
character = chars[0] if chars else ""

lote = []
for i, cena in enumerate(escolhidas, 1):
    p = (base.replace("[CENA]", cena)
             .replace("[CHARACTER]", character)
             .replace("[TEXTO DE CIMA]", cima)
             .replace("[TEXTO DE BAIXO]", baixo))
    # Tag no inicio (regra do Calendar) + MARCADOR UNICO (canal/data/variante).
    # CRITICO: o veo_driver casa card<->prompt pelos PRIMEIROS 70 CHARS. Como o
    # template é o mesmo pras 4 variantes (a cena só muda no meio), sem o
    # marcador os 70 chars ficam idênticos e o casamento troca os arquivos
    # (visto no teste 05/08: arquivo v1 recebeu a imagem do prompt v2).
    marca = f"[{CANAL} {DATA[:2]}-{DATA[3:5]} v{i}]"
    if p.startswith("PROMPT: "):
        p = f"PROMPT: {marca} " + p[len("PROMPT: "):]
    else:
        p = f"{marca} " + p
    lote.append({"tipo": "imagem",
                 "arquivo": f"{CANAL}_{DATA[:2]}{DATA[3:5]}_v{i}.jpg",
                 "prompt": p,
                 "_cena": cena[:70]})

io.open(OUT, "w", encoding="utf-8").write(json.dumps(lote, ensure_ascii=False, indent=2))
print(f"\n=== {len(lote)} prompts -> {OUT} ===")
for it in lote:
    print(f"  {it['arquivo']}: cena={it['_cena']}")
print(f"\nprompt[0] (300c): {lote[0]['prompt'][:300]}")
