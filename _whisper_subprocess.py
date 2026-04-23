"""Subprocess worker: roda faster-whisper isolado e emite JSON por linha.

Uso: python _whisper_subprocess.py <mp3_path> <modelo> <device> [idioma]
Emite: linhas JSON {"type": "segment"|"done"|"error", ...}
Crash nativo (segfault) mata so este subprocess — o parent detecta via exit code.
"""
import sys
import os
import json
import ctypes
from pathlib import Path


def _load_nvidia_dlls():
    nvidia = Path(sys.executable).parent / "Lib" / "site-packages" / "nvidia"
    if not nvidia.exists():
        return
    for dll_dir in nvidia.glob("*/bin"):
        try:
            os.add_dll_directory(str(dll_dir))
        except Exception:
            pass
        os.environ["PATH"] = str(dll_dir) + os.pathsep + os.environ.get("PATH", "")
    cublas = nvidia / "cublas" / "bin" / "cublas64_12.dll"
    if cublas.exists():
        try:
            ctypes.CDLL(str(cublas))
        except Exception:
            pass


def _emit(obj):
    print(json.dumps(obj, ensure_ascii=False), flush=True)


def main():
    if len(sys.argv) < 4:
        _emit({"type": "error", "msg": "uso: <mp3> <modelo> <device> [idioma]"})
        sys.exit(2)

    mp3_path = sys.argv[1]
    modelo = sys.argv[2]
    device = sys.argv[3]
    idioma = sys.argv[4] if len(sys.argv) > 4 and sys.argv[4] else None

    _load_nvidia_dlls()

    try:
        from faster_whisper import WhisperModel
    except Exception as e:
        _emit({"type": "error", "msg": f"import faster_whisper: {e}"})
        sys.exit(3)

    compute = "float16" if device == "cuda" else "int8"

    try:
        model = WhisperModel(modelo, device=device, compute_type=compute)
    except Exception as e:
        _emit({"type": "error", "msg": f"load model ({device}/{compute}): {e}"})
        sys.exit(4)

    try:
        kwargs = {}
        if idioma:
            kwargs["language"] = idioma
        segments, info = model.transcribe(mp3_path, **kwargs)
        for seg in segments:
            _emit({"type": "segment", "start": seg.start, "end": seg.end, "text": seg.text})
        _emit({"type": "done"})
    except Exception as e:
        _emit({"type": "error", "msg": f"transcribe: {e}"})
        sys.exit(5)


if __name__ == "__main__":
    main()
