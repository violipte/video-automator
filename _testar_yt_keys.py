"""Testa/registra chaves da YouTube Data API v3 pro Niche Spy.

Uso:
  python _testar_yt_keys.py                          # testa as que já estão no config.json
  python _testar_yt_keys.py EN=AIza... EN3=AIza...   # adiciona ao config.json e testa
  python _testar_yt_keys.py --search                 # testa também search.list (custa 100 unidades!)

Diagnóstico dos erros comuns:
  accessNotConfigured -> a YouTube Data API v3 não está ATIVADA no projeto da key
  forbidden/blocked   -> a key tem RESTRIÇÃO de API (liberar "YouTube Data API v3" nela)
  keyInvalid          -> key errada/revogada
  quotaExceeded       -> cota do dia estourada (10.000 unidades por projeto)
"""
import json
import sys
import urllib.error
import urllib.parse
import urllib.request
from pathlib import Path

CONFIG = Path(__file__).parent / "config.json"
API = "https://www.googleapis.com/youtube/v3"


def _get(endpoint, params, key):
    q = urllib.parse.urlencode({**params, "key": key})
    try:
        with urllib.request.urlopen(f"{API}/{endpoint}?{q}", timeout=25) as r:
            return True, json.loads(r.read())
    except urllib.error.HTTPError as e:
        try:
            err = json.loads(e.read())["error"]
            reason = (err.get("errors") or [{}])[0].get("reason", "?")
            return False, f"HTTP{e.code}/{reason}: {err.get('message','')[:120]}"
        except Exception:
            return False, f"HTTP{e.code}"
    except Exception as e:
        return False, f"ERRO: {str(e)[:100]}"


def testar(nome, key, com_search=False):
    ok, d = _get("channels", {"part": "snippet,statistics", "forHandle": "@MrBeast"}, key)
    if not ok:
        print(f"  ❌ {nome:<14} channels.list -> {d}")
        return False
    it = (d.get("items") or [{}])[0]
    subs = (it.get("statistics") or {}).get("subscriberCount", "?")
    print(f"  ✅ {nome:<14} channels.list OK  ({it.get('snippet',{}).get('title','?')} · {subs} subs)")
    if com_search:
        ok2, d2 = _get("search", {"part": "snippet", "q": "dark documentary",
                                  "type": "channel", "maxResults": "3"}, key)
        if ok2:
            n = len(d2.get("items", []))
            print(f"     ✅ search.list OK ({n} canais) — 100 unidades gastas")
        else:
            print(f"     ❌ search.list -> {d2}")
            return False
    return True


def main():
    args = [a for a in sys.argv[1:] if a != "--search"]
    com_search = "--search" in sys.argv
    cfg = json.load(open(CONFIG, encoding="utf-8"))
    keys = cfg.get("youtube_api_keys") or []

    # novas chaves via argv: NOME=AIza...
    for a in args:
        if "=" in a:
            nome, k = a.split("=", 1)
            keys = [x for x in keys if x.get("id") != nome] + [{"id": nome, "key": k.strip()}]
    if args:
        cfg["youtube_api_keys"] = keys
        json.dump(cfg, open(CONFIG, "w", encoding="utf-8"), ensure_ascii=False, indent=2)
        print(f"config.json atualizado: {len(keys)} chave(s)\n")

    if not keys:
        print("Nenhuma chave. Use: python _testar_yt_keys.py EN=AIza... EN3=AIza...")
        return
    print(f"Testando {len(keys)} chave(s){' + search.list' if com_search else ''}:\n")
    bons = sum(testar(k.get("id", "?"), k["key"], com_search) for k in keys if k.get("key"))
    print(f"\n>>> {bons}/{len(keys)} chave(s) prontas pro Niche Spy")


if __name__ == "__main__":
    main()
