"""
Upload de videos renderizados pro Google Drive (conta pessoal).

Reaproveita OAuth do projeto drive-to-youtube (mesmo client_secret, mesmo
token, mesma conta pessoal pitermoreiraviolim@gmail.com, projeto GCP
`drive-sheets-personal`).

Uso:
    from drive_uploader import upload_video, test_connection, oauth_status

    # Upload um MP4 (cria subpasta YYYY-MM-DD dentro do folder_id se nao existir)
    file_id = upload_video(
        local_path="F:/.../Videos/CON_20260513_01.mp4",
        folder_id_raiz="1XYZ...",
        data_pasta="2026-05-13",  # YYYY-MM-DD
    )

    # Validar conexao + folder
    ok, msg = test_connection(folder_id="1XYZ...")
"""
import io
import json
import logging
from datetime import datetime
from pathlib import Path
from typing import Optional

from google.auth.transport.requests import Request
from google.oauth2.credentials import Credentials
from googleapiclient.discovery import build
from googleapiclient.errors import HttpError
from googleapiclient.http import MediaFileUpload

BASE_DIR = Path(__file__).parent
TOKEN_PATH = BASE_DIR / "tokens" / "personal_drive.json"
CLIENT_SECRET_PATH = BASE_DIR / "credentials" / "personal" / "client_secret.json"

# Escopo (mesmo do drive-to-youtube - 'drive' permite read+write+create)
SCOPES = ["https://www.googleapis.com/auth/drive"]

# Cache do service (criado on-demand, refresh automatico)
_service_cache = None
_creds_cache = None

# Cache de folder lookups (evita listar Drive toda vez)
# {(folder_id_raiz, nome_subfolder): folder_id_resultante}
_folder_cache: dict[tuple[str, str], str] = {}


def _load_creds() -> Optional[Credentials]:
    """Carrega credenciais OAuth do token salvo. Refresh automatico se expirou."""
    global _creds_cache
    if _creds_cache and _creds_cache.valid:
        return _creds_cache

    if not TOKEN_PATH.exists():
        return None

    try:
        creds = Credentials.from_authorized_user_file(str(TOKEN_PATH), SCOPES)
        # Refresh se expirou mas tem refresh_token
        if not creds.valid:
            if creds.expired and creds.refresh_token:
                creds.refresh(Request())
                # Persiste o token atualizado
                TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
            else:
                return None
        _creds_cache = creds
        return creds
    except Exception as e:
        logging.warning(f"[drive_uploader] _load_creds falhou: {e}")
        return None


def _get_service():
    """Retorna service do Drive API. Cache em modulo (1 instancia por processo)."""
    global _service_cache
    if _service_cache is not None:
        return _service_cache
    creds = _load_creds()
    if not creds:
        raise RuntimeError(
            f"OAuth nao configurado. Token nao existe ou invalido em {TOKEN_PATH}.\n"
            f"Rode: python auth_drive.py"
        )
    _service_cache = build("drive", "v3", credentials=creds, cache_discovery=False)
    return _service_cache


def oauth_status() -> dict:
    """Retorna status do OAuth pra exibir na UI.

    Returns:
        {"configured": bool, "valid": bool, "email": str|None, "erro": str|None}
    """
    if not CLIENT_SECRET_PATH.exists():
        return {
            "configured": False, "valid": False, "email": None,
            "erro": f"client_secret nao existe em {CLIENT_SECRET_PATH}"
        }
    if not TOKEN_PATH.exists():
        return {
            "configured": False, "valid": False, "email": None,
            "erro": f"Token nao gerado. Rode python auth_drive.py"
        }
    creds = _load_creds()
    if not creds:
        return {
            "configured": True, "valid": False, "email": None,
            "erro": "Token existe mas e invalido (refresh falhou). Re-rode auth_drive.py"
        }
    # Tenta pegar email via API
    try:
        svc = _get_service()
        about = svc.about().get(fields="user(emailAddress)").execute()
        email = about.get("user", {}).get("emailAddress", "?")
        return {"configured": True, "valid": True, "email": email, "erro": None}
    except Exception as e:
        return {
            "configured": True, "valid": True, "email": None,
            "erro": f"Token valido mas API falhou: {e}"
        }


def test_connection(folder_id: str) -> tuple[bool, str]:
    """Testa se consegue ler a pasta destino. Retorna (ok, mensagem).

    Args:
        folder_id: ID da pasta raiz onde os videos serao salvos

    Returns:
        (True, "OK: pasta 'Nome' encontrada, N arquivos visiveis")
        (False, "erro descricao")
    """
    try:
        svc = _get_service()
        # Pega metadados da pasta
        folder = svc.files().get(
            fileId=folder_id,
            fields="id,name,mimeType",
            supportsAllDrives=True
        ).execute()
        if folder.get("mimeType") != "application/vnd.google-apps.folder":
            return False, f"ID '{folder_id}' existe mas NAO eh uma pasta (mimeType={folder.get('mimeType')})"
        # Lista 1 arquivo pra confirmar permissao de leitura
        children = svc.files().list(
            q=f"'{folder_id}' in parents and trashed=false",
            pageSize=5,
            fields="files(id,name,mimeType)",
            includeItemsFromAllDrives=True,
            supportsAllDrives=True,
        ).execute()
        n_children = len(children.get("files", []))
        return True, f"OK: pasta '{folder.get('name')}' encontrada ({n_children} item(s) visivel(is))"
    except HttpError as e:
        if e.resp.status == 404:
            return False, f"Pasta nao encontrada: ID '{folder_id}' invalido ou sem permissao"
        elif e.resp.status == 403:
            return False, f"Sem permissao na pasta '{folder_id}' (verifique compartilhamento)"
        return False, f"HTTP Error {e.resp.status}: {str(e)[:200]}"
    except Exception as e:
        return False, f"Erro: {str(e)[:200]}"


def _ensure_subfolder(folder_id_raiz: str, nome: str) -> str:
    """Cria (ou retorna existente) subpasta dentro da raiz.

    Cache em memoria: 1 hit no Drive por (raiz, nome) na vida do processo.
    """
    cache_key = (folder_id_raiz, nome)
    if cache_key in _folder_cache:
        return _folder_cache[cache_key]

    svc = _get_service()
    # Buscar se ja existe
    q = (
        f"'{folder_id_raiz}' in parents and "
        f"name='{nome}' and "
        f"mimeType='application/vnd.google-apps.folder' and "
        f"trashed=false"
    )
    res = svc.files().list(
        q=q, pageSize=1, fields="files(id,name)",
        includeItemsFromAllDrives=True, supportsAllDrives=True,
    ).execute()
    files = res.get("files", [])
    if files:
        sub_id = files[0]["id"]
        _folder_cache[cache_key] = sub_id
        return sub_id

    # Criar nova subpasta
    metadata = {
        "name": nome,
        "mimeType": "application/vnd.google-apps.folder",
        "parents": [folder_id_raiz],
    }
    created = svc.files().create(
        body=metadata, fields="id,name", supportsAllDrives=True,
    ).execute()
    sub_id = created["id"]
    _folder_cache[cache_key] = sub_id
    return sub_id


def upload_video(
    local_path: str,
    folder_id_raiz: str,
    data_pasta: str,
    nome_destino: Optional[str] = None,
) -> dict:
    """Upload um MP4 pro Drive. Cria subpasta {data_pasta} se nao existir.

    Args:
        local_path: caminho local do arquivo a enviar
        folder_id_raiz: pasta raiz do canal no Drive
        data_pasta: nome da subpasta (ex: "2026-05-13")
        nome_destino: nome no Drive (default = basename do local_path)

    Returns:
        {
            "ok": bool,
            "file_id": str|None,
            "web_view_link": str|None,
            "erro": str|None,
            "tamanho_mb": float,
        }
    """
    local = Path(local_path)
    if not local.exists():
        return {"ok": False, "file_id": None, "web_view_link": None,
                "erro": f"Arquivo nao existe: {local_path}", "tamanho_mb": 0}

    tamanho_mb = local.stat().st_size / (1024 * 1024)
    nome = nome_destino or local.name

    try:
        # 1. Garantir subpasta de data
        subfolder_id = _ensure_subfolder(folder_id_raiz, data_pasta)

        # 2. Verificar se ja existe arquivo com mesmo nome (skip-existing)
        svc = _get_service()
        q = (
            f"'{subfolder_id}' in parents and "
            f"name='{nome}' and trashed=false"
        )
        existing = svc.files().list(
            q=q, pageSize=1, fields="files(id,name,size)",
            includeItemsFromAllDrives=True, supportsAllDrives=True,
        ).execute().get("files", [])

        if existing:
            ex = existing[0]
            ex_size_mb = int(ex.get("size", 0)) / (1024 * 1024)
            # Considera "existe" se size bate (margem 1MB)
            if abs(ex_size_mb - tamanho_mb) < 1.0:
                return {
                    "ok": True, "file_id": ex["id"],
                    "web_view_link": f"https://drive.google.com/file/d/{ex['id']}/view",
                    "erro": None, "tamanho_mb": ex_size_mb,
                    "skip": True,
                }
            # Size diferente: deletar antigo e re-upload
            try:
                svc.files().delete(fileId=ex["id"], supportsAllDrives=True).execute()
            except Exception:
                pass

        # 3. Upload (resumable, chunks 5MB)
        media = MediaFileUpload(
            str(local), mimetype="video/mp4",
            resumable=True, chunksize=5 * 1024 * 1024,
        )
        metadata = {"name": nome, "parents": [subfolder_id]}
        req = svc.files().create(
            body=metadata, media_body=media,
            fields="id,webViewLink", supportsAllDrives=True,
        )
        response = None
        while response is None:
            _status, response = req.next_chunk()
        return {
            "ok": True, "file_id": response["id"],
            "web_view_link": response.get("webViewLink"),
            "erro": None, "tamanho_mb": tamanho_mb,
            "skip": False,
        }
    except HttpError as e:
        return {"ok": False, "file_id": None, "web_view_link": None,
                "erro": f"HTTP {e.resp.status}: {str(e)[:300]}", "tamanho_mb": tamanho_mb}
    except Exception as e:
        return {"ok": False, "file_id": None, "web_view_link": None,
                "erro": str(e)[:300], "tamanho_mb": tamanho_mb}


if __name__ == "__main__":
    # Smoke test rapido
    import sys
    if len(sys.argv) < 2:
        print("Uso: python drive_uploader.py <comando> [args]")
        print("Comandos:")
        print("  status                    - verifica OAuth")
        print("  test <folder_id>          - testa pasta destino")
        print("  upload <file> <folder> <data>  - upload teste")
        sys.exit(1)

    cmd = sys.argv[1]
    if cmd == "status":
        print(json.dumps(oauth_status(), indent=2, default=str))
    elif cmd == "test":
        ok, msg = test_connection(sys.argv[2])
        print(f"{'OK' if ok else 'ERRO'}: {msg}")
    elif cmd == "upload":
        res = upload_video(sys.argv[2], sys.argv[3], sys.argv[4])
        print(json.dumps(res, indent=2, default=str))
    else:
        print(f"Comando desconhecido: {cmd}")
        sys.exit(1)
