"""
Motor de geração de thumbnails estilo YouTube.
Suporta: texto multilinha com cores por linha, background box, outline grosso,
logo/watermark, zoom da imagem, sombreado inferior, presets por canal.
"""

import base64
import io
import json
from pathlib import Path

import httpx
from PIL import Image, ImageDraw, ImageFont, ImageFilter

BASE_DIR = Path(__file__).parent
TEMP_DIR = BASE_DIR / "temp"
TEMP_DIR.mkdir(exist_ok=True)

THUMB_W = 1280
THUMB_H = 720

estado_thumb = {"ativo": False, "jobs": [], "job_atual": -1}

DEFAULT_LINE_CONFIG = {
    "font": "Impact",
    "size": 90,
    "color": "#FFFFFF",
    "outline_color": "#000000",
    "outline_width": 6,
    "bg_box": False,
    "bg_box_color": "#000000",
    "bg_box_opacity": 200,
    "bg_box_padding": 15,
    "shadow": True,
    "shadow_offset": 4,
    "shadow_color": "#000000",
    "uppercase": True,
}

DEFAULT_TEXT_CONFIG = {
    # Config global (afeta todas as linhas como fallback)
    "font": "Impact",
    "size": 90,
    "colors": ["#FFFFFF", "#FFD700"],
    "outline_color": "#000000",
    "outline_width": 6,
    "bg_box": True,
    "bg_box_color": "#000000",
    "bg_box_opacity": 200,
    "bg_box_padding": 15,
    "shadow": True,
    "shadow_offset": 4,
    "shadow_color": "#000000",
    "position": "bottom",
    "position_x": 50,
    "position_y": 75,
    "align": "center",
    "margin": 40,
    "line_spacing": 8,
    "uppercase": True,
    "zoom": 1.0,
    "vignette": 0,
    "logo_path": "",
    "logo_position": "top-left",
    "logo_scale": 0.12,
    "logo_opacity": 200,
    # Config individual por linha (sobrescreve global)
    # "lines": [
    #   {"font": "Impact", "size": 100, "color": "#FFFFFF", "outline_width": 8, "bg_box": True, "bg_box_color": "#000000"},
    #   {"font": "Impact", "size": 110, "color": "#FFD700", "outline_width": 8, "bg_box": True, "bg_box_color": "#FFD700", "bg_box_opacity": 255}
    # ]
    "lines": [],
}


FONTS_DIR = BASE_DIR / "fonts"
FONTS_DIR.mkdir(exist_ok=True)

def _find_font(font_name: str, size: int) -> ImageFont.FreeTypeFont:
    """Encontra fonte no sistema Windows ou na pasta local fonts/."""
    import os
    fonts_dir = os.path.join(os.environ.get("WINDIR", "C:\\Windows"), "Fonts")
    user_fonts = os.path.join(os.environ.get("LOCALAPPDATA", ""), "Microsoft", "Windows", "Fonts")
    local_fonts = str(FONTS_DIR)
    all_dirs = [local_fonts, user_fonts, fonts_dir]

    # Mapeamento de nomes comuns
    font_map = {
        "impact": "impact.ttf",
        "arial black": "ariblk.ttf",
        "arial": "arial.ttf",
        "montserrat black": "Montserrat-Black.ttf",
        "montserrat": "Montserrat-Regular.ttf",
        "bebas neue": "BebasNeue-Regular.ttf",
        "oswald": "Oswald-Bold.ttf",
        "anton": "Anton-Regular.ttf",
        "open sans": "OpenSans.ttf",
        "inter": "Inter.ttf",
        "poppins": "Poppins-Bold.ttf",
        "poppins bold": "Poppins-Bold.ttf",
        "poppins black": "Poppins-Black.ttf",
        "zuume rough": "fontspring-demo-zuumerough-bold.otf",
        "zuume rough bold": "fontspring-demo-zuumerough-bold.otf",
        "zuume": "fontspring-demo-zuumerough-bold.otf",
    }

    name_lower = font_name.lower()

    # Tentar mapeamento direto — local > user > sistema
    if name_lower in font_map:
        for d in all_dirs:
            if not d or not os.path.exists(d):
                continue
            path = os.path.join(d, font_map[name_lower])
            if os.path.exists(path):
                return ImageFont.truetype(path, size)

    # Tentar nome direto
    for d in all_dirs:
        if not d or not os.path.exists(d):
            continue
        for ext in (".ttf", ".otf"):
            path = os.path.join(d, font_name + ext)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)
            path = os.path.join(d, font_name.replace(" ", "") + ext)
            if os.path.exists(path):
                return ImageFont.truetype(path, size)

    # Tentar buscar parcialmente em todas as pastas
    for d in all_dirs:
        if not d or not os.path.exists(d):
            continue
        for f in Path(d).iterdir():
            if name_lower.replace(" ", "") in f.stem.lower():
                try:
                    return ImageFont.truetype(str(f), size)
                except Exception:
                    continue

    # Fallback
    try:
        return ImageFont.truetype(os.path.join(fonts_dir, "impact.ttf"), size)
    except Exception:
        return ImageFont.load_default()


def gerar_thumbnail(imagem_fundo: str, texto: str, config: dict = None) -> Image.Image:
    """Gera thumbnail com texto sobreposto.

    Args:
        imagem_fundo: caminho da imagem de fundo
        texto: texto para sobrepor (multilinha com \\n)
        config: configurações de estilo

    Returns:
        PIL Image object (1280x720)
    """
    cfg = dict(DEFAULT_TEXT_CONFIG)
    if config:
        cfg.update(config)

    # Carregar e preparar imagem de fundo
    img = Image.open(imagem_fundo).convert("RGB")

    # Zoom: crop centralizado
    zoom = float(cfg.get("zoom", 1.0))
    if zoom > 1.0:
        iw, ih = img.size
        cw = int(iw / zoom)
        ch = int(ih / zoom)
        left = (iw - cw) // 2
        top = (ih - ch) // 2
        img = img.crop((left, top, left + cw, top + ch))

    # Resize para 1280x720 (cover)
    img = _resize_cover(img, THUMB_W, THUMB_H)

    # Sombreado inferior (vignette/gradient)
    vignette = int(cfg.get("vignette", 0))
    if vignette > 0:
        gradient = Image.new("RGBA", (THUMB_W, THUMB_H), (0, 0, 0, 0))
        draw_g = ImageDraw.Draw(gradient)
        # Gradiente de baixo para cima
        strength = int(vignette * 2.55)  # 0-100 → 0-255
        h_start = int(THUMB_H * 0.4)  # começa nos 40% de baixo
        for y in range(h_start, THUMB_H):
            alpha = int(strength * (y - h_start) / (THUMB_H - h_start))
            draw_g.line([(0, y), (THUMB_W, y)], fill=(0, 0, 0, alpha))
        img = img.convert("RGBA")
        img = Image.alpha_composite(img, gradient)
        img = img.convert("RGB")

    # Logo/watermark
    logo_path = cfg.get("logo_path", "")
    if logo_path and Path(logo_path).exists():
        _apply_logo(img, logo_path, cfg)

    # Texto
    if texto and texto.strip():
        _apply_text(img, texto, cfg)

    return img


def _resize_cover(img: Image.Image, w: int, h: int) -> Image.Image:
    """Resize mantendo aspect ratio e cortando para cobrir."""
    iw, ih = img.size
    ratio = max(w / iw, h / ih)
    new_w = int(iw * ratio)
    new_h = int(ih * ratio)
    img = img.resize((new_w, new_h), Image.LANCZOS)
    # Crop centralizado
    left = (new_w - w) // 2
    top = (new_h - h) // 2
    return img.crop((left, top, left + w, top + h))


def _apply_logo(img: Image.Image, logo_path: str, cfg: dict):
    """Aplica logo/watermark."""
    try:
        logo = Image.open(logo_path).convert("RGBA")
        scale = float(cfg.get("logo_scale", 0.12))
        logo_w = int(THUMB_W * scale)
        logo_h = int(logo.size[1] * (logo_w / logo.size[0]))
        logo = logo.resize((logo_w, logo_h), Image.LANCZOS)

        # Opacidade
        opacity = int(cfg.get("logo_opacity", 200))
        if opacity < 255:
            alpha = logo.split()[3]
            alpha = alpha.point(lambda p: min(p, opacity))
            logo.putalpha(alpha)

        # Posição
        pos = cfg.get("logo_position", "top-left")
        margin = 20
        if pos == "top-left":
            x, y = margin, margin
        elif pos == "top-right":
            x, y = THUMB_W - logo_w - margin, margin
        elif pos == "bottom-left":
            x, y = margin, THUMB_H - logo_h - margin
        elif pos == "bottom-right":
            x, y = THUMB_W - logo_w - margin, THUMB_H - logo_h - margin
        else:
            x, y = margin, margin

        img.paste(logo, (x, y), logo)
    except Exception:
        pass


def _get_line_config(cfg: dict, line_index: int) -> dict:
    """Retorna config mesclada: global + override da linha."""
    line_cfgs = cfg.get("lines", [])
    base = {
        "font": cfg.get("font", "Impact"),
        "size": cfg.get("size", 90),
        "color": cfg.get("colors", ["#FFFFFF"])[line_index % len(cfg.get("colors", ["#FFFFFF"]))] if cfg.get("colors") else "#FFFFFF",
        "outline_color": cfg.get("outline_color", "#000000"),
        "outline_width": cfg.get("outline_width", 6),
        "bg_box": cfg.get("bg_box", False),
        "bg_box_color": cfg.get("bg_box_color", "#000000"),
        "bg_box_opacity": cfg.get("bg_box_opacity", 200),
        "bg_box_padding": cfg.get("bg_box_padding", 15),
        "shadow": cfg.get("shadow", True),
        "shadow_offset": cfg.get("shadow_offset", 4),
        "shadow_color": cfg.get("shadow_color", "#000000"),
        "uppercase": cfg.get("uppercase", True),
    }
    if line_index < len(line_cfgs) and line_cfgs[line_index]:
        base.update(line_cfgs[line_index])
    return base


def _apply_text(img: Image.Image, texto: str, cfg: dict):
    """Aplica texto com config individual por linha."""
    raw_lines = texto.strip().split("\n")
    align = cfg.get("align", "center")
    line_spacing = int(cfg.get("line_spacing", 8))
    margin = int(cfg.get("margin", 40))
    pos_x = float(cfg.get("position_x", 50)) / 100

    # Pré-calcular cada linha com sua fonte individual
    line_data = []
    total_height = 0
    max_width = 0
    for i, raw in enumerate(raw_lines):
        lc = _get_line_config(cfg, i)
        text = raw.upper() if lc.get("uppercase", True) else raw
        font = _find_font(lc.get("font", "Impact"), int(lc.get("size", 90)))
        bbox = font.getbbox(text)
        w = bbox[2] - bbox[0]
        h = bbox[3] - bbox[1]
        line_data.append({
            "text": text, "font": font, "config": lc,
            "w": w, "h": h, "y_off": bbox[1],
        })
        total_height += h + line_spacing
        max_width = max(max_width, w)
    total_height -= line_spacing

    # Posição do bloco
    position = cfg.get("position", "bottom")
    pos_y_pct = float(cfg.get("position_y", 75)) / 100
    if position == "top":
        block_y = margin
    elif position == "center":
        block_y = (THUMB_H - total_height) // 2
    elif position == "bottom":
        block_y = THUMB_H - total_height - margin
    elif position == "custom":
        block_y = int(THUMB_H * pos_y_pct) - total_height // 2
    else:
        block_y = THUMB_H - total_height - margin

    # Background boxes (na camada RGBA)
    has_any_bg = any(ld["config"].get("bg_box", False) for ld in line_data)
    if has_any_bg:
        overlay = Image.new("RGBA", img.size, (0, 0, 0, 0))
        overlay_draw = ImageDraw.Draw(overlay)
        y = block_y
        for ld in line_data:
            lc = ld["config"]
            if lc.get("bg_box", False):
                if align == "center":
                    lx = int(THUMB_W * pos_x) - ld["w"] // 2
                elif align == "left":
                    lx = margin
                else:
                    lx = THUMB_W - ld["w"] - margin
                pad = int(lc.get("bg_box_padding", 15))
                r, g, b = _hex_to_rgb(lc.get("bg_box_color", "#000000"))
                opacity = int(lc.get("bg_box_opacity", 200))
                overlay_draw.rectangle(
                    [lx - pad, y - pad // 2, lx + ld["w"] + pad, y + ld["h"] + pad // 2],
                    fill=(r, g, b, opacity)
                )
            y += ld["h"] + line_spacing
        img_rgba = img.convert("RGBA")
        img_rgba = Image.alpha_composite(img_rgba, overlay)
        img.paste(img_rgba.convert("RGB"))

    # Desenhar texto
    draw = ImageDraw.Draw(img)
    y = block_y
    for ld in line_data:
        lc = ld["config"]
        if align == "center":
            lx = int(THUMB_W * pos_x) - ld["w"] // 2
        elif align == "left":
            lx = margin
        else:
            lx = THUMB_W - ld["w"] - margin

        # Shadow
        if lc.get("shadow", False):
            so = int(lc.get("shadow_offset", 4))
            sc = lc.get("shadow_color", "#000000")
            draw.text((lx + so, y + so - ld["y_off"]), ld["text"], font=ld["font"], fill=sc)

        # Outline
        ow = int(lc.get("outline_width", 6))
        oc = lc.get("outline_color", "#000000")
        if ow > 0:
            for ox in range(-ow, ow + 1):
                for oy in range(-ow, ow + 1):
                    if ox*ox + oy*oy <= ow*ow:
                        draw.text((lx + ox, y + oy - ld["y_off"]), ld["text"], font=ld["font"], fill=oc)

        # Texto
        draw.text((lx, y - ld["y_off"]), ld["text"], font=ld["font"], fill=lc.get("color", "#FFFFFF"))

        y += ld["h"] + line_spacing


def _hex_to_rgb(hex_color: str) -> tuple:
    hex_color = hex_color.lstrip("#")
    return tuple(int(hex_color[i:i+2], 16) for i in (0, 2, 4))


def gerar_thumbnail_base64(imagem_fundo: str, texto: str, config: dict = None) -> str:
    """Gera thumbnail e retorna como base64."""
    img = gerar_thumbnail(imagem_fundo, texto, config)
    buf = io.BytesIO()
    img.save(buf, format="JPEG", quality=92)
    return base64.b64encode(buf.getvalue()).decode()


def salvar_thumbnail(imagem_fundo: str, texto: str, output_path: str, config: dict = None) -> str:
    """Gera e salva thumbnail em disco."""
    img = gerar_thumbnail(imagem_fundo, texto, config)
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, format="JPEG", quality=95)
    return output_path


def extrair_thumb_youtube(url: str) -> dict:
    """Extrai URL da thumbnail de um vídeo do YouTube."""
    import re
    video_id = None
    patterns = [
        r'(?:v=|/v/|youtu\.be/)([a-zA-Z0-9_-]{11})',
        r'(?:embed/)([a-zA-Z0-9_-]{11})',
    ]
    for p in patterns:
        m = re.search(p, url)
        if m:
            video_id = m.group(1)
            break

    if not video_id:
        return {"ok": False, "erro": "ID do vídeo não encontrado na URL"}

    thumbs = {
        "maxres": f"https://img.youtube.com/vi/{video_id}/maxresdefault.jpg",
        "hq": f"https://img.youtube.com/vi/{video_id}/hqdefault.jpg",
        "sd": f"https://img.youtube.com/vi/{video_id}/sddefault.jpg",
    }

    return {"ok": True, "video_id": video_id, "thumbnails": thumbs}


def baixar_imagem(url: str, output_path: str) -> str:
    """Baixa imagem de URL."""
    resp = httpx.get(url, timeout=30.0, follow_redirects=True)
    resp.raise_for_status()
    Path(output_path).parent.mkdir(parents=True, exist_ok=True)
    with open(output_path, "wb") as f:
        f.write(resp.content)
    return output_path
