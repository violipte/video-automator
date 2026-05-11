"""
Extracao automatica de metadados de videos do YouTube:
- oEmbed (gratis, sem key): titulo, autor, thumb_url
- OCR de thumb via OpenAI GPT-4o-mini vision: texto da thumb

Fallback gracioso: erros nao quebram o fluxo, retornam None/vazio.
"""
import json
import urllib.request
import urllib.parse
import urllib.error
from typing import Optional

import scriptwriter  # pra obter credencial OpenAI

OEMBED_URL = "https://www.youtube.com/oembed"
OPENAI_URL = "https://api.openai.com/v1/chat/completions"
OCR_MODEL = "gpt-4o-mini"  # vision-capable, ~$0.0015/imagem

OCR_PROMPT = (
    "Extract ONLY the main headline text shown on this YouTube thumbnail. "
    "Ignore: channel name, watermarks, small text, decorative elements, the YouTube logo. "
    "Return ONLY the headline text, in the same language and capitalization as shown, "
    "in a single line. If no text is visible, return an empty string. "
    "Do NOT add quotes, explanation, or commentary."
)


def oembed_get(url: str, timeout: int = 8) -> Optional[dict]:
    """Busca metadados via YouTube oEmbed. Retorna dict ou None se falhar.

    Returns: {"titulo": str, "autor": str, "thumb_url": str} or None
    """
    try:
        params = urllib.parse.urlencode({"url": url, "format": "json"})
        req = urllib.request.Request(f"{OEMBED_URL}?{params}", headers={"User-Agent": "video-automator/1.0"})
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            data = json.loads(resp.read().decode("utf-8"))
        return {
            "titulo": data.get("title", "").strip(),
            "autor": data.get("author_name", "").strip(),
            "thumb_url": data.get("thumbnail_url", "").strip(),
        }
    except Exception as e:
        print(f"[oembed] falha em {url}: {e}")
        return None


def _obter_openai_key() -> Optional[str]:
    """Pega primeira credencial OpenAI status=ok."""
    try:
        for c in scriptwriter.carregar_credenciais():
            if c.get("provedor") == "gpt" and c.get("status") == "ok":
                return c.get("api_key", "")
    except Exception:
        pass
    return None


def extrair_texto_thumb(
    thumb_url: str,
    video_id: str = "",
    api_key: Optional[str] = None,
    timeout: int = 30,
) -> Optional[str]:
    """OCR da thumb via GPT-4o-mini vision. Retorna texto extraido ou None.

    Se thumb_url da oembed falhar (alguns videos so tem 'maxres' faltando), usa fallback
    https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg -> hqdefault.jpg
    """
    if api_key is None:
        api_key = _obter_openai_key()
    if not api_key:
        print("[ocr] sem credencial OpenAI cadastrada")
        return None

    candidatos = []
    if thumb_url:
        candidatos.append(thumb_url)
    if video_id:
        candidatos.append(f"https://i.ytimg.com/vi/{video_id}/maxresdefault.jpg")
        candidatos.append(f"https://i.ytimg.com/vi/{video_id}/hqdefault.jpg")

    for img_url in candidatos:
        try:
            body = {
                "model": OCR_MODEL,
                "max_tokens": 200,
                "messages": [{
                    "role": "user",
                    "content": [
                        {"type": "text", "text": OCR_PROMPT},
                        {"type": "image_url", "image_url": {"url": img_url, "detail": "low"}},
                    ],
                }],
            }
            data = json.dumps(body).encode("utf-8")
            req = urllib.request.Request(
                OPENAI_URL, data=data, method="POST",
                headers={
                    "Authorization": f"Bearer {api_key}",
                    "Content-Type": "application/json",
                },
            )
            with urllib.request.urlopen(req, timeout=timeout) as resp:
                resposta = json.loads(resp.read().decode("utf-8"))
            texto = (resposta.get("choices", [{}])[0]
                            .get("message", {})
                            .get("content", "") or "").strip()
            # Remove aspas de envolvimento se vier
            if texto and texto[0] in '"\'' and texto[-1] in '"\'':
                texto = texto[1:-1].strip()
            return texto
        except urllib.error.HTTPError as e:
            erro_body = ""
            try:
                erro_body = e.read().decode("utf-8", errors="replace")[:200]
            except Exception:
                pass
            print(f"[ocr] HTTPError {e.code} em {img_url}: {erro_body}")
            # 404 da thumb URL -> tenta proxima. Outros erros -> aborta.
            if e.code != 404:
                return None
        except Exception as e:
            print(f"[ocr] erro em {img_url}: {e}")
            return None

    return None


def enriquecer_video(link: str, video_id: str = "") -> dict:
    """Faz oembed + ocr e retorna {'titulo', 'texto_thumb', 'thumb_url'}.
    Erros sao silenciosos: campos ficam vazios.
    """
    result = {"titulo": "", "texto_thumb": "", "thumb_url": ""}
    meta = oembed_get(link)
    if meta:
        result["titulo"] = meta.get("titulo", "")
        result["thumb_url"] = meta.get("thumb_url", "")

    texto = extrair_texto_thumb(result["thumb_url"], video_id=video_id)
    if texto:
        result["texto_thumb"] = texto

    return result
