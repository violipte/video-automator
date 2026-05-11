#!/bin/bash
# Bootstrap do render-worker em pod RunPod (sem Docker custom image).
#
# Como funciona:
#   - Pod é criado com imagem oficial RunPod PyTorch (já tem CUDA + Python + PyTorch).
#   - Docker Start Command da pod chama este script.
#   - Script instala ffmpeg + deps Python + baixa código do VPS + roda worker.
#
# Vantagem: dispensa registry Docker. Pod sobe em ~2-3 min.
#
# Como configurar na RunPod:
#   Image: runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04
#          (ou similar - qualquer pytorch+cuda recente serve)
#   Docker Start Command:
#     bash -c "curl -fsSL http://85.239.243.215:8502/bootstrap.sh | bash"
#   Env Vars: WORKER_TOKEN=...
#
# Variáveis de ambiente esperadas (passar via Env Vars no painel RunPod):
#   WORKER_TOKEN  (obrigatório)
#   VPS_URL       (default: http://85.239.243.215:8500)
#   POLL_INTERVAL (default: 5)

set -e

VPS_URL_DEFAULT="http://85.239.243.215:8500"
BUNDLE_URL="${BUNDLE_URL:-http://85.239.243.215:8502/render-worker.tar.gz}"
VPS_URL="${VPS_URL:-$VPS_URL_DEFAULT}"

echo "================================================================"
echo "Render Worker Bootstrap (RunPod pod)"
echo "================================================================"
echo "VPS_URL:    $VPS_URL"
echo "BUNDLE_URL: $BUNDLE_URL"
echo ""

if [ -z "$WORKER_TOKEN" ]; then
    echo "ERRO: WORKER_TOKEN nao foi setado nos Env Vars da pod."
    echo "Configure WORKER_TOKEN no painel RunPod e reinicie a pod."
    exit 1
fi

# 0. SSH server (pra auto-download dos exports rodar via scp)
# RunPod normalmente sobe sshd por padrao, mas como o dockerArgs sobrescreve
# o CMD da imagem, precisamos subir manualmente.
echo "[0/5] Configurando SSH server pra acesso externo..."
mkdir -p /root/.ssh
chmod 700 /root/.ssh
if [ -n "${PUBLIC_KEY:-}" ]; then
    echo "$PUBLIC_KEY" > /root/.ssh/authorized_keys
    chmod 600 /root/.ssh/authorized_keys
    echo "  authorized_keys cadastrado ($(wc -c < /root/.ssh/authorized_keys) bytes)"
fi
# sshd ja vem na imagem PyTorch da RunPod; so precisa iniciar
mkdir -p /run/sshd
/usr/sbin/sshd -D > /var/log/sshd.log 2>&1 &
echo "  sshd subiu em background (PID $!)"

# 1. apt deps (ffmpeg + fontes pra subtitle render)
echo "[1/5] Instalando ffmpeg + fontes (apt-get)..."
export DEBIAN_FRONTEND=noninteractive
apt-get update -qq
apt-get install -y -qq --no-install-recommends \
    ffmpeg \
    fontconfig \
    fonts-dejavu-core \
    curl \
    openssh-server \
    > /tmp/apt.log 2>&1 || {
    echo "ERRO no apt-get. Log:"; tail -20 /tmp/apt.log; exit 1;
}

# 2. pip deps (faster-whisper, opencv headless, httpx, etc)
# Ubuntu 24.04 / Python 3.12 tem PEP 668 (externally-managed env). --break-system-packages
# é seguro aqui — container descartável, não afeta sistema do user.
echo "[2/5] Instalando deps Python (pip)..."
pip install --quiet --no-cache-dir --break-system-packages \
    faster-whisper \
    opencv-python-headless \
    numpy \
    pillow \
    psutil \
    httpx \
    || { echo "ERRO no pip install"; exit 1; }

# 3. Baixa bundle do worker (tarball com .py + rules/)
echo "[3/5] Baixando bundle do worker de $BUNDLE_URL..."
mkdir -p /opt/worker
curl -fsSL "$BUNDLE_URL" -o /tmp/worker.tar.gz || {
    echo "ERRO: bundle nao encontrado em $BUNDLE_URL"; exit 1;
}
tar -xzf /tmp/worker.tar.gz -C /opt/worker
echo "  Bundle extraido. Arquivos:"
ls /opt/worker | head -10

# 4. Cria dirs no network volume + valida assets
echo "[4/5] Validando network volume + assets..."
mkdir -p /workspace/temp /workspace/cache /workspace/exports /opt/worker/logs

if [ -d /workspace/assets ]; then
    echo "  Assets OK. Topo:"
    ls /workspace/assets | head -5
else
    echo "  AVISO: /workspace/assets nao existe. Network Volume nao foi montado ou nao foi sincronizado."
    echo "  O worker vai falhar quando tentar renderizar. Sincronize com docker/sync-assets.sh primeiro."
fi

# 5. Diagnóstico GPU + roda worker
echo "[5/5] Diagnostico GPU + iniciando worker..."
nvidia-smi -L 2>&1 | head -3 || echo "  (nvidia-smi falhou)"
ffmpeg -hide_banner -encoders 2>/dev/null | grep -i nvenc | head -3 || echo "  (FFmpeg sem NVENC)"
curl -sf -m 10 "$VPS_URL/api/health" > /dev/null && echo "  VPS OK" || echo "  AVISO: VPS nao respondeu /api/health"

# Exporta env vars que o render_worker.py espera (defaults sensatos)
export VPS_URL="$VPS_URL"
export WORKER_TOKEN="$WORKER_TOKEN"
export POLL_INTERVAL="${POLL_INTERVAL:-5}"
export TEMP_DIR="${TEMP_DIR:-/workspace/temp}"
export CACHE_DIR="${CACHE_DIR:-/workspace/cache}"
export EXPORT_BASE="${EXPORT_BASE:-/workspace/exports}"
export ASSETS_BASE_REMAP="${ASSETS_BASE_REMAP:-F:/Canal Dark=/workspace/assets|F:\\Canal Dark=/workspace/assets}"
export REMOTE_MODE=true

echo ""
echo "================================================================"
echo "Bootstrap concluido. Iniciando ${WORKERS_PER_POD:-2}x render_worker.py..."
echo "================================================================"

# DEFESA: salva env vars num .env file persistente. Se shell sair ou tmux perder
# contexto, workers podem source-ar isso pra ter acesso ao token + paths.
# CRITICO: usa aspas duplas em todos os valores — ASSETS_BASE_REMAP tem espaços,
# pipes e backslashes que sem quotes o bash interpreta como comando.
cat > /opt/worker/.env <<EOF
WORKER_TOKEN="${WORKER_TOKEN}"
VPS_URL="${VPS_URL}"
POLL_INTERVAL="${POLL_INTERVAL:-5}"
TEMP_DIR="${TEMP_DIR:-/workspace/temp}"
CACHE_DIR="${CACHE_DIR:-/workspace/cache}"
EXPORT_BASE="${EXPORT_BASE:-/workspace/exports}"
ASSETS_BASE_REMAP="${ASSETS_BASE_REMAP}"
REMOTE_MODE="true"
EOF
chmod 600 /opt/worker/.env

# DEFESA: cria script wrapper que SEMPRE source-a o .env antes de rodar worker.
# Se algo matar workers, basta `bash /opt/worker/run-worker.sh` pra reiniciar.
cat > /opt/worker/run-worker.sh <<'WRAPEOF'
#!/bin/bash
set -e
cd /opt/worker
set -a; source /opt/worker/.env; set +a
# Validacao critica antes de subir
if [ -z "$WORKER_TOKEN" ]; then
    echo "[run-worker] FATAL: WORKER_TOKEN vazio. Abortando." >&2
    exit 1
fi
exec python render_worker.py
WRAPEOF
chmod +x /opt/worker/run-worker.sh

cd /opt/worker

# Múltiplos workers paralelos no mesmo pod
N=${WORKERS_PER_POD:-2}
for i in $(seq 1 "$N"); do
    WORKER_ID="$i" /opt/worker/run-worker.sh > "/workspace/worker-${i}.log" 2>&1 &
    echo "  worker #$i iniciado (PID $!)"
done

# DEFESA: loop de respawn — se algum worker cair, reinicia em 5s.
while true; do
    # Conta workers vivos
    ALIVE=$(pgrep -f "/opt/worker/run-worker.sh" | wc -l)
    if [ "$ALIVE" -lt "$N" ]; then
        echo "[bootstrap] ALERTA: $ALIVE/$N workers vivos, respawning..."
        for i in $(seq 1 "$N"); do
            if ! pgrep -f "WORKER_ID=$i.*run-worker.sh" >/dev/null 2>&1; then
                # Pode ser que o pgrep não pegue env vars; abordagem alternativa simples:
                if [ ! -f "/workspace/worker-${i}.lock" ] || ! kill -0 $(cat /workspace/worker-${i}.lock 2>/dev/null) 2>/dev/null; then
                    WORKER_ID="$i" /opt/worker/run-worker.sh >> "/workspace/worker-${i}.log" 2>&1 &
                    echo $! > "/workspace/worker-${i}.lock"
                    echo "  worker #$i respawn (PID $!)"
                fi
            fi
        done
    fi
    sleep 30
done
