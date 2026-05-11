#!/bin/bash
# Auto-download dos vídeos renderizados do Network Volume (RunPod) → PC do Piter.
#
# Roda em loop, a cada DOWNLOAD_INTERVAL segundos:
#   1. Pega o primeiro pod RUNNING via RunPod API
#   2. Faz rsync incremental de /workspace/exports/ → F:/Canal Dark/Automator Exports/
#   3. Sleep e repete
#
# rsync incremental = só baixa o que mudou/é novo. Pula arquivos já presentes.
# Se nenhum pod estiver ativo, espera o próximo ciclo.
#
# Uso (Git Bash do PC):
#   bash docker/auto-download.sh
#
# Pra parar: Ctrl+C ou kill o processo

set -u

API_KEY="${RUNPOD_API_KEY:?RUNPOD_API_KEY env var obrigatoria (sem fallback hardcoded por seguranca)}"
DEST="${DEST:-F:/Canal Dark/Automator Exports}"
KEY="${SSH_KEY:-$HOME/.ssh/id_rsa}"
DOWNLOAD_INTERVAL="${DOWNLOAD_INTERVAL:-60}"
SSH_OPTS="-o StrictHostKeyChecking=no -o UserKnownHostsFile=/dev/null -o LogLevel=ERROR -o ConnectTimeout=15"

mkdir -p "$DEST"

echo "================================================================"
echo "Auto-download dos exports RunPod → PC"
echo "  Destino:  $DEST"
echo "  SSH key:  $KEY"
echo "  Interval: ${DOWNLOAD_INTERVAL}s"
echo "================================================================"

iteracao=0
while true; do
    iteracao=$((iteracao + 1))
    ts=$(date '+%H:%M:%S')

    # Pega primeiro pod RUNNING (com publicIp + portMapping pra 22)
    POD_INFO=$(curl -sf -m 15 -H "Authorization: Bearer $API_KEY" \
        "https://rest.runpod.io/v1/pods" 2>/dev/null \
        | python3 -c "
import json, sys
d = json.load(sys.stdin)
pods = d if isinstance(d, list) else d.get('data', [])
for p in pods:
    if p.get('desiredStatus') != 'RUNNING': continue
    ip = p.get('publicIp')
    port = (p.get('portMappings') or {}).get('22')
    if ip and port:
        print(f'{ip} {port} {p.get(\"name\",\"?\")}')
        break
")

    if [ -z "$POD_INFO" ]; then
        echo "[$ts #${iteracao}] Nenhum pod RUNNING acessível. Aguardando ${DOWNLOAD_INTERVAL}s..."
        sleep "$DOWNLOAD_INTERVAL"
        continue
    fi

    IP=$(echo "$POD_INFO" | awk '{print $1}')
    PORT=$(echo "$POD_INFO" | awk '{print $2}')
    NAME=$(echo "$POD_INFO" | awk '{print $3}')

    echo "[$ts #${iteracao}] Sync via $NAME ($IP:$PORT)..."

    # rsync incremental: --update (skip se mtime maior no destino), --partial (resume),
    # --inplace (não cria temp), --info=stats2 (resumo final)
    rsync -avz --update --partial \
        -e "ssh $SSH_OPTS -i $KEY -p $PORT" \
        "root@${IP}:/workspace/exports/" \
        "$DEST/" 2>&1 | tail -3

    echo "[$ts #${iteracao}] OK. Próximo em ${DOWNLOAD_INTERVAL}s."
    sleep "$DOWNLOAD_INTERVAL"
done
