"""MOTOR VidMator (edição superdinâmica / Remotion-Director) como bridge do Automator.

Recebe um roteiro + uma narração JÁ PRODUZIDOS pelo Automator e devolve o MP4 renderizado
pelo pipeline do Director (montar -> resolver -> epoca -> ... -> preparar -> render-broll).
NÃO narra (o Automator já narrou na fase de narração) e NÃO usa o whisper/SRT do Automator
(o Director faz a própria legenda).

Chamado pelo render_worker quando `template.motor in ("vidmator","hibrido")`.

API:
    render_vidmator(template: dict, mp3_path: str, roteiro_path: str, out_path: str,
                    idioma: str = "en", progress_cb=None) -> bool
"""
import json
import os
import shutil
import subprocess
import time
from pathlib import Path

# --- interpretadores/paths do track experimental (independentes do venv do Automator) ---
TESTE = Path(r"F:/Canal Dark/Aplicativo de Edição/banco-videos/teste")
REMO = Path(r"F:/Canal Dark/Aplicativo de Edição/remotion")
CBPY = r"F:/Canal Dark/chatterbox-test/venv/Scripts/python.exe"          # venv Chatterbox (CUDA): whisper
PY314 = r"C:/Users/Piter Piter/AppData/Local/Programs/Python/Python314/python.exe"  # 3.14 (rembg/PIL): passes
NODE = "node"

# passes do Director (na ordem do producao.py), todos no PY314 / cwd=TESTE
# produto_cta por último (lê words.json; gated por preset.produto_cta.ativo — nichos sem produto skipam)
PASSES = ["montar_timeline", "resolver_cascata", "epoca", "detectar_mapas", "pessoas",
          "datas", "topicos", "trilha", "efeitos", "fontes", "imagens", "ilustrar", "apresentar",
          "produto_cta"]


def _run(interp, script, cwd, env, timeout=1800):
    r = subprocess.run([interp, script], cwd=str(cwd), env=env,
                       capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=timeout)
    tail = (r.stdout or "").strip().splitlines()[-1:] or [""]
    if r.returncode != 0:
        err = " | ".join((r.stderr or "").strip().splitlines()[-3:])[:240]
        raise RuntimeError(f"{script} falhou (exit {r.returncode}): {err}")
    return tail[0][:80]


_LOCK_DIR = TESTE / ".vidmator_lock"
_LOCK_STALE_SEC = 3 * 3600   # lock mais velho que 3h = processo morto -> quebra


def _adquirir_lock(timeout_sec=7200, poll=20):
    """MUTEX entre processos: o Director usa UM workspace compartilhado (roteiro_en.txt etc.) —
    2 jobs vidmator em paralelo se sobrescrevem (race pego no e2e 2026-07-02, 2 workers).
    os.mkdir é atômico no Windows -> serializa. Jobs 'simples' não passam por aqui (seguem paralelos)."""
    t0 = time.time()
    while True:
        try:
            os.mkdir(_LOCK_DIR)
            (_LOCK_DIR / "pid.txt").write_text(str(os.getpid()), encoding="utf-8")
            return True
        except FileExistsError:
            try:
                idade = time.time() - _LOCK_DIR.stat().st_mtime
                if idade > _LOCK_STALE_SEC:
                    shutil.rmtree(_LOCK_DIR, ignore_errors=True)
                    continue
            except Exception:
                pass
            if time.time() - t0 > timeout_sec:
                raise RuntimeError("vidmator lock: outro job segurou o workspace por >2h — abortando")
            time.sleep(poll)


def _soltar_lock():
    shutil.rmtree(_LOCK_DIR, ignore_errors=True)


def render_vidmator(template: dict, mp3_path: str, roteiro_path: str, out_path: str,
                    idioma: str = "en", progress_cb=None) -> bool:
    """Renderiza um job no motor VidMator. Retorna True em sucesso.
    SERIALIZADO por lock (workspace compartilhado) — ver _adquirir_lock."""
    def _prog(msg, pct):
        if progress_cb:
            try:
                progress_cb(msg, pct)
            except Exception:
                pass

    _prog("VidMator: aguardando workspace (lock)...", 5)
    _adquirir_lock()
    try:
        return _render_vidmator_locked(template, mp3_path, roteiro_path, out_path, idioma, _prog)
    finally:
        _soltar_lock()


def _render_vidmator_locked(template: dict, mp3_path: str, roteiro_path: str, out_path: str,
                            idioma: str, _prog) -> bool:

    nicho = (template.get("nicho") or "default").strip().lower()
    env = {**os.environ, "PYTHONUNBUFFERED": "1", "NICHO": nicho, "IDIOMA": idioma}
    if template.get("trilha_pasta"):
        env["MUSICA_PASTA"] = template["trilha_pasta"]
    if (template.get("fonte_imagens")):
        env["FONTE_IMAGENS"] = template["fonte_imagens"]       # api|local|hibrido (resolver lê)
    if template.get("pasta_imagens"):
        env["PASTA_IMAGENS_LOCAL"] = template["pasta_imagens"]

    # 1. ponte de entrada: o Director espera roteiro_en.txt + narracao_joanne.mp3 em TESTE/
    if not Path(mp3_path).exists():
        raise RuntimeError(f"narração não encontrada: {mp3_path}")
    shutil.copy2(mp3_path, TESTE / "narracao_joanne.mp3")
    if roteiro_path and Path(roteiro_path).exists():
        shutil.copy2(roteiro_path, TESTE / "roteiro_en.txt")
    else:
        raise RuntimeError(f"roteiro não encontrado: {roteiro_path}")
    _prog(f"VidMator: nicho={nicho}", 8)

    # 2. whisper (legenda própria do Director) — venv Chatterbox/CUDA
    _run(CBPY, "transcrever_words.py", TESTE, env, timeout=1200)
    _prog("VidMator: transcrição ok", 12)

    # 3. passes do Director
    for i, p in enumerate(PASSES):
        _run(PY314, f"{p}.py", TESTE, env)
        _prog(f"VidMator: {p}", 12 + int(38 * (i + 1) / len(PASSES)))

    # 4. preparar render (cwd=REMO)
    _run(PY314, "preparar_render.py", REMO, env)

    # 4.5 GATE DE NEGÓCIO — CTA do eBook é CORE BUSINESS (Piter 2026-07-02): se o preset do nicho
    # exige produto_cta, o timeline TEM que carregar a janela. Pega qualquer regressão (pass fora da
    # lista, preset errado, preparar não emitindo) ANTES de gastar GPU. Falha ruidosa, nunca silêncio.
    try:
        _presets_gate = json.load(open(TESTE / "presets.json", encoding="utf-8"))
        _cta_cfg = ((_presets_gate.get(nicho) or {}).get("produto_cta") or {})
        if _cta_cfg.get("ativo"):
            _tlr = json.load(open(REMO / "timeline_render.json", encoding="utf-8"))
            if not _tlr.get("produto_cta"):
                raise RuntimeError(f"GATE produto_cta: preset '{nicho}' exige CTA do eBook mas o timeline "
                                   f"não tem a janela — produção ABORTADA (CTA é core business)")
    except RuntimeError:
        raise
    except Exception as _ge:
        print(f"gate produto_cta: aviso (não-fatal na checagem): {str(_ge)[:80]}")
    _prog("VidMator: preparado, renderizando...", 55)

    # 5. render-broll em CHUNKS resilientes (retry/resume) -> REMO/out/<base>.mp4
    base = Path(out_path).stem
    renv = {**env, "RENDER_OUT": f"{base}.mp4", "RENDER_CHUNKS": "6", "RENDER_CONCURRENCY": "10"}
    saida_local = REMO / "out" / f"{base}.mp4"
    ok = False
    for att in range(1, 7):
        _prog(f"VidMator: render tentativa {att}", min(95, 55 + att * 6))
        r = subprocess.run([NODE, "render-broll.mjs"], cwd=str(REMO), env=renv,
                           capture_output=True, text=True, encoding="utf-8", errors="replace", timeout=5400)
        if r.returncode == 0 and saida_local.exists() and saida_local.stat().st_size > 100000:
            ok = True
            break
        time.sleep(5)
    if not ok:
        raise RuntimeError("render-broll falhou após retries")

    # 5.5 COLD-OPEN typewriter (padrão VidMator; preset.cold_open) — citação real escolhida do roteiro,
    # renderiza TypewriterQuote parametrizado e prependa via concat -c copy (params idênticos, lossless).
    try:
        _presets = json.load(open(TESTE / "presets.json", encoding="utf-8"))
        cold_open = bool((_presets.get(nicho) or {}).get("cold_open"))
    except Exception:
        cold_open = False
    if cold_open:
        try:
            _run(PY314, "coldopen_quote.py", TESTE, env, timeout=180)
            q = json.loads((TESTE / "coldopen.json").read_text(encoding="utf-8"))
            cenv = {**env, "COMP_PROPS": json.dumps({"quote": q["quote"], "author": q["author"], "cps": 20})}
            r = subprocess.run([NODE, "render-comp.mjs", "TypewriterQuote", f"{base}_coldopen.mp4"],
                               cwd=str(REMO), env=cenv, capture_output=True, text=True,
                               encoding="utf-8", errors="replace", timeout=900)
            cold = REMO / "out" / f"{base}_coldopen.mp4"
            if r.returncode == 0 and cold.exists():
                cc = REMO / "out" / f"_cc_{base}.txt"
                final = REMO / "out" / f"{base}_final.mp4"
                cc.write_text(f"file '{str(cold).replace(chr(92), '/')}'\nfile '{str(saida_local).replace(chr(92), '/')}'\n",
                              encoding="utf-8")
                rc = subprocess.run(["ffmpeg", "-y", "-f", "concat", "-safe", "0", "-i", str(cc),
                                     "-c", "copy", str(final)], capture_output=True, timeout=300)
                if rc.returncode == 0 and final.exists() and final.stat().st_size > saida_local.stat().st_size * 0.9:
                    saida_local.unlink(missing_ok=True)
                    final.rename(saida_local)
                    _prog("VidMator: cold-open ok", 97)
                cc.unlink(missing_ok=True)
            cold.unlink(missing_ok=True)
        except Exception as e:
            print(f"cold-open falhou (segue sem): {str(e)[:80]}")   # nunca derruba o job por causa do cold-open

    # 6. entrega o MP4 no caminho que o worker espera
    Path(out_path).parent.mkdir(parents=True, exist_ok=True)
    shutil.move(str(saida_local), out_path)
    _prog("VidMator: concluído", 100)
    return True
