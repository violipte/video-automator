"""
Integração com o Link Tracker para criação automática de links rastreáveis.
O tracker roda em servidor remoto (Contabo) com FastAPI + SQLite.
"""

import httpx
from datetime import datetime


def criar_link_rastreavel(
    tracker_url: str,
    tracker_auth: str,
    canal: str,
    data: str,
    url_destino: str,
    titulo: str = "",
    grupo: str = "",
) -> dict:
    """Cria um link rastreável no Link Tracker.

    Args:
        tracker_url: URL base do tracker (ex: https://track.dominio.com)
        tracker_auth: Credenciais "user:pass"
        canal: Tag do canal (ex: EN, CO2)
        data: Data no formato DD/MM/YYYY ou DD-MM
        url_destino: URL para onde o link redireciona
        titulo: Título do vídeo
        grupo: Grupo no tracker

    Returns:
        {"ok": True, "slug": "en-0904", "url": "https://track.dominio.com/en-0904"}
    """
    # Formatar slug: canal-DDMM
    data_limpa = data.replace("/", "").replace("-", "")
    if len(data_limpa) >= 4:
        ddmm = data_limpa[:4]  # DDMM
    else:
        ddmm = datetime.now().strftime("%d%m")

    slug = f"{canal.lower()}-{ddmm}"

    try:
        # Autenticar no tracker
        user, password = tracker_auth.split(":", 1) if ":" in tracker_auth else (tracker_auth, "")

        client = httpx.Client(timeout=15.0)

        # Login para pegar session cookie
        if password:
            login_resp = client.post(
                f"{tracker_url}/api/login",
                json={"user": user, "password": password},
            )
            # O cookie de sessão é salvo automaticamente no client

        # Criar link
        resp = client.post(
            f"{tracker_url}/api/links",
            json={
                "slug": slug,
                "url_destino": url_destino,
                "titulo": titulo or f"{canal} - {data}",
                "grupo": grupo or canal,
                "repassar_params": 1,
            },
        )

        if resp.status_code == 409:
            # Slug já existe — usar o existente
            return {
                "ok": True,
                "slug": slug,
                "url": f"{tracker_url}/{slug}",
                "existente": True,
            }

        resp.raise_for_status()

        return {
            "ok": True,
            "slug": slug,
            "url": f"{tracker_url}/{slug}",
            "existente": False,
        }

    except Exception as e:
        return {"ok": False, "erro": str(e)}


def montar_link_com_utm(
    tracker_url: str,
    slug: str,
    canal: str,
    data: str,
) -> str:
    """Monta URL completa com UTMs."""
    return (
        f"{tracker_url}/{slug}"
        f"?utm_source=youtube"
        f"&utm_medium={canal}"
        f"&utm_campaign={data.replace('/', '-')}"
    )


def montar_comentario(
    template: str,
    link: str,
    titulo: str = "",
    canal: str = "",
    data: str = "",
) -> str:
    """Substitui variáveis no template do comentário."""
    return (
        template
        .replace("{{link}}", link)
        .replace("{{titulo}}", titulo)
        .replace("{{canal}}", canal)
        .replace("{{data}}", data)
    )
