"""
Narrador TTS via Chatterbox Turbo (open source, self-hosted GPU local).

Roda em venv Python 3.12 isolado em F:/Canal Dark/chatterbox-test/ via subprocess
(o video-automator usa Python 3.13 que ainda nao tem wheel CUDA pra Chatterbox).

Configuracao no template:
{
  "narracao_voz": {
    "provider": "chatterbox",
    "voice_ref": "F:/Canal Dark/CapCut/CapCut Materials/Vozes/Bill EN.MP3",
    "exaggeration": 0.5,
    "cfg_weight": 0.5,
    "fallback": {
      "provider": "inworld",
      "voice_id": "default-...",
      "model": "inworld-tts-1.5-max"
    }
  }
}

Uso: o orchestrator chama `narrar_chatterbox(texto, voice_ref, ...)` e recebe
{"ok", "audio_local", "erro", ...} no mesmo padrao do narrator_inworld.

Pre-requisito: venv 3.12 com chatterbox-tts + torch CUDA em
F:/Canal Dark/chatterbox-test/venv/.
"""

import json
import subprocess
import time
from pathlib import Path

# Caminhos fixos do ambiente Chatterbox isolado
CHATTERBOX_DIR = Path("F:/Canal Dark/chatterbox-test")
# pythonw.exe (sem console) preferido a python.exe: evita janelas piscando.
# Como o runner usa torch multiprocessing (spawn), os workers filhos herdam
# sys.executable — se for pythonw, eles tambem nao abrem janela de console.
_cb_pyw = CHATTERBOX_DIR / "venv" / "Scripts" / "pythonw.exe"
_cb_py = CHATTERBOX_DIR / "venv" / "Scripts" / "python.exe"
CHATTERBOX_PYTHON = _cb_pyw if _cb_pyw.exists() else _cb_py
CHATTERBOX_RUNNER = CHATTERBOX_DIR / "chatterbox_runner.py"

# CREATE_NO_WINDOW so existe no Windows; no Linux getattr retorna 0 (sem efeito).
_CREATE_NO_WINDOW = getattr(subprocess, "CREATE_NO_WINDOW", 0)

# Timeout pra geracao completa: ~30k chars com 1.2x realtime + safety = ~45min
CHATTERBOX_TIMEOUT_SEG = 60 * 60  # 1h


def disponivel() -> bool:
    """Verifica se ambiente Chatterbox esta instalado e pronto."""
    return CHATTERBOX_PYTHON.exists() and CHATTERBOX_RUNNER.exists()


def narrar_chatterbox(
    texto: str,
    voice_ref: str,
    nome_saida: str,
    pasta: str = "",
    destino_final: str = "",
    exaggeration: float = 0.5,
    cfg_weight: float = 0.5,
    chunk_max_chars: int = 300,
    model_variant: str = "base",
    on_progress=None,
) -> dict:
    """Gera narracao completa via Chatterbox local (subprocess venv 3.12).

    Args:
        texto: roteiro completo
        voice_ref: path absoluto pro arquivo de voz de referencia (MP3/WAV 5-15s)
        nome_saida: nome base do arquivo (ex: "ENS" -> ENS.mp3)
        pasta: diretorio de saida (opcional, default video-automator/narracoes)
        destino_final: caminho completo do MP3 (override de pasta+nome)
        exaggeration: 0.0 monotone, 1.0 dramatico (default 0.5)
        cfg_weight: classifier-free guidance (default 0.5)
        chunk_max_chars: tamanho max do chunk pra geracao (default 300)

    Returns:
        {
            "ok": bool,
            "audio_local": str,           # path MP3 final
            "duracao_seg": float,
            "chunks": int,
            "tempo_geracao_seg": float,
            "erro": str,
        }
    """
    if not disponivel():
        return {
            "ok": False, "audio_local": "", "erro": f"venv Chatterbox nao encontrado em {CHATTERBOX_DIR}",
            "duracao_seg": 0, "chunks": 0, "tempo_geracao_seg": 0,
        }

    if not Path(voice_ref).exists():
        return {
            "ok": False, "audio_local": "", "erro": f"voice_ref nao existe: {voice_ref}",
            "duracao_seg": 0, "chunks": 0, "tempo_geracao_seg": 0,
        }

    # Calcula destino final
    if destino_final:
        out_path = Path(destino_final)
    else:
        out_dir = Path(pasta) if pasta else Path(__file__).parent / "narracoes"
        out_dir.mkdir(parents=True, exist_ok=True)
        out_path = out_dir / f"{nome_saida}.mp3"

    out_path.parent.mkdir(parents=True, exist_ok=True)

    # Metricas
    try:
        import tts_metrics
        tts_metrics.evento(tag=nome_saida, provider="chatterbox", event="start",
                          chars=len(texto))
    except Exception:
        tts_metrics = None

    t_start = time.time()
    payload = {
        "texto": texto,
        "voice_ref": str(Path(voice_ref).resolve()),
        "output": str(out_path.resolve()),
        "chunk_max_chars": chunk_max_chars,
        "exaggeration": exaggeration,
        "cfg_weight": cfg_weight,
        "nome_saida": nome_saida,
        "chunk_dir": str(out_path.parent.resolve()),
        "model_variant": (model_variant or "base").lower(),
    }

    print(f"[CHATTERBOX] {nome_saida}: {len(texto)} chars, voice={Path(voice_ref).name}, variant={payload['model_variant']}")

    try:
        # Subprocess: roda runner no venv 3.12 isolado.
        # Popen + thread leitora de stderr captura progresso por chunk em real-time.
        # Padrao do stderr: "[chatterbox] chunk N/M: ..." -> on_progress(N, M)
        import re as _re
        _chunk_re = _re.compile(r"^\[chatterbox\] chunk (\d+)/(\d+):")
        proc = subprocess.Popen(
            [str(CHATTERBOX_PYTHON), str(CHATTERBOX_RUNNER)],
            stdin=subprocess.PIPE,
            stdout=subprocess.PIPE,
            stderr=subprocess.PIPE,
            creationflags=_CREATE_NO_WINDOW,  # sem janela de console (Windows)
        )

        _stderr_lines = []
        _last_report_ts = [0.0]
        _last_progress_ts = [time.time()]  # WATCHDOG: ultimo timestamp de progresso (chunk ou linha [chatterbox])
        _watchdog_killed = [False]
        REPORT_THROTTLE_SEG = 5.0  # report progresso no max a cada 5s
        STALL_TIMEOUT_SEG = 12 * 60  # 12min sem progresso (era 8min). 2 kills observados em produção mesmo com heartbeats — gap real do Chatterbox pode passar 8min ocasionalmente.

        def _stderr_reader():
            try:
                for raw in iter(proc.stderr.readline, b""):
                    line = raw.decode("utf-8", errors="replace").rstrip()
                    if not line:
                        continue
                    _stderr_lines.append(line)
                    # Qualquer linha [chatterbox] conta como progresso (chunk, concat, load, etc)
                    if line.startswith("[chatterbox]"):
                        print(line)  # log no console
                        _last_progress_ts[0] = time.time()
                    # Parse chunk progress
                    m = _chunk_re.match(line)
                    if m and on_progress:
                        try:
                            n = int(m.group(1))
                            total = int(m.group(2))
                            now = time.time()
                            if (now - _last_report_ts[0]) >= REPORT_THROTTLE_SEG or n == total:
                                _last_report_ts[0] = now
                                try:
                                    on_progress(n, total)
                                except Exception:
                                    pass
                        except Exception:
                            pass
            except Exception:
                pass

        def _watchdog_loop():
            """Mata subprocess se passar STALL_TIMEOUT_SEG sem progresso."""
            while proc.poll() is None:  # enquanto subprocess vivo
                time.sleep(30)
                if proc.poll() is not None:
                    return
                elapsed = time.time() - _last_progress_ts[0]
                if elapsed > STALL_TIMEOUT_SEG:
                    print(f"[CHATTERBOX] WATCHDOG: sem progresso ha {elapsed:.0f}s (>{STALL_TIMEOUT_SEG}s) — matando subprocess travado")
                    _watchdog_killed[0] = True
                    try:
                        proc.kill()
                    except Exception:
                        pass
                    return

        import threading as _th
        _stderr_thread = _th.Thread(target=_stderr_reader, daemon=True)
        _stderr_thread.start()
        _watchdog_thread = _th.Thread(target=_watchdog_loop, daemon=True)
        _watchdog_thread.start()

        # Envia input + aguarda fim do processo
        try:
            stdout_bytes, _ = proc.communicate(
                input=json.dumps(payload).encode("utf-8"),
                timeout=CHATTERBOX_TIMEOUT_SEG,
            )
        except subprocess.TimeoutExpired:
            proc.kill()
            try: proc.wait(timeout=10)
            except Exception: pass
            raise
        _stderr_thread.join(timeout=5)
        # Se watchdog matou, sinaliza explicitamente
        if _watchdog_killed[0]:
            return {
                "ok": False, "audio_local": "",
                "erro": f"watchdog: travamento >{STALL_TIMEOUT_SEG}s sem progresso, subprocess morto",
                "duracao_seg": 0, "chunks": 0,
                "tempo_geracao_seg": time.time() - t_start,
            }
        stdout_text = stdout_bytes.decode("utf-8", errors="replace").strip()
        stderr_text = "\n".join(_stderr_lines)

        if proc.returncode != 0:
            erro = f"runner exit={proc.returncode}\nstderr tail: {stderr_text[-500:]}"
            print(f"[CHATTERBOX] {nome_saida}: ERRO - {erro[:200]}")
            if tts_metrics:
                try: tts_metrics.evento(tag=nome_saida, provider="chatterbox",
                                       event="error", erro=erro[:200])
                except Exception: pass
            return {
                "ok": False, "audio_local": "", "erro": erro,
                "duracao_seg": 0, "chunks": 0,
                "tempo_geracao_seg": time.time() - t_start,
            }

        # Parse JSON da ultima linha do stdout
        # (algumas warnings podem aparecer antes — pega so a ultima linha valida)
        result = None
        for line in reversed(stdout_text.splitlines()):
            line = line.strip()
            if line.startswith("{"):
                try:
                    result = json.loads(line)
                    break
                except json.JSONDecodeError:
                    continue

        if not result:
            return {
                "ok": False, "audio_local": "",
                "erro": f"runner nao retornou JSON valido. stdout: {stdout_text[-300:]}",
                "duracao_seg": 0, "chunks": 0,
                "tempo_geracao_seg": time.time() - t_start,
            }

        if not result.get("ok"):
            if tts_metrics:
                try: tts_metrics.evento(tag=nome_saida, provider="chatterbox",
                                       event="error", erro=result.get("erro","")[:200])
                except Exception: pass
            return result

        # =====================================================================
        # VALIDACAO ANTI-SILENCIO (post-mortem CO1 26/05/2026)
        # ---------------------------------------------------------------------
        # Bug observado: Chatterbox retornou ok=True mas o MP3 final ficou
        # majoritariamente em silencio (1.9MB / 1554s = 10 kbps efetivo,
        # esperado ~128 kbps). O DB marcou narração como "ok" e o vídeo foi
        # postado defeituoso. Para evitar reincidência: validar que o bitrate
        # efetivo (size_bytes * 8 / duracao / 1000) está em faixa plausível
        # antes de aceitar como sucesso. Se < 50 kbps, força fallback/retry.
        # =====================================================================
        try:
            audio_path = result.get("audio_local", "") or str(out_path)
            dur_check = float(result.get("duracao_seg", 0) or 0)
            if audio_path and Path(audio_path).exists() and dur_check > 10:
                size_bytes = Path(audio_path).stat().st_size
                effective_kbps = (size_bytes * 8) / (dur_check * 1000)
                if effective_kbps < 50:
                    erro_silencio = (
                        f"MP3 corrompido/silencioso detectado: "
                        f"{effective_kbps:.0f} kbps efetivo (esperado ~128). "
                        f"File: {size_bytes/1024/1024:.2f}MB / {dur_check:.0f}s. "
                        f"Forcando fallback/retry."
                    )
                    print(f"[CHATTERBOX] {nome_saida}: REJEITADO - {erro_silencio}")
                    # Renomeia o MP3 ruim pra .bad para inspecao posterior (nao deleta)
                    try:
                        bad_path = Path(audio_path).with_suffix(".bad.mp3")
                        Path(audio_path).rename(bad_path)
                    except Exception:
                        pass
                    if tts_metrics:
                        try:
                            tts_metrics.evento(
                                tag=nome_saida, provider="chatterbox",
                                event="error", erro=erro_silencio[:200],
                            )
                        except Exception:
                            pass
                    return {
                        "ok": False, "audio_local": "",
                        "erro": erro_silencio,
                        "duracao_seg": 0,
                        "chunks": result.get("chunks", 0),
                        "tempo_geracao_seg": time.time() - t_start,
                    }
        except Exception as _e_val:
            # Falha na validacao nao deve derrubar a narracao se o resto deu certo
            print(f"[CHATTERBOX] {nome_saida}: aviso - validacao anti-silencio falhou: {_e_val}")

        print(f"[CHATTERBOX] {nome_saida}: OK -> {result.get('audio_local')} "
              f"({result.get('duracao_seg',0):.0f}s audio, "
              f"{result.get('tempo_geracao_seg',0)/60:.1f}min geracao, "
              f"{result.get('chunks',0)} chunks)")

        if tts_metrics:
            try: tts_metrics.evento(tag=nome_saida, provider="chatterbox", event="ok",
                                   duracao_seg=result.get("duracao_seg",0),
                                   chunks=result.get("chunks",0))
            except Exception: pass

        return result

    except subprocess.TimeoutExpired:
        msg = f"timeout apos {CHATTERBOX_TIMEOUT_SEG}s"
        print(f"[CHATTERBOX] {nome_saida}: TIMEOUT - {msg}")
        if tts_metrics:
            try: tts_metrics.evento(tag=nome_saida, provider="chatterbox",
                                   event="timeout", erro=msg)
            except Exception: pass
        return {
            "ok": False, "audio_local": "", "erro": msg,
            "duracao_seg": 0, "chunks": 0,
            "tempo_geracao_seg": time.time() - t_start,
        }
    except Exception as e:
        msg = f"exception: {e}"
        print(f"[CHATTERBOX] {nome_saida}: EXCEPTION - {msg}")
        if tts_metrics:
            try: tts_metrics.evento(tag=nome_saida, provider="chatterbox",
                                   event="error", erro=msg[:200])
            except Exception: pass
        return {
            "ok": False, "audio_local": "", "erro": msg,
            "duracao_seg": 0, "chunks": 0,
            "tempo_geracao_seg": time.time() - t_start,
        }


if __name__ == "__main__":
    # Smoke test rapido
    import sys
    if len(sys.argv) < 3:
        print("Uso: python narrator_chatterbox.py <texto_ou_arquivo> <voice_ref> [output]")
        sys.exit(1)

    arg_texto = sys.argv[1]
    if Path(arg_texto).exists():
        txt = Path(arg_texto).read_text(encoding="utf-8")
    else:
        txt = arg_texto

    voice = sys.argv[2]
    output = sys.argv[3] if len(sys.argv) > 3 else "narracoes/test_chatterbox.mp3"

    r = narrar_chatterbox(
        texto=txt,
        voice_ref=voice,
        nome_saida="test",
        destino_final=output,
    )
    print(json.dumps(r, indent=2, ensure_ascii=False))
