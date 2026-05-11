"""
Thumbnail Generator com 3 modos de geracao:

  - 'prompt-mixer': pools (cena + character) + ai33.pro/Gemini  (DE, EN, EN2, EN3, ENO2, ENS, NARC, NPD, CON)
  - 'agente':       Claude CLI gera prompt em runtime + ai33.pro (ENO)
  - 'imagem_fixa':  PIL overlay de texto sobre imagem base       (CO1, CO2, CO3, CO4)

Uso direto:
    res = gerar_thumbnail(canal='EN', tema='...', titulo='...', thumb='RELAX...', output_dir=Path(...))
    # res = {'ok': True, 'path': '...', 'modo': 'prompt-mixer', 'provider_usado': 'ai33'}
"""
import json
import random
import re
import time
import urllib.error
from datetime import datetime
from pathlib import Path
from typing import Optional

BASE_DIR = Path(__file__).parent
THUMB_TEMPLATES_FILE = BASE_DIR / "thumb_templates.json"
THUMB_CO_CONFIG_FILE = BASE_DIR / "thumb_co_config.json"
AGENTS_DIR = BASE_DIR / "agents"
TEMP_DIR = BASE_DIR / "temp"

# ============= SPLIT TEXTO TOP/BOTTOM =============

def split_thumb_text(text: str) -> tuple[str, str]:
    """Quebra texto no espaco MAIS PROXIMO DO MEIO da string (balanceamento por chars).
    Ex: 'DO NOT ABANDON THAT PERSON' -> ('DO NOT ABANDON', 'THAT PERSON').
    Sem espaco -> retorna (text, '')."""
    text = (text or "").strip()
    if " " not in text:
        return text, ""
    mid = len(text) / 2
    best_i, best_delta = None, float("inf")
    for i, ch in enumerate(text):
        if ch == " ":
            delta = abs(i - mid)
            if delta < best_delta:
                best_delta = delta
                best_i = i
    return text[:best_i].strip(), text[best_i + 1:].strip()


# ============= CARREGADORES DE CONFIG =============

def _load_thumb_templates() -> list:
    if not THUMB_TEMPLATES_FILE.exists():
        return []
    try:
        return json.loads(THUMB_TEMPLATES_FILE.read_text(encoding="utf-8")).get("templates", [])
    except Exception as e:
        print(f"[thumb] erro lendo {THUMB_TEMPLATES_FILE}: {e}")
        return []


def get_template_canal(canal: str) -> Optional[dict]:
    """Retorna template do canal (modo prompt-mixer ou agente). None se nao achou."""
    for t in _load_thumb_templates():
        if t.get("canal") == canal:
            return t
    return None


def _load_co_config(canal: str) -> Optional[dict]:
    """Retorna config CO* mergeada (default + override do canal). None se canal nao eh CO*."""
    if not re.match(r'^CO\d', canal or ""):
        return None
    if not THUMB_CO_CONFIG_FILE.exists():
        return None
    try:
        cfg = json.loads(THUMB_CO_CONFIG_FILE.read_text(encoding="utf-8"))
    except Exception as e:
        print(f"[thumb] erro lendo {THUMB_CO_CONFIG_FILE}: {e}")
        return None
    default = dict(cfg.get("default", {}))
    canal_cfg = (cfg.get("canais", {}) or {}).get(canal, {})
    if canal_cfg.get("_override"):
        # override completo
        return canal_cfg
    # merge: default + canal-level overrides shallow
    merged = dict(default)
    for k, v in canal_cfg.items():
        if k.startswith("_"):
            continue
        if v not in (None, "", [], {}):
            merged[k] = v
    return merged


# ============= MODO A: PROMPT-MIXER (ai33.pro) =============

def _build_prompt_mixer(template: dict, thumb_top: str, thumb_bottom: str,
                        rng: random.Random | None = None) -> str:
    """Substitui placeholders no prompt_base usando pools aleatorios + textos top/bottom."""
    rng = rng or random.Random()
    base = template.get("prompt_base", "")
    if not base:
        raise RuntimeError(f"Template {template.get('canal')} sem prompt_base (PENDENTE)")
    pools = template.get("pools", {})
    cenas = pools.get("cena", [])
    chars = pools.get("character", [])
    cena_pick = rng.choice(cenas) if cenas else ""
    char_pick = rng.choice(chars) if chars else ""
    return (base
            .replace("[CENA]", cena_pick)
            .replace("[CHARACTER]", char_pick)
            .replace("[TEXTO DE CIMA]", thumb_top)
            .replace("[TEXTO DE BAIXO]", thumb_bottom))


# ============= MODO B: AGENTE LLM (ENO) =============

def _gerar_prompt_via_agente(canal: str, tema: str, titulo: str,
                             thumb_top: str, thumb_bottom: str) -> str:
    """Chama Claude CLI/API com agente do canal pra gerar prompt visual em runtime."""
    import scriptwriter

    agent_path = AGENTS_DIR / f"thumbnail-{canal.lower()}" / "CLAUDE.md"
    if not agent_path.exists():
        raise RuntimeError(f"Agente {agent_path} nao encontrado (PENDENTE pra canal {canal})")
    instrucoes = agent_path.read_text(encoding="utf-8")
    if "PITER PREENCHE" in instrucoes.upper() or "PENDENTE" in instrucoes.upper() and len(instrucoes) < 3000:
        # heuristica: se ainda eh placeholder, avisar
        print(f"[thumb] aviso: agente {canal} pode estar em branco/placeholder")

    system_msg = instrucoes + (
        "\n\n=== STRICT OUTPUT FORMAT (override) ===\n"
        "Return ONLY the visual prompt for ai33.pro/Gemini. Single string, "
        "no markdown fences, no preamble, no JSON, no commentary. "
        "The prompt should be a complete description of the YouTube thumbnail "
        "to generate, including scene, character, composition, lighting, "
        "text overlay positioning, and style."
    )
    user_msg = (
        f"Generate a YouTube thumbnail prompt for channel **{canal}**.\n\n"
        f"Tema: {tema}\n"
        f"Titulo: {titulo}\n"
        f"Thumb top text:    {thumb_top}\n"
        f"Thumb bottom text: {thumb_bottom}\n\n"
        f"Output ONLY the visual prompt:"
    )

    # Tenta Claude CLI -> Claude API
    try:
        return scriptwriter._chamar_claude_cli(system_msg, user_msg, "local-cli", "sonnet").strip()
    except Exception as e_cli:
        cred = next((c for c in scriptwriter.carregar_credenciais()
                     if c.get("provedor") == "claude" and c.get("status") == "ok"), None)
        if not cred:
            raise RuntimeError(f"Claude CLI falhou ({e_cli}) e sem credencial Claude API")
        return scriptwriter._chamar_claude(
            system_msg, user_msg, cred.get("api_key", ""), "claude-sonnet-4-6"
        ).strip()


# ============= MODO C: IMAGEM FIXA + PIL OVERLAY (CO*) =============

def _gerar_imagem_fixa(co_config: dict, thumb_top: str, thumb_bottom: str,
                       output_path: Path) -> Path:
    """Abre imagem base + escreve textos top/bottom com PIL. Sem chamar ai33."""
    from PIL import Image, ImageDraw, ImageFont

    img_path = co_config.get("imagem_base", "")
    if not img_path:
        raise RuntimeError("imagem_base nao configurada (PENDENTE - Piter preencher thumb_co_config.json)")
    img_path = Path(img_path)
    if not img_path.is_absolute():
        img_path = BASE_DIR / img_path
    if not img_path.exists():
        raise RuntimeError(f"imagem_base nao encontrada: {img_path}")

    fonte_path = co_config.get("fonte", "fonts/Anton-Regular.ttf")
    fonte_path = Path(fonte_path)
    if not fonte_path.is_absolute():
        fonte_path = BASE_DIR / fonte_path

    tam_fonte = int(co_config.get("tamanho_fonte", 110))
    cor_texto = co_config.get("cor_texto", "#FFFFFF")
    outline_w = int(co_config.get("outline_width", 6))
    outline_c = co_config.get("outline_color", "#000000")

    img = Image.open(img_path).convert("RGB")
    if img.size != (1280, 720):
        img = img.resize((1280, 720), Image.LANCZOS)

    draw = ImageDraw.Draw(img)
    try:
        fonte = ImageFont.truetype(str(fonte_path), tam_fonte)
    except Exception as e:
        raise RuntimeError(f"Falha carregando fonte {fonte_path}: {e}")

    def _draw_centered(text: str, x_pct: float, y_pct: float):
        if not text:
            return
        try:
            bbox = draw.textbbox((0, 0), text, font=fonte)
            w = bbox[2] - bbox[0]
            h = bbox[3] - bbox[1]
        except Exception:
            w, h = fonte.getsize(text) if hasattr(fonte, "getsize") else (200, 100)
        x = int(1280 * x_pct / 100 - w / 2)
        y = int(720 * y_pct / 100 - h / 2)
        # Outline (pseudo-stroke)
        if outline_w > 0:
            for dx in range(-outline_w, outline_w + 1):
                for dy in range(-outline_w, outline_w + 1):
                    if dx == 0 and dy == 0:
                        continue
                    draw.text((x + dx, y + dy), text, font=fonte, fill=outline_c)
        draw.text((x, y), text, font=fonte, fill=cor_texto)

    pos_top = co_config.get("posicao_top", {})
    pos_bot = co_config.get("posicao_bottom", {})
    _draw_centered(thumb_top, pos_top.get("x_pct", 50), pos_top.get("y_pct", 18))
    _draw_centered(thumb_bottom, pos_bot.get("x_pct", 50), pos_bot.get("y_pct", 78))

    output_path.parent.mkdir(parents=True, exist_ok=True)
    img.save(output_path, "JPEG", quality=92, optimize=True)
    if output_path.stat().st_size > 2 * 1024 * 1024:
        img.save(output_path, "JPEG", quality=80, optimize=True)
    return output_path


# ============= AI33.PRO (modos A e B) =============

def _carregar_ai33_key() -> str:
    import scriptwriter
    config = scriptwriter.carregar_config()
    return config.get("ai33_api_key", "")


def _gerar_imagem_ai33(prompt: str, model_id: str, aspect_ratio: str,
                       resolution: str, timeout_s: int = 300) -> str:
    """Chama ai33.pro/v1i, polling ate done, retorna imageUrl. Erros levantam excecao."""
    import urllib.request
    import urllib.parse

    key = _carregar_ai33_key()
    if not key:
        raise RuntimeError("config.ai33_api_key nao configurado")

    # Kickoff
    form_data = urllib.parse.urlencode({
        "prompt": prompt,
        "model_id": model_id,
        "generations_count": "1",
        "model_parameters": json.dumps({"aspect_ratio": aspect_ratio, "resolution": resolution}),
    }).encode("utf-8")

    req = urllib.request.Request(
        "https://api.ai33.pro/v1i/task/generate-image",
        data=form_data, method="POST",
        headers={
            "xi-api-key": key,
            "Content-Type": "application/x-www-form-urlencoded",
        },
    )
    try:
        with urllib.request.urlopen(req, timeout=60) as resp:
            body = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as e:
        erro_body = e.read().decode("utf-8", errors="replace")[:500]
        raise RuntimeError(f"ai33 HTTP {e.code}: {erro_body}")
    if not body.get("success"):
        raise RuntimeError(f"ai33 generate-image falhou: {body}")
    task_id = body["task_id"]

    # Polling
    deadline = time.time() + timeout_s
    while time.time() < deadline:
        time.sleep(4)
        req = urllib.request.Request(
            f"https://api.ai33.pro/v1/task/{task_id}",
            headers={"xi-api-key": key},
        )
        try:
            with urllib.request.urlopen(req, timeout=30) as resp:
                data = json.loads(resp.read().decode("utf-8"))
        except Exception as e:
            print(f"[thumb] poll error: {e}")
            continue
        status = data.get("status", "")
        if status == "done":
            try:
                return data["metadata"]["result_images"][0]["imageUrl"]
            except Exception as e:
                raise RuntimeError(f"ai33 done mas sem imageUrl: {data}")
        if status == "error":
            raise RuntimeError(f"ai33 task erro: {data.get('error_message', data)}")

    raise RuntimeError(f"ai33 task timeout apos {timeout_s}s")


def _baixar_e_resize(image_url: str, output_path: Path) -> Path:
    """Baixa imagem da URL + resize 1280x720 JPEG <=2MB."""
    import urllib.request
    from PIL import Image

    output_path.parent.mkdir(parents=True, exist_ok=True)
    raw_path = output_path.with_suffix(".raw.png")
    with urllib.request.urlopen(image_url, timeout=120) as resp:
        raw_path.write_bytes(resp.read())

    img = Image.open(raw_path).convert("RGB")
    img = img.resize((1280, 720), Image.LANCZOS)
    img.save(output_path, "JPEG", quality=92, optimize=True)
    raw_path.unlink(missing_ok=True)

    if output_path.stat().st_size > 2 * 1024 * 1024:
        img2 = Image.open(output_path).convert("RGB")
        img2.save(output_path, "JPEG", quality=80, optimize=True)
    return output_path


# ============= DISPATCHER =============

def gerar_thumbnail(canal: str, tema: str, titulo: str, thumb: str,
                    output_dir: Path | None = None,
                    force_regenerate: bool = False) -> dict:
    """Gera thumbnail pro canal escolhendo automaticamente o modo correto.

    Args:
        force_regenerate: se True, apaga cache existente e refaz.

    Returns: {ok: bool, path: str, modo: str, cached?: bool, provider_usado?: str, erro?: str}
    """
    if output_dir is None:
        output_dir = TEMP_DIR / "thumbs"
    output_dir = Path(output_dir)
    output_dir.mkdir(parents=True, exist_ok=True)
    output_path = output_dir / f"{canal}.jpg"

    # Cache local: se ja existe e nao eh regen forcada, retorna direto
    if output_path.exists() and output_path.stat().st_size > 1000:
        if force_regenerate:
            output_path.unlink(missing_ok=True)
        else:
            return {"ok": True, "path": str(output_path), "modo": "cached", "cached": True}

    # Detecta modo: CO* -> imagem_fixa, senao consulta thumb_templates.json
    if re.match(r'^CO\d', canal or ""):
        cfg = _load_co_config(canal)
        if not cfg:
            return {"ok": False, "erro": f"thumb_co_config nao encontrado pra {canal}", "modo": "imagem_fixa"}
        if not cfg.get("imagem_base"):
            return {"ok": False, "erro": f"PENDENTE: imagem_base nao configurada pra {canal}", "modo": "imagem_fixa"}
        try:
            top, bot = (split_thumb_text(thumb) if cfg.get("split_texto", True) else (thumb, ""))
            _gerar_imagem_fixa(cfg, top, bot, output_path)
            return {"ok": True, "path": str(output_path), "modo": "imagem_fixa"}
        except Exception as e:
            return {"ok": False, "erro": str(e), "modo": "imagem_fixa"}

    template = get_template_canal(canal)
    if not template:
        # ENO ou outro canal sem template -> tenta agente
        agent_path = AGENTS_DIR / f"thumbnail-{canal.lower()}" / "CLAUDE.md"
        if agent_path.exists():
            try:
                top, bot = split_thumb_text(thumb)
                prompt = _gerar_prompt_via_agente(canal, tema, titulo, top, bot)
                if not prompt:
                    return {"ok": False, "erro": "agente retornou prompt vazio", "modo": "agente"}
                image_url = _gerar_imagem_ai33(prompt, "gemini-3-pro-image-preview", "16:9", "2K")
                _baixar_e_resize(image_url, output_path)
                return {"ok": True, "path": str(output_path), "modo": "agente",
                        "provider_usado": "claude_cli + ai33"}
            except Exception as e:
                return {"ok": False, "erro": str(e), "modo": "agente"}
        return {"ok": False, "erro": f"Sem template ou agente pra canal '{canal}'", "modo": "?"}

    modo = template.get("modo", "prompt-mixer")
    if modo == "prompt-mixer":
        if not template.get("prompt_base"):
            return {"ok": False, "erro": f"PENDENTE: prompt_base vazio pra '{canal}'", "modo": modo}
        try:
            top, bot = (split_thumb_text(thumb) if template.get("split_texto", True) else (thumb, ""))
            prompt = _build_prompt_mixer(template, top, bot)
            image_url = _gerar_imagem_ai33(
                prompt,
                template.get("model_id", "gemini-3-pro-image-preview"),
                template.get("aspect_ratio", "16:9"),
                template.get("resolution", "2K"),
            )
            _baixar_e_resize(image_url, output_path)
            return {"ok": True, "path": str(output_path), "modo": modo, "provider_usado": "ai33"}
        except Exception as e:
            return {"ok": False, "erro": str(e), "modo": modo}

    if modo == "agente":
        try:
            top, bot = split_thumb_text(thumb)
            prompt = _gerar_prompt_via_agente(canal, tema, titulo, top, bot)
            image_url = _gerar_imagem_ai33(
                prompt,
                template.get("model_id", "gemini-3-pro-image-preview"),
                template.get("aspect_ratio", "16:9"),
                template.get("resolution", "2K"),
            )
            _baixar_e_resize(image_url, output_path)
            return {"ok": True, "path": str(output_path), "modo": modo,
                    "provider_usado": "claude_cli + ai33"}
        except Exception as e:
            return {"ok": False, "erro": str(e), "modo": modo}

    return {"ok": False, "erro": f"Modo desconhecido '{modo}' pra canal '{canal}'", "modo": modo}
