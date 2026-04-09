"""
Motor de montagem de vídeo usando FFmpeg.
Usa cache de clips Ken Burns para velocidade máxima.
Passe 1: pré-renderiza cada imagem como clip via Pillow (suave, sem jitter).
Passe 2: concatena + overlay + legenda + áudio (rápido).
"""

import hashlib
import math
import os
import random
import re
import subprocess
from pathlib import Path

import cv2
import numpy as np

from transcriber import obter_duracao

TEMP_DIR = Path(__file__).parent / "temp"
TEMP_DIR.mkdir(exist_ok=True)
CACHE_DIR = Path(__file__).parent / "cache"
CACHE_DIR.mkdir(exist_ok=True)

ESTILOS_LEGENDA = {
    1: {"FontName": "Arial", "FontSize": 24, "PrimaryColour": "&H00FFFFFF", "OutlineColour": "&H00000000", "Outline": 2, "Shadow": 0, "Bold": 1, "Alignment": 2, "MarginV": 30},
    2: {"FontName": "Arial", "FontSize": 26, "PrimaryColour": "&H00FFFFFF", "OutlineColour": "&H00000000", "Outline": 3, "Shadow": 0, "Bold": 1, "Alignment": 2, "MarginV": 30},
    3: {"FontName": "Arial", "FontSize": 24, "PrimaryColour": "&H0000FFFF", "OutlineColour": "&H00000000", "Outline": 2, "Shadow": 0, "Bold": 1, "Alignment": 2, "MarginV": 30},
    4: {"FontName": "Arial", "FontSize": 20, "PrimaryColour": "&H00FFFFFF", "OutlineColour": "&H00000000", "Outline": 0, "Shadow": 0, "Bold": 0, "Alignment": 2, "MarginV": 30},
    5: {"FontName": "Arial", "FontSize": 18, "PrimaryColour": "&H0000FFFF", "OutlineColour": "&H00000000", "Outline": 0, "Shadow": 0, "Bold": 0, "Alignment": 2, "MarginV": 30},
}


def _set_low_priority(proc):
    """Define prioridade baixa no processo para não travar o sistema."""
    try:
        import psutil
        p = psutil.Process(proc.pid)
        p.nice(psutil.BELOW_NORMAL_PRIORITY_CLASS)
    except Exception:
        try:
            # Fallback sem psutil (Windows)
            import ctypes
            BELOW_NORMAL = 0x00004000
            handle = ctypes.windll.kernel32.OpenProcess(0x0200, False, proc.pid)
            if handle:
                ctypes.windll.kernel32.SetPriorityClass(handle, BELOW_NORMAL)
                ctypes.windll.kernel32.CloseHandle(handle)
        except Exception:
            pass


def _rodar_ffmpeg(cmd, callback_progresso=None, duracao_total=0):
    """Roda FFmpeg com prioridade baixa e parseia progresso."""
    proc = subprocess.Popen(
        cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
        text=True, encoding="utf-8", errors="replace"
    )
    _set_low_priority(proc)
    for linha in proc.stderr:
        match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})", linha)
        if match and callback_progresso and duracao_total > 0:
            h, m, s, cs = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
            tempo = h * 3600 + m * 60 + s + cs / 100
            callback_progresso(min(100.0, (tempo / duracao_total) * 100))
    proc.wait()
    return proc.returncode


def _cache_key(img_path, dur, w, h, fps, zoom_ratio, efeito="zoom_in"):
    """Gera chave de cache baseada nos parâmetros do clip."""
    dados = f"{img_path}|{dur}|{w}x{h}|{fps}|{zoom_ratio}|{efeito}"
    return hashlib.md5(dados.encode()).hexdigest()[:12]


class VideoEngine:
    def __init__(self, template: dict, narracao_path: str, output_path: str):
        self.template = template
        self.narracao_path = narracao_path
        self.output_path = output_path
        self.processo = None
        self.cancelado = False
        self.duracao_total = 0.0

    def montar(self, srt_path: str = None, callback_progresso=None, callback_etapa=None) -> str:
        """
        callback_progresso: fn(pct: float) - progresso 0-100
        callback_etapa: fn(etapa: str) - nome da etapa atual
        """
        self.cancelado = False
        t = self.template
        self.duracao_total = obter_duracao(self.narracao_path)

        tipo_fundo = t.get("tipo_fundo", "imagens")
        dur_por_item = t.get("duracao_por_imagem", 10)
        fps = t.get("fps", 30)
        res = t.get("resolucao", [1920, 1080])
        w, h = res[0], res[1]
        zoom = t.get("efeito_zoom", True)
        zoom_ratio = t.get("zoom_ratio", 1.04)
        video_loop = t.get("video_loop", True)

        # === PASSO 1: Preparar clips de fundo (com cache) ===
        arquivos_fundo = self._listar_arquivos_fundo()
        n_itens = max(1, int(self.duracao_total / dur_por_item) + 1)

        lista_expandida = []
        while len(lista_expandida) < n_itens:
            copia = list(arquivos_fundo)
            random.shuffle(copia)
            lista_expandida.extend(copia)
        lista_expandida = lista_expandida[:n_itens]

        duracoes = [dur_por_item] * n_itens
        excesso = dur_por_item * n_itens - self.duracao_total
        if excesso > 0:
            duracoes[-1] = max(1, duracoes[-1] - excesso)

        # Pré-renderizar clips com efeitos suaves (cacheados)
        # Alterna entre efeitos para variedade sem ser agressivo
        efeitos_pool = ["zoom_in", "zoom_out", "pan_left", "pan_right"]
        clips = []
        if tipo_fundo == "imagens":
            total_clips = len(lista_expandida)
            if callback_etapa:
                callback_etapa(f"Gerando clips (0/{total_clips})")
            for i, (img, dur) in enumerate(zip(lista_expandida, duracoes)):
                if self.cancelado:
                    raise RuntimeError("Produção cancelada pelo usuário.")
                efeito = efeitos_pool[i % len(efeitos_pool)] if zoom else "static"
                clip_path = self._gerar_clip_cached(img, dur, w, h, fps, zoom_ratio, efeito)
                clips.append(clip_path)
                if callback_progresso:
                    callback_progresso((i + 1) / total_clips * 60)
                if callback_etapa:
                    callback_etapa(f"Gerando clips ({i+1}/{total_clips})")
        else:
            clips = list(zip(lista_expandida, duracoes))

        if callback_progresso:
            callback_progresso(62)

        self._montar_final(clips, tipo_fundo, zoom, srt_path, w, h, fps, callback_progresso, callback_etapa)
        return self.output_path

    def _gerar_clip_cached(self, img_path, dur, w, h, fps, zoom_ratio, efeito="zoom_in"):
        """Gera clip com OpenCV (interpolação subpixel perfeita) + FFmpeg pipe.

        OpenCV gera cada frame com cv2.resize (INTER_LINEAR, ~2ms/frame).
        Coordenadas float = zero jitter. Ease-in-out cúbico = movimento suave.
        Frames enviados via pipe rawvideo pro FFmpeg encodar.
        ~1-2s por clip de 10s em 1080p.
        """
        key = _cache_key(img_path, dur, w, h, fps, zoom_ratio, efeito)
        cache_path = CACHE_DIR / f"{key}.mp4"
        if cache_path.exists():
            return str(cache_path)

        total_frames = int(dur * fps)

        # Carregar imagem com OpenCV
        img = cv2.imread(img_path, cv2.IMREAD_COLOR)
        if img is None:
            raise FileNotFoundError(f"Não conseguiu abrir imagem: {img_path}")

        # Escalar para tamanho de trabalho: cover zoom_ratio * output
        img_h_orig, img_w_orig = img.shape[:2]
        target_w = int(w * zoom_ratio)
        target_h = int(h * zoom_ratio)
        ratio = max(target_w / img_w_orig, target_h / img_h_orig)
        big_w = int(img_w_orig * ratio)
        big_h = int(img_h_orig * ratio)
        img_big = cv2.resize(img, (big_w, big_h), interpolation=cv2.INTER_LANCZOS4)

        # Pré-calcular crop boxes (float) para cada frame
        boxes = []
        for i in range(total_frames):
            t = i / max(total_frames - 1, 1)
            # Ease in-out cúbico: suaviza início e fim
            te = t * t * (3.0 - 2.0 * t)

            if efeito == "zoom_in":
                s = 1.0 + (zoom_ratio - 1.0) * te
                cw = big_w / s
                ch = big_h / s
                cx = (big_w - cw) / 2.0
                cy = (big_h - ch) / 2.0
            elif efeito == "zoom_out":
                s = zoom_ratio - (zoom_ratio - 1.0) * te
                cw = big_w / s
                ch = big_h / s
                cx = (big_w - cw) / 2.0
                cy = (big_h - ch) / 2.0
            elif efeito == "pan_left":
                cw = big_w / zoom_ratio
                ch = big_h / zoom_ratio
                cx = (big_w - cw) * (1.0 - te)
                cy = (big_h - ch) / 2.0
            elif efeito == "pan_right":
                cw = big_w / zoom_ratio
                ch = big_h / zoom_ratio
                cx = (big_w - cw) * te
                cy = (big_h - ch) / 2.0
            else:
                cw = big_w / zoom_ratio
                ch = big_h / zoom_ratio
                cx = (big_w - cw) / 2.0
                cy = (big_h - ch) / 2.0

            boxes.append((cx, cy, cw, ch))

        # Pipe frames pro FFmpeg
        for codec_args in [
            ["-c:v", "h264_nvenc", "-preset", "p4", "-cq", "24"],
            ["-c:v", "libx264", "-preset", "fast", "-crf", "22"],
        ]:
            cmd = [
                "ffmpeg", "-y", "-threads", "2",
                "-f", "rawvideo", "-pix_fmt", "bgr24",
                "-s", f"{w}x{h}", "-r", str(fps), "-i", "pipe:0",
                *codec_args, "-pix_fmt", "yuv420p",
                "-frames:v", str(total_frames), "-an", str(cache_path)
            ]
            try:
                proc = subprocess.Popen(
                    cmd, stdin=subprocess.PIPE,
                    stdout=subprocess.DEVNULL, stderr=subprocess.PIPE
                )
                write = proc.stdin.write
                for cx, cy, cw, ch in boxes:
                    # Affine transform: mapeia crop region → output size
                    # Isso faz interpolação subpixel automática (zero jitter)
                    src_pts = np.float32([
                        [cx, cy],
                        [cx + cw, cy],
                        [cx, cy + ch],
                    ])
                    dst_pts = np.float32([
                        [0, 0],
                        [w, 0],
                        [0, h],
                    ])
                    mat = cv2.getAffineTransform(src_pts, dst_pts)
                    frame = cv2.warpAffine(
                        img_big, mat, (w, h),
                        flags=cv2.INTER_LINEAR,
                        borderMode=cv2.BORDER_REFLECT_101,
                    )
                    write(frame.tobytes())

                proc.stdin.close()
                proc.wait()
                if proc.returncode == 0:
                    break
            except Exception:
                if proc and proc.poll() is None:
                    proc.kill()
                continue

        return str(cache_path)

    def _montar_final(self, clips, tipo_fundo, zoom, srt_path, w, h, fps, callback_progresso, callback_etapa=None):
        """Passo 2: concatena clips + overlay + ajustes + legenda + áudio."""
        t = self.template
        video_loop = t.get("video_loop", True)
        inputs = []
        filtros = []
        input_idx = 0

        # Inputs: clips de fundo (já renderizados)
        if tipo_fundo == "imagens":
            # Clips são paths de vídeo cacheado (com ou sem zoom)
            for clip in clips:
                inputs.extend(["-i", clip])
                input_idx += 1
            n_clips = len(clips)
        else:
            if video_loop:
                # LOOP: pré-concatenar vídeos shuffled em um único arquivo
                if callback_etapa:
                    callback_etapa("Pré-concatenando vídeos de fundo (loop)...")

                concat_list = TEMP_DIR / "bg_concat.txt"
                with open(concat_list, "w", encoding="utf-8") as f:
                    for arquivo, dur in clips:
                        vid_dur = obter_duracao(arquivo)
                        if vid_dur > 0 and vid_dur < dur:
                            loops = int(dur / vid_dur) + 1
                            for _ in range(loops):
                                f.write(f"file '{Path(arquivo).as_posix()}'\n")
                        else:
                            f.write(f"file '{Path(arquivo).as_posix()}'\n")

                bg_video = str(TEMP_DIR / "bg_concat.mp4")
                concat_cmd = [
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(concat_list),
                    "-vf", f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-an",
                    "-t", f"{self.duracao_total:.2f}",
                    bg_video
                ]
                ret = _rodar_ffmpeg(concat_cmd)
                if ret != 0:
                    raise RuntimeError("Falha ao pré-concatenar vídeos de fundo")

                inputs.extend(["-i", bg_video])
                input_idx += 1
                n_clips = 1
            else:
                # SEM LOOP: concatenar todos os vídeos disponíveis, sem repetição
                if callback_etapa:
                    callback_etapa("Pré-concatenando vídeos de fundo (sem loop)...")

                todos_videos = self._listar_arquivos_fundo()
                random.shuffle(todos_videos)
                concat_list = TEMP_DIR / "bg_concat.txt"
                with open(concat_list, "w", encoding="utf-8") as f:
                    for arquivo in todos_videos:
                        f.write(f"file '{Path(arquivo).as_posix()}'\n")

                bg_video = str(TEMP_DIR / "bg_concat.mp4")
                concat_cmd = [
                    "ffmpeg", "-y", "-f", "concat", "-safe", "0",
                    "-i", str(concat_list),
                    "-vf", f"fps={fps},scale={w}:{h}:force_original_aspect_ratio=decrease,pad={w}:{h}:(ow-iw)/2:(oh-ih)/2",
                    "-c:v", "libx264", "-preset", "ultrafast", "-crf", "18",
                    "-pix_fmt", "yuv420p", "-an",
                    bg_video
                ]
                ret = _rodar_ffmpeg(concat_cmd)
                if ret != 0:
                    raise RuntimeError("Falha ao pré-concatenar vídeos de fundo")

                inputs.extend(["-i", bg_video])
                input_idx += 1
                n_clips = 1

        # Narração
        idx_narracao = input_idx
        inputs.extend(["-i", self.narracao_path])
        input_idx += 1

        # Trilha sonora
        trilha = t.get("trilha_sonora", "")
        idx_trilha = None
        if trilha and Path(trilha).exists():
            idx_trilha = input_idx
            inputs.extend(["-stream_loop", "-1", "-i", trilha])
            input_idx += 1

        # Overlays
        overlays = t.get("overlays", [])
        idx_overlays_inicio = input_idx
        for ov in overlays:
            caminho = ov.get("caminho", "")
            if caminho and Path(caminho).exists():
                inputs.extend(["-stream_loop", "-1", "-i", caminho])
                input_idx += 1

        # Moldura (imagem estática como overlay permanente)
        moldura = t.get("moldura", {})
        idx_moldura = None
        moldura_path = moldura.get("arquivo", "")
        if moldura_path and Path(moldura_path).exists():
            idx_moldura = input_idx
            inputs.extend(["-loop", "1", "-t", f"{self.duracao_total:.2f}", "-i", moldura_path])
            input_idx += 1

        # CTA overlay (fundo verde)
        cta = t.get("cta", {})
        idx_cta = None
        cta_path = cta.get("arquivo", "")
        if cta_path and Path(cta_path).exists():
            idx_cta = input_idx
            inputs.extend(["-stream_loop", "-1", "-i", cta_path])
            input_idx += 1

        # === FILTROS ===

        # Concatenar clips
        if n_clips == 1:
            # Vídeo pré-concatenado — já está no fps/resolução correto, só trim
            filtros.append(
                f"[0:v]setpts=PTS-STARTPTS,trim=0:{self.duracao_total:.2f},setpts=PTS-STARTPTS[trimmed]"
            )
        elif tipo_fundo == "imagens":
            for i in range(n_clips):
                filtros.append(f"[{i}:v]setpts=PTS-STARTPTS[img{i}]")
            concat_inputs = "".join(f"[img{i}]" for i in range(n_clips))
            filtros.append(f"{concat_inputs}concat=n={n_clips}:v=1:a=0[base]")
            filtros.append(f"[base]trim=0:{self.duracao_total:.2f},setpts=PTS-STARTPTS[trimmed]")
        else:
            for i in range(n_clips):
                filtros.append(
                    f"[{i}:v]scale={w}:{h}:force_original_aspect_ratio=decrease,"
                    f"pad={w}:{h}:(ow-iw)/2:(oh-ih)/2,setpts=PTS-STARTPTS,"
                    f"fps={fps}[img{i}]"
                )
            concat_inputs = "".join(f"[img{i}]" for i in range(n_clips))
            filtros.append(f"{concat_inputs}concat=n={n_clips}:v=1:a=0[base]")
            filtros.append(f"[base]trim=0:{self.duracao_total:.2f},setpts=PTS-STARTPTS[trimmed]")
        ultimo_video = "trimmed"

        # Ajustes visuais (Lumetri-style)
        aj = t.get("ajustes", {})
        # Fallback para campos antigos
        brilho = aj.get("brilho", t.get("ajuste_brilho", 0))
        contraste = aj.get("contraste", t.get("ajuste_contraste", 1.0))
        exposicao = aj.get("exposicao", 0)
        saturacao = aj.get("saturacao", 1.0)
        realces = aj.get("realces", 0)
        sombras = aj.get("sombras", 0)
        brancos = aj.get("brancos", 0)
        pretos = aj.get("pretos", 0)
        temperatura = aj.get("temperatura", 0)
        tonalidade = aj.get("tonalidade", 0)
        vinheta = aj.get("vinheta", 0)

        # Randomização: variações sutis centradas nos valores base
        if aj.get("randomizar"):
            import random as rnd
            def _rv(base, variacao, minimo, maximo):
                """Randomiza valor com variação em torno do base, clamped."""
                return round(max(minimo, min(maximo, base + rnd.uniform(-variacao, variacao))), 3)

            exposicao   = _rv(exposicao,   0.02,  -0.08,  0.08)
            contraste   = _rv(contraste,   0.03,   0.92,  1.08)
            brilho      = _rv(brilho,      0.01,  -0.04,  0.04)
            saturacao   = _rv(saturacao,    0.04,   0.9,   1.1)
            realces     = _rv(realces,      0.04,  -0.15,  0.15)
            sombras     = _rv(sombras,      0.04,  -0.15,  0.15)
            brancos     = _rv(brancos,      0.03,  -0.1,   0.1)
            pretos      = _rv(pretos,       0.03,  -0.1,   0.1)
            temperatura = _rv(temperatura,  0.04,  -0.12,  0.12)
            tonalidade  = _rv(tonalidade,   0.03,  -0.08,  0.08)
            vinheta     = _rv(vinheta,      0.01,   0.0,   0.08)

        # Construir cadeia de filtros
        ajuste_filtros = []

        # EQ: exposição (gamma), brilho, contraste, saturação
        eq_parts = []
        gamma = max(0.1, 1.0 + exposicao)  # exposição como gamma
        if gamma != 1.0:
            eq_parts.append(f"gamma={gamma:.2f}")
        if brilho != 0:
            eq_parts.append(f"brightness={brilho}")
        if contraste != 1.0:
            eq_parts.append(f"contrast={contraste}")
        if saturacao != 1.0:
            eq_parts.append(f"saturation={saturacao}")
        if eq_parts:
            ajuste_filtros.append("eq=" + ":".join(eq_parts))

        # Curves: realces, sombras, brancos, pretos
        # Mapear para curvas de tons (curves filter)
        has_curves = any(v != 0 for v in [realces, sombras, brancos, pretos])
        if has_curves:
            # Construir pontos da curva de luminância
            # Pretos afetam 0-0.15, Sombras 0.15-0.4, Realces 0.6-0.85, Brancos 0.85-1.0
            p_pretos = max(0, min(0.3, 0 + pretos * 0.15))
            p_sombras = max(0.1, min(0.6, 0.25 + sombras * 0.15))
            p_realces = max(0.5, min(0.95, 0.75 + realces * 0.15))
            p_brancos = max(0.7, min(1.0, 1.0 + brancos * 0.15))
            curve = f"0/0 0.15/{p_pretos:.2f} 0.35/{p_sombras:.2f} 0.65/{p_realces:.2f} 0.85/{p_brancos:.2f} 1/1"
            ajuste_filtros.append(f"curves=m='{curve}'")

        # Temperatura e tonalidade via colorbalance
        if temperatura != 0 or tonalidade != 0:
            # Temperatura: positivo = quente (mais vermelho/amarelo), negativo = frio (mais azul)
            # Tonalidade: positivo = verde, negativo = magenta
            rs = temperatura * 0.3
            gs = tonalidade * 0.3
            bs = -temperatura * 0.3
            rm = temperatura * 0.15
            gm = tonalidade * 0.15
            bm = -temperatura * 0.15
            ajuste_filtros.append(
                f"colorbalance=rs={rs:.2f}:gs={gs:.2f}:bs={bs:.2f}:"
                f"rm={rm:.2f}:gm={gm:.2f}:bm={bm:.2f}"
            )

        # Vinheta (só aplica se acima de 0.05 para evitar vinheta fantasma da randomização)
        if vinheta > 0.05:
            # angle: PI/4 (~0.785) é neutro, menor = mais forte. Range útil: 0.4 a 0.75
            angle = max(0.4, 0.75 - vinheta * 0.35)
            ajuste_filtros.append(f"vignette=angle={angle:.2f}:mode=forward")

        if ajuste_filtros:
            chain = ",".join(ajuste_filtros)
            filtros.append(f"[{ultimo_video}]{chain}[adjusted]")
            ultimo_video = "adjusted"

        # Overlays
        overlays_validos = [ov for ov in overlays if ov.get("caminho") and Path(ov["caminho"]).exists()]
        use_gpu_overlay = overlays_validos and self._tem_cuda_filters()

        if overlays_validos and use_gpu_overlay:
            # GPU path: chromakey_cuda + overlay_cuda
            filtros.append(f"[{ultimo_video}]hwupload_cuda[gpu_base]")
            gpu_video = "gpu_base"
            for i, ov in enumerate(overlays_validos):
                idx_ov = idx_overlays_inicio + i
                opacidade = ov.get("opacidade", 0.3)
                filtros.append(
                    f"[{idx_ov}:v]scale={w}:{h},"
                    f"trim=0:{self.duracao_total:.2f},setpts=PTS-STARTPTS,"
                    f"hwupload_cuda,"
                    f"chromakey_cuda=0x000000:0.15:0.0[ov{i}]"
                )
                filtros.append(
                    f"[{gpu_video}][ov{i}]overlay_cuda=0:0[gpu_comp{i}]"
                )
                gpu_video = f"gpu_comp{i}"
            filtros.append(f"[{gpu_video}]hwdownload,format=yuv420p[from_gpu]")
            ultimo_video = "from_gpu"
        elif overlays_validos:
            # CPU fallback: colorkey + overlay
            for i, ov in enumerate(overlays_validos):
                idx_ov = idx_overlays_inicio + i
                opacidade = ov.get("opacidade", 0.3)
                filtros.append(
                    f"[{idx_ov}:v]scale={w}:{h},"
                    f"trim=0:{self.duracao_total:.2f},setpts=PTS-STARTPTS,"
                    f"colorkey=black:0.15:0.15,"
                    f"format=yuva420p,colorchannelmixer=aa={opacidade}"
                    f"[ov{i}]"
                )
                filtros.append(
                    f"[{ultimo_video}][ov{i}]overlay=0:0,format=yuv420p[comp{i}]"
                )
                ultimo_video = f"comp{i}"

        # Legenda via ASS (controle total de posição e estilo)
        if srt_path and Path(srt_path).exists():
            estilo_num = t.get("estilo_legenda", 1)
            estilo = self._obter_estilo_legenda(estilo_num)
            ass_path = self._gerar_ass(srt_path, estilo, w, h)
            ass_escapado = str(Path(ass_path).as_posix()).replace(":", "\\:")
            filtros.append(
                f"[{ultimo_video}]ass='{ass_escapado}'[subtitled]"
            )
            ultimo_video = "subtitled"

        # Moldura (imagem estática overlay - fica permanentemente por cima)
        if idx_moldura is not None and moldura:
            moldura_tipo = moldura.get("tipo", "chromakey")
            moldura_opac = moldura.get("opacidade", 1.0)

            if moldura_tipo == "alpha":
                filtros.append(
                    f"[{idx_moldura}:v]fps={fps},scale={w}:{h},format=yuva420p,"
                    f"setpts=PTS-STARTPTS,"
                    f"colorchannelmixer=aa={moldura_opac}[moldura]"
                )
            else:
                filtros.append(
                    f"[{idx_moldura}:v]fps={fps},scale={w}:{h},"
                    f"setpts=PTS-STARTPTS,"
                    f"chromakey=0x00FF00:0.2:0.1,format=yuva420p,"
                    f"colorchannelmixer=aa={moldura_opac}[moldura]"
                )

            filtros.append(
                f"[{ultimo_video}][moldura]overlay=0:0:shortest=1,format=yuv420p[with_moldura]"
            )
            ultimo_video = "with_moldura"

        # CTA overlay (chroma key verde → transparente, aparece em intervalos)
        if idx_cta is not None and cta:
            cta_duracao = cta.get("duracao", 8)
            cta_inicio = cta.get("inicio", 30)
            cta_intervalo = cta.get("intervalo", 300)  # segundos
            cta_pos = cta.get("posicao", "bottom-right")
            cta_escala = cta.get("escala", 0.25)  # 25% do tamanho do vídeo

            cta_w = int(w * cta_escala)
            cta_h = int(h * cta_escala)

            # Posição
            if cta_pos == "bottom-right":
                ox, oy = f"{w - cta_w - 40}", f"{h - cta_h - 80}"
            elif cta_pos == "bottom-center":
                ox, oy = f"{(w - cta_w) // 2}", f"{h - cta_h - 80}"
            elif cta_pos == "bottom-left":
                ox, oy = "40", f"{h - cta_h - 80}"
            elif cta_pos == "top-right":
                ox, oy = f"{w - cta_w - 40}", "40"
            elif cta_pos == "top-center":
                ox, oy = f"{(w - cta_w) // 2}", "40"
            elif cta_pos == "top-left":
                ox, oy = "40", "40"
            elif cta_pos == "center":
                ox, oy = f"{(w - cta_w) // 2}", f"{(h - cta_h) // 2}"
            else:
                ox, oy = f"{w - cta_w - 40}", f"{h - cta_h - 80}"

            # Calcular intervalos de aparição: enable='between(t,s1,e1)+between(t,s2,e2)+...'
            enables = []
            t_atual = cta_inicio
            while t_atual < self.duracao_total:
                t_fim = min(t_atual + cta_duracao, self.duracao_total)
                enables.append(f"between(t\\,{t_atual:.1f}\\,{t_fim:.1f})")
                t_atual += cta_intervalo
            enable_expr = "+".join(enables) if enables else "0"

            # Chromakey verde + escalar + overlay com enable
            filtros.append(
                f"[{idx_cta}:v]chromakey=0x00FF00:0.2:0.1,"
                f"scale={cta_w}:{cta_h}[cta_scaled]"
            )
            filtros.append(
                f"[{ultimo_video}][cta_scaled]overlay={ox}:{oy}:"
                f"enable='{enable_expr}'[with_cta]"
            )
            ultimo_video = "with_cta"

        # Áudio
        narracao_vol = t.get("narracao_volume", 1.0)
        filtros.append(f"[{idx_narracao}:a]volume={narracao_vol}[narr]")

        if idx_trilha is not None:
            trilha_vol = t.get("trilha_volume", 0.15)
            filtros.append(
                f"[{idx_trilha}:a]atrim=0:{self.duracao_total:.2f},volume={trilha_vol}[bg]"
            )
            filtros.append("[narr][bg]amix=inputs=2:duration=first:dropout_transition=2:weights=1 0.5:normalize=0[audio_out]")
            audio_label = "audio_out"
        else:
            audio_label = "narr"

        # === CONSTRUIR COMANDO ===
        filter_script = ";\n".join(filtros)
        filter_file = TEMP_DIR / "filter_complex.txt"
        filter_file.write_text(filter_script, encoding="utf-8")

        if callback_etapa:
            callback_etapa("Renderizando vídeo final...")
        cmd = ["ffmpeg", "-y", "-threads", "4"]
        cmd.extend(inputs)
        cmd.extend(["-filter_complex_script", str(filter_file)])
        cmd.extend([
            "-map", f"[{ultimo_video}]",
            "-map", f"[{audio_label}]",
            "-c:v", "h264_nvenc",
            "-preset", "p4",
            "-cq", "20",
            "-pix_fmt", "yuv420p",
            "-c:a", "aac",
            "-b:a", "192k",
            "-movflags", "+faststart",
            "-t", f"{self.duracao_total:.2f}",
            self.output_path
        ])

        Path(self.output_path).parent.mkdir(parents=True, exist_ok=True)

        # Executar com progresso (passo 2 = 65-100%)
        def cb_passo2(pct):
            if callback_progresso:
                callback_progresso(65 + pct * 0.35)

        self.processo = subprocess.Popen(
            cmd, stderr=subprocess.PIPE, stdout=subprocess.DEVNULL,
            text=True, encoding="utf-8", errors="replace"
        )
        _set_low_priority(self.processo)

        import time as _time
        _last_progress_time = _time.time()
        _stall_timeout = 600  # 10 min sem progresso = morto

        for linha in self.processo.stderr:
            if self.cancelado:
                self.processo.kill()
                raise RuntimeError("Produção cancelada pelo usuário.")
            match = re.search(r"time=(\d{2}):(\d{2}):(\d{2})\.(\d{2})", linha)
            if match and self.duracao_total > 0:
                hh, mm, ss, cs = int(match.group(1)), int(match.group(2)), int(match.group(3)), int(match.group(4))
                tempo = hh * 3600 + mm * 60 + ss + cs / 100
                cb_passo2(min(100.0, (tempo / self.duracao_total) * 100))
                _last_progress_time = _time.time()

            # Watchdog: se sem progresso por 10 min, matar
            if _time.time() - _last_progress_time > _stall_timeout:
                self.processo.kill()
                raise RuntimeError(f"FFmpeg travou (sem progresso por {_stall_timeout}s)")

        try:
            self.processo.wait(timeout=30)
        except subprocess.TimeoutExpired:
            self.processo.kill()
            self.processo.wait()
        if self.processo.returncode != 0:
            # Deletar arquivo incompleto/corrompido
            if Path(self.output_path).exists():
                Path(self.output_path).unlink(missing_ok=True)
            raise RuntimeError(f"FFmpeg falhou com código {self.processo.returncode}")

        # Limpar metadados do vídeo (remove encoder, software, timestamps)
        if Path(self.output_path).exists():
            clean_path = self.output_path.replace(".mp4", "_clean.mp4")
            clean_cmd = [
                "ffmpeg", "-y", "-i", self.output_path,
                "-map_metadata", "-1",
                "-c", "copy",
                "-fflags", "+bitexact",
                "-flags:v", "+bitexact",
                "-flags:a", "+bitexact",
                clean_path
            ]
            ret = subprocess.run(clean_cmd, capture_output=True, timeout=60)
            if ret.returncode == 0 and Path(clean_path).exists():
                Path(self.output_path).unlink()
                Path(clean_path).rename(self.output_path)

        if callback_progresso:
            callback_progresso(100.0)

    def cancelar(self):
        self.cancelado = True
        if self.processo and self.processo.poll() is None:
            self.processo.kill()

    @staticmethod
    def _tem_cuda_filters() -> bool:
        """GPU overlay desabilitado - colorkey CPU é estável e rápido (141fps)."""
        return False

    def _listar_arquivos_fundo(self) -> list:
        pasta = self.template.get("pasta_imagens", "")
        if not pasta or not Path(pasta).exists():
            raise FileNotFoundError(f"Pasta de imagens não encontrada: {pasta}")

        extensoes_img = {".jpg", ".jpeg", ".png", ".bmp", ".webp"}
        extensoes_vid = {".mp4", ".avi", ".mov", ".mkv", ".webm"}
        tipo = self.template.get("tipo_fundo", "imagens")
        extensoes = extensoes_img if tipo == "imagens" else extensoes_vid

        arquivos = [str(f) for f in Path(pasta).iterdir() if f.suffix.lower() in extensoes]
        arquivos.sort()

        if not arquivos:
            raise FileNotFoundError(f"Nenhum arquivo encontrado em: {pasta}")
        return arquivos

    def _obter_estilo_legenda(self, estilo_num: int) -> dict:
        """Retorna dict de estilo para ASS. Com PlayRes nativo, pixels são reais."""
        preset = dict(ESTILOS_LEGENDA.get(estilo_num, ESTILOS_LEGENDA[1]))
        config = self.template.get("legenda_config", {})
        if config.get("fonte"):
            preset["FontName"] = config["fonte"]
        if config.get("tamanho"):
            # FontSize no ASS com PlayResY nativo é em pixels reais.
            # Templates definem tamanho na escala do libass padrão (PlayResY≈288).
            # Escalar: tamanho * (resolução_real / 288)
            res = self.template.get("resolucao", [1920, 1080])
            scale = res[1] / 288.0
            preset["FontSize"] = int(config["tamanho"] * scale)
        if config.get("cor_primaria"):
            preset["PrimaryColour"] = config["cor_primaria"]
        if config.get("cor_outline_ass"):
            preset["OutlineColour"] = config["cor_outline_ass"]
        elif config.get("cor_outline"):
            preset["OutlineColour"] = config["cor_outline"]
        if config.get("outline_espessura") is not None:
            res = self.template.get("resolucao", [1920, 1080])
            scale = res[1] / 288.0
            preset["Outline"] = int(config["outline_espessura"] * scale)
        if config.get("sombra") is not None:
            res = self.template.get("resolucao", [1920, 1080])
            scale = res[1] / 288.0
            preset["Shadow"] = int(config["sombra"] * scale)
        if config.get("bold") is not None:
            preset["Bold"] = 1 if config["bold"] else 0

        res = self.template.get("resolucao", [1920, 1080])
        w, h = res[0], res[1]
        scale = h / 288.0

        posicao = config.get("posicao", "bottom")
        preset["MarginL"] = int(40 * scale)
        preset["MarginR"] = int(40 * scale)

        if posicao == "top":
            preset["Alignment"] = 8  # top-center
            preset["MarginV"] = 50
        elif posicao == "center":
            preset["Alignment"] = 5  # middle-center
            preset["MarginV"] = 0
        elif posicao == "custom":
            y_pct = config.get("posicao_y", 85)
            if y_pct <= 33:
                preset["Alignment"] = 8
                preset["MarginV"] = int(h * y_pct / 100)
            elif y_pct <= 66:
                preset["Alignment"] = 5
                preset["MarginV"] = 0
            else:
                preset["Alignment"] = 2
                preset["MarginV"] = int(h * (100 - y_pct) / 100)
            x_pct = config.get("posicao_x", 50)
            if x_pct < 50:
                preset["MarginR"] = int(w * (50 - x_pct) / 50)
            elif x_pct > 50:
                preset["MarginL"] = int(w * (x_pct - 50) / 50)
        else:
            preset["Alignment"] = 2  # bottom-center
            preset["MarginV"] = 50

        return preset

    def _gerar_ass(self, srt_path: str, estilo: dict, w: int, h: int) -> str:
        """Converte SRT para ASS com PlayRes nativo = posição pixel-perfect."""
        from subtitle_fixer import _parse_srt

        conteudo = Path(srt_path).read_text(encoding="utf-8")
        legendas = _parse_srt(conteudo)

        # Montar estilo ASS
        font = estilo.get("FontName", "Arial")
        size = estilo.get("FontSize", 24)
        pcol = estilo.get("PrimaryColour", "&H00FFFFFF")
        ocol = estilo.get("OutlineColour", "&H00000000")
        outline = estilo.get("Outline", 2)
        shadow = estilo.get("Shadow", 0)
        bold = estilo.get("Bold", 1)
        align = estilo.get("Alignment", 2)
        mv = estilo.get("MarginV", 50)
        ml = estilo.get("MarginL", 40)
        mr = estilo.get("MarginR", 40)

        # Cabeçalho ASS com PlayResX/Y = resolução real do vídeo
        header = (
            "[Script Info]\n"
            f"PlayResX: {w}\n"
            f"PlayResY: {h}\n"
            "ScriptType: v4.00+\n"
            "WrapStyle: 0\n"
            "ScaledBorderAndShadow: yes\n"
            "\n"
            "[V4+ Styles]\n"
            "Format: Name, Fontname, Fontsize, PrimaryColour, SecondaryColour, "
            "OutlineColour, BackColour, Bold, Italic, Underline, StrikeOut, "
            "ScaleX, ScaleY, Spacing, Angle, BorderStyle, Outline, Shadow, "
            "Alignment, MarginL, MarginR, MarginV, Encoding\n"
            f"Style: Default,{font},{size},{pcol},&H000000FF,{ocol},&H80000000,"
            f"{bold},0,0,0,100,100,0,0,1,{outline},{shadow},{align},{ml},{mr},{mv},1\n"
            "\n"
            "[Events]\n"
            "Format: Layer, Start, End, Style, Name, MarginL, MarginR, MarginV, Effect, Text\n"
        )

        # Converter timestamps SRT → ASS (HH:MM:SS.cc)
        def srt_to_ass_time(t):
            t = t.replace(",", ".")
            parts = t.split(":")
            hh = parts[0]
            mm = parts[1]
            ss_cs = parts[2]
            # SRT: HH:MM:SS,mmm → ASS: H:MM:SS.cc
            ss, ms = ss_cs.split(".")
            cs = ms[:2]  # centésimos
            return f"{int(hh)}:{mm}:{ss}.{cs}"

        lines = []
        for leg in legendas:
            start = srt_to_ass_time(leg.inicio)
            end = srt_to_ass_time(leg.fim)
            # Substituir quebras de linha SRT por \N (ASS)
            texto = leg.texto.replace("\n", "\\N")
            lines.append(f"Dialogue: 0,{start},{end},Default,,0,0,0,,{texto}")

        ass_content = header + "\n".join(lines) + "\n"
        ass_file = TEMP_DIR / f"{Path(srt_path).stem}.ass"
        ass_file.write_text(ass_content, encoding="utf-8-sig")
        return str(ass_file)
