# -*- coding: utf-8 -*-
"""Gera thumbs no Nano Banana (Flow) reusando o veo_driver do outro Claude.

O veo_driver ja resolve o dificil (fila, casamento prompt<->card, download).
A UNICA coisa que falta pra IMAGEM e' a config do popup: o `configurar_video`
clica a aba "Video"; Nano Banana esta na aba "Imagem". Entao aqui eu troco
(monkey-patch) `fd.configurar_video` pela minha `configurar_imagem` e chamo o
main() do veo_driver — sem editar NENHUM arquivo do outro Claude.

Uso: python thumb_nano.py <lote.json> <out_dir> [fila] [proj_id]
"""
import re
import sys
from pathlib import Path

RAIZ = Path("F:/Canal Dark/Aplicativo de Edição")
sys.path.insert(0, str(RAIZ / "veo_flow"))
sys.path.insert(0, str(RAIZ / "banco-videos" / "teste"))

import flow_driver as fd          # noqa: E402
import veo_driver as vd           # noqa: E402

LOTE = sys.argv[1]
OUT = sys.argv[2]
FILA = sys.argv[3] if len(sys.argv) > 3 else "2"
PROJ = sys.argv[4] if len(sys.argv) > 4 else None


def configurar_imagem(page, modelo="Nano Banana 2", aspecto="16:9", dur=None, saidas="1x"):
    """Aba IMAGEM + modelo Nano Banana + aspecto + saidas. `dur` ignorado (so video).

    Assinatura compativel com fd.configurar_video pro monkey-patch funcionar.
    Tolerante: o popup do Flow flakeia e a config PERSISTE por projeto; cada
    passo falha em silencio pra nao abortar o lote.
    """
    _dispensar_avisos(page)   # anuncio do Flow pode estar na frente do seletor
    fd._seletor_modelo_btn(page).click()
    fd._pausa()
    # aba Imagem
    try:
        page.get_by_role("tab", name=re.compile(r"^Imagem$|^Image$", re.I)).first.click()
        fd._pausa(0.4, 0.8)
        print("  aba Imagem OK")
    except Exception as e:
        print(f"  !! aba Imagem falhou ({type(e).__name__}) — popup pode ja estar em Imagem")
    # modelo (o botao mostra o atual, ex 'Nano Banana 2 x2')
    try:
        page.get_by_role("button", name=re.compile(r"Nano|Banana|Imagen", re.I)).first.click()
        fd._pausa(0.3, 0.7)
        page.get_by_text(modelo, exact=True).first.click()
        fd._pausa(0.3, 0.7)
        print(f"  modelo {modelo} OK")
    except Exception as e:
        print(f"  (modelo: ja em {modelo}? {type(e).__name__})")
    # aspecto + saidas
    for nome in (aspecto, saidas):
        try:
            page.get_by_role("tab", name=nome, exact=True).first.click()
            fd._pausa(0.2, 0.5)
            print(f"  tab {nome} OK")
        except Exception:
            print(f"  (tab {nome} indisponivel — mantendo atual)")
    try:
        custo = page.get_by_text(re.compile(r"cr[ée]dito", re.I)).first.inner_text(timeout=3000)
        print(f"  >> config: {modelo} · {aspecto} · {saidas}  ({custo.strip()})")
    except Exception:
        print(f"  >> config: {modelo} · {aspecto} · {saidas}")
    # estado do botao (confirma que ficou em modo imagem)
    try:
        print(f"  botao modelo agora: {fd._seletor_modelo_btn(page).inner_text(timeout=3000)!r}")
    except Exception:
        pass
    page.keyboard.press("Escape")
    fd._pausa(0.3, 0.7)


def _dispensar_avisos(page):
    """Popups de ANUNCIO do Flow ('Tools Community Gallery is Live!' etc.)
    bloqueiam a UI e derrubam o driver (visto 31/07, ASH 04/08). Google solta
    um novo a cada feature — dispensa generica por texto de botao, best-effort,
    em qualquer idioma que eles usem."""
    try:
        page.keyboard.press("Escape")
        fd._pausa(0.2, 0.4)
    except Exception:
        pass
    for pat in (r"Comece j[aá]", r"Come[cç]ar", r"Got it", r"Entendi", r"Start",
                r"Dismiss", r"Continuar", r"Fechar", r"Close", r"^OK$"):
        try:
            b = page.get_by_role("button", name=re.compile(pat, re.I)).first
            if b.is_visible(timeout=700):
                b.click(timeout=2000)
                print(f"  aviso do Flow dispensado (botao ~/{pat}/)")
                fd._pausa(0.3, 0.6)
        except Exception:
            pass


_abrir_orig = fd._abrir_projeto


def _abrir_robusto(page, proj_id=None):
    """_abrir_projeto com auto-cura: falhou (popup na frente?) -> dispensa
    avisos e re-tenta UMA vez."""
    try:
        return _abrir_orig(page, proj_id)
    except Exception as e:
        print(f"  _abrir_projeto falhou ({type(e).__name__}) — dispensando avisos e re-tentando")
        _dispensar_avisos(page)
        return _abrir_orig(page, proj_id)


# --- troca a config de video pela de imagem e roda o driver do outro Claude ---
fd._abrir_projeto = _abrir_robusto
fd.configurar_video = configurar_imagem

argv = ["veo_driver", "--lote", LOTE, "--out", OUT,
        "--tipo", "imagem", "--fila", FILA, "--modelo", "Nano Banana 2"]
if PROJ:
    argv += ["--proj", PROJ]
sys.argv = argv
print(f"=== thumb_nano: lote={LOTE} out={OUT} fila={FILA} proj={PROJ or 'NOVO'} ===")
vd.main()
