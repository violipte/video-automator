"""
Motor de geração de thumbnails.
Sobreposição de texto em imagem de fundo usando Pillow.
"""

import base64
import io
import json
import re
from pathlib import Path

from PIL import Image, ImageDraw, ImageFont

BASE_DIR = Path(__file__).parent
TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)

# Resolução padrão de thumbnail do YouTube
THUMB_W = 1280
THUMB_H = 720

# Estado global de geração
estado_thumb = {
    "ativo": False,
    "jobs": [],
    "job_atual": -1,
}

# Config padrão de texto
DEFAULT_TEXT_CONFIG = {
    "font": "Arial Black",
    "size": 72,
    "color": "#FFFFFF",
    "outline_color": "#000000",
    "outline_width": 4,
    "shadow": True,
    "shadow_offset": 4,
    "position": "center",  # top, center, bottom
    "margin": 60,
    "line_spacing": 10,
}


def _find_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    """Tenta encontrar a fonte no sistema."""
    import subprocess as sp

    # Tentar nomes comuns
    for ext in (".ttf", ".otf"):
        for prefix in [
            Path("C:/Windows/Fonts"),
            Path("C:/Users") / "Public" / "Fonts",
        ]:
            candidate = prefix / (font_name.replace(" ", "") + ext)
            if candidate.exists():
                return ImageFont.truetype(str(candidate), size)
            # Tentar com espaço
            candidate = prefix / (font_name + ext)
            if candidate.exists():
                return ImageFont.truetype(str(candidate), size)

    # Tentar via PowerShell para pegar o caminho exato
    try:
        r = sp.run(
            ['powershell', '-Command',
             f'(New-Object System.Drawing.Text.InstalledFontCollection).Families | '
             f'Where-Object {{ $_.Name -eq "{font_name}" }} | '
             f'ForEach-Object {{ $_.Name }}'],
            capture_output=True, text=True, timeout=5
        )
        if r.stdout.strip():
            # Fonte existe, tentar variações do arquivo
            for suffix in ["", " Bold", " Black", "bd", "bl"]:
                for ext in (".ttf", ".otf"):
                    fname = font_name.replace(" ", "") + suffix.replace(" ", "") + ext
                    candidate = Path("C:/Windows/Fonts") / fname
                    if candidate.exists():
                        return ImageFont.truetype(str(candidate), size)
    except Exception:
        pass

    # Fallback: deixar o Pillow resolver
    try:
        return ImageFont.truetype(font_name, size)
    except Exception:
        # Último recurso: Arial
        try:
            return ImageFont.truetype("C:/Windows/Fonts/arialbd.ttf", size)
        except Exception:
            return ImageFont.load_default()


def _wrap_text(draw: ImageDraw.Draw, text: str, font: ImageFont.FreeTypeFont, max_width: int) -> list:
    """Quebra texto em múltiplas linhas para caber na largura."""
    words = text.split()
    lines = []
    current = ""

    for word in words:
        test = (current + " " + word).strip()
        bbox = draw.textbbox((0, 0), test, font=font)
        w = bbox[2] - bbox[0]
        if w <= max_width and current:
            current = test
        elif not current:
            current = word
        else:
            lines.append(current)
            current = word

    if current:
        lines.append(current)

    return lines if lines else [text]


def gerar_thumbnail(imagem_fundo: str, texto: str, config: dict = None) -> bytes:
    """
    Gera thumbnail sobrepondo texto na imagem de fundo.

    Args:
        imagem_fundo: Caminho da imagem de fundo
        texto: Texto a ser sobreposto
        config: Configuração de estilo do texto

    Returns:
        Bytes da imagem JPEG gerada
    """
    cfg = {**DEFAULT_TEXT_CONFIG, **(config or {})}

    # Abrir e redimensionar imagem de fundo
    img = Image.open(imagem_fundo).convert("RGB")
    img = img.resize((THUMB_W, THUMB_H), Image.LANCZOS)

    draw = ImageDraw.Draw(img)

    # Fonte
    font = _find_font(cfg["font"], cfg["size"])

    # Quebrar texto em linhas
    margin = cfg["margin"]
    max_width = THUMB_W - (margin * 2)
    lines = _wrap_text(draw, texto, font, max_width)

    # Calcular altura total do bloco de texto
    line_heights = []
    for line in lines:
        bbox = draw.textbbox((0, 0), line, font=font)
        line_heights.append(bbox[3] - bbox[1])

    total_height = sum(line_heights) + cfg["line_spacing"] * (len(lines) - 1)

    # Posição Y baseada no alinhamento
    pos = cfg["position"]
    if pos == "top":
        y_start = margin
    elif pos == "bottom":
        y_start = THUMB_H - total_height - margin
    else:  # center
        y_start = (THUMB_H - total_height) // 2

    # Desenhar cada linha
    outline_w = cfg["outline_width"]
    shadow_offset = cfg.get("shadow_offset", 4) if cfg["shadow"] else 0

    y = y_start
    for i, line in enumerate(lines):
        bbox = draw.textbbox((0, 0), line, font=font)
        line_w = bbox[2] - bbox[0]
        x = (THUMB_W - line_w) // 2  # Centralizado horizontalmente

        # Sombra
        if shadow_offset:
            draw.text(
                (x + shadow_offset, y + shadow_offset),
                line, font=font,
                fill=(0, 0, 0, 180),
            )

        # Outline (contorno)
        if outline_w > 0:
            for dx in range(-outline_w, outline_w + 1):
                for dy in range(-outline_w, outline_w + 1):
                    if dx == 0 and dy == 0:
                        continue
                    draw.text((x + dx, y + dy), line, font=font, fill=cfg["outline_color"])

        # Texto principal
        draw.text((x, y), line, font=font, fill=cfg["color"])

        y += line_heights[i] + cfg["line_spacing"]

    # Salvar em buffer
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    buf.seek(0)
    return buf.read()


def gerar_thumbnail_base64(imagem_fundo: str, texto: str, config: dict = None) -> str:
    """Gera thumbnail e retorna como base64."""
    data = gerar_thumbnail(imagem_fundo, texto, config)
    return base64.b64encode(data).decode()


def salvar_thumbnail(imagem_fundo: str, texto: str, config: dict, output_path: str) -> str:
    """Gera e salva thumbnail no caminho especificado."""
    data = gerar_thumbnail(imagem_fundo, texto, config)
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.write(data)
    return str(p)


def extrair_thumb_youtube(url: str) -> dict:
    """
    Extrai URL da thumbnail de um vídeo do YouTube usando oEmbed API.

    Returns:
        Dict com thumbnail_url e info do vídeo
    """
    import httpx

    # Extrair video ID
    video_id = None
    patterns = [
        r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:embed/)([a-zA-Z0-9_-]{11})',
        r'(?:shorts/)([a-zA-Z0-9_-]{11})',
    ]
    for pat in patterns:
        m = re.search(pat, url)
        if m:
            video_id = m.group(1)
            break

    if not video_id:
        return {"error": "URL do YouTube inválida"}

    # Thumbnail direta (maxresdefault)
    thumb_urls = {
        "maxres": f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        "hq": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
        "sd": f"https://img.youtube.com/vi/{video_id}/sddefault.jpg",
    }

    # Tentar oEmbed para metadados
    try:
        resp = httpx.get(
            f"https://www.youtube.com/oembed?url=https://www.youtube.com/watch?v={video_id}&format=json",
            timeout=10.0,
        )
        resp.raise_for_status()
        info = resp.json()
        return {
            "video_id": video_id,
            "title": info.get("title", ""),
            "author": info.get("author_name", ""),
            "thumbnail_url": thumb_urls["maxres"],
            "thumbnail_hq": thumb_urls["hq"],
            "thumbnail_sd": thumb_urls["sd"],
        }
    except Exception:
        return {
            "video_id": video_id,
            "thumbnail_url": thumb_urls["maxres"],
            "thumbnail_hq": thumb_urls["hq"],
            "thumbnail_sd": thumb_urls["sd"],
        }


def baixar_imagem(url: str, output_path: str) -> str:
    """Baixa uma imagem de URL e salva localmente."""
    import httpx

    resp = httpx.get(url, timeout=15.0, follow_redirects=True)
    resp.raise_for_status()
    p = Path(output_path)
    p.parent.mkdir(parents=True, exist_ok=True)
    with open(p, "wb") as f:
        f.write(resp.content)
    return str(p)
