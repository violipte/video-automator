"""
Setup OAuth do Google Drive (conta pessoal pitermoreiraviolim@gmail.com).

Roda 1x quando o token expirar ou nao existir.

Uso:
    python auth_drive.py

Flow:
    1. Imprime URL de autorizacao
    2. Voce abre no browser logado com pitermoreiraviolim@gmail.com
    3. Autoriza escopo 'drive'
    4. Copia codigo retornado e cola aqui
    5. Token salvo em tokens/personal_drive.json (gitignored)
"""
import json
from pathlib import Path

from google_auth_oauthlib.flow import InstalledAppFlow

BASE_DIR = Path(__file__).parent
CLIENT_SECRET = BASE_DIR / "credentials" / "personal" / "client_secret.json"
TOKEN_PATH = BASE_DIR / "tokens" / "personal_drive.json"

SCOPES = ["https://www.googleapis.com/auth/drive"]


def main():
    if not CLIENT_SECRET.exists():
        print(f"ERRO: client_secret nao encontrado em {CLIENT_SECRET}")
        return 1

    TOKEN_PATH.parent.mkdir(parents=True, exist_ok=True)

    print("=" * 70)
    print("OAuth Setup - Google Drive (conta pessoal)")
    print("=" * 70)
    print()
    print(f"Client secret: {CLIENT_SECRET}")
    print(f"Token destino: {TOKEN_PATH}")
    print(f"Escopo: drive (read + write + create)")
    print()

    # Usa flow local server (abre browser sozinho na porta dinamica)
    # IMPORTANTE: precisa estar logado em pitermoreiraviolim@gmail.com no browser
    flow = InstalledAppFlow.from_client_secrets_file(str(CLIENT_SECRET), SCOPES)
    print("Abrindo browser pra autorizacao...")
    print("Se nao abrir sozinho, copie a URL impressa abaixo.")
    print()

    creds = flow.run_local_server(
        port=0,
        open_browser=True,
        authorization_prompt_message="Autorize no browser. URL: {url}",
        success_message="Autorizado! Pode fechar esta janela.",
    )

    # Salva token
    TOKEN_PATH.write_text(creds.to_json(), encoding="utf-8")
    print()
    print(f"OK: Token salvo em {TOKEN_PATH}")
    print()
    print("Proximo: testar com:")
    print("  python drive_uploader.py status")
    return 0


if __name__ == "__main__":
    import sys
    sys.exit(main())
