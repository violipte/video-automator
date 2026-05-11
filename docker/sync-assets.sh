#!/bin/bash
# Sync assets de F:/Canal Dark/ pro Network Volume RunPod via pod temporário.
#
# Uso (no Git Bash do PC do Piter):
#   bash docker/sync-assets.sh <user@host> <ssh-port> [ssh-key-path]
#
# Exemplo:
#   bash docker/sync-assets.sh root@213.173.109.45 46406 ~/.ssh/id_ed25519

set -e

if [ -z "$1" ] || [ -z "$2" ]; then
    echo "Uso: bash $0 <user@host> <ssh-port> [ssh-key-path]"
    echo "Exemplo: bash $0 root@213.173.109.45 46406 ~/.ssh/id_ed25519"
    exit 1
fi

HOST="$1"
PORT="$2"
KEY="${3:-$HOME/.ssh/id_rsa}"

# Opcoes pra desabilitar prompts de host key (pod novo, host key sempre nova)
SSH_OPTS=(-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR)

echo "================================================================"
echo "Sync de assets pro Network Volume RunPod"
echo "Pod:  $HOST:$PORT"
echo "Key:  $KEY"
echo "================================================================"

if [ ! -f "$KEY" ]; then
    echo "ERRO: chave SSH nao encontrada em $KEY"
    echo "Liste suas chaves disponiveis: ls -la ~/.ssh/"
    exit 1
fi

# 1. Cria estrutura no volume
ssh "${SSH_OPTS[@]}" -i "$KEY" -p "$PORT" "$HOST" "mkdir -p /workspace/assets/Imagens/Chosen\ One /workspace/assets/Video /workspace/assets/Music"

echo ""
echo "[1/4] Sync Imagens/Chosen One/  (~9GB, demora ~20-40min)..."
scp "${SSH_OPTS[@]}" -i "$KEY" -P "$PORT" -r "F:/Canal Dark/Imagens/Chosen One/Estilo Chosen One/" \
    "$HOST:/workspace/assets/Imagens/Chosen One/" || true
scp "${SSH_OPTS[@]}" -i "$KEY" -P "$PORT" -r "F:/Canal Dark/Imagens/Chosen One/Starseeds/" \
    "$HOST:/workspace/assets/Imagens/Chosen One/" || true
scp "${SSH_OPTS[@]}" -i "$KEY" -P "$PORT" -r "F:/Canal Dark/Imagens/Chosen One/Frames/" \
    "$HOST:/workspace/assets/Imagens/Chosen One/" || true

echo ""
echo "[2/4] Sync Video/  (CTAs greenscreen + overlays, ~50MB)..."
scp "${SSH_OPTS[@]}" -i "$KEY" -P "$PORT" "F:/Canal Dark/Video/"*.mp4 "$HOST:/workspace/assets/Video/"

echo ""
echo "[3/4] Sync Music/  (trilhas sonoras, ~50MB)..."
scp "${SSH_OPTS[@]}" -i "$KEY" -P "$PORT" "F:/Canal Dark/Music/"*.mp3 "$HOST:/workspace/assets/Music/" 2>/dev/null || true
scp "${SSH_OPTS[@]}" -i "$KEY" -P "$PORT" "F:/Canal Dark/Music/"*.wav "$HOST:/workspace/assets/Music/" 2>/dev/null || true

echo ""
echo "[4/4] Verificando estrutura final..."
ssh "${SSH_OPTS[@]}" -i "$KEY" -p "$PORT" "$HOST" "
    echo '=== Assets sincronizados ==='
    du -sh /workspace/assets/Imagens 2>/dev/null
    du -sh /workspace/assets/Video 2>/dev/null
    du -sh /workspace/assets/Music 2>/dev/null
    echo ''
    echo '=== Subpastas Imagens ==='
    ls /workspace/assets/Imagens/Chosen\ One/Estilo\ Chosen\ One/ 2>/dev/null
    echo ''
    echo '=== Vídeos ==='
    ls -la /workspace/assets/Video/ 2>/dev/null
    echo ''
    echo '=== Musicas ==='
    ls -la /workspace/assets/Music/ 2>/dev/null
"

echo ""
echo "================================================================"
echo "Sync concluído. Network Volume tá pronto."
echo "Agora você pode parar o pod temporário (volume persiste)."
echo "================================================================"
