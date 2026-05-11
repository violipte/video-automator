#!/bin/bash
# Entrypoint do render-worker em container.
# Cria dirs de cache + valida config + dispara worker.

set -e

if [ -z "$WORKER_TOKEN" ]; then
    echo "[entrypoint] ERRO: WORKER_TOKEN nao setado. Defina via env var no pod."
    echo "[entrypoint] Exemplo: docker run -e WORKER_TOKEN=xxx ..."
    exit 1
fi

# Garante que dirs de trabalho existem (em /workspace que é o network volume)
mkdir -p "${TEMP_DIR:-/workspace/temp}"
mkdir -p "${CACHE_DIR:-/workspace/cache}"
mkdir -p "${EXPORT_BASE:-/workspace/exports}"
mkdir -p /opt/worker/logs

# Diagnostico inicial
echo "================================================================"
echo "Render Worker — pod RunPod"
echo "================================================================"
echo "VPS_URL:           ${VPS_URL}"
echo "ASSETS_BASE_REMAP: ${ASSETS_BASE_REMAP}"
echo "TEMP_DIR:          ${TEMP_DIR:-/workspace/temp}"
echo "CACHE_DIR:         ${CACHE_DIR:-/workspace/cache}"
echo "EXPORT_BASE:       ${EXPORT_BASE:-/workspace/exports}"
echo ""
echo "GPU disponivel:"
nvidia-smi -L 2>&1 | head -3 || echo "  (nvidia-smi falhou — sem GPU?)"
echo ""
echo "FFmpeg NVENC:"
ffmpeg -hide_banner -encoders 2>/dev/null | grep -i nvenc | head -3 || echo "  (FFmpeg sem NVENC)"
echo ""
echo "Assets disponiveis em /workspace/assets:"
ls /workspace/assets 2>/dev/null | head -5 || echo "  (sem assets - sincronize antes de renderizar)"
echo ""
echo "Conectando ao VPS..."
curl -sf -m 10 "${VPS_URL}/api/health" > /dev/null && echo "  VPS OK" || echo "  AVISO: VPS nao respondeu /api/health"
echo "================================================================"

# Dispara worker
cd /opt/worker
exec python render_worker.py
