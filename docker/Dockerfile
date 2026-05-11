# Render Worker do Video Automator - imagem pra rodar em pod RunPod (4090, A5000, etc)
# Base: PyTorch oficial com CUDA 12.4 + Ubuntu 22.04 (compatível com RunPod RTX/A5000)
FROM pytorch/pytorch:2.5.0-cuda12.4-cudnn9-runtime

ENV DEBIAN_FRONTEND=noninteractive \
    TZ=America/Sao_Paulo \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

# FFmpeg (com NVENC), git, curl, fonts pra renderização
RUN apt-get update && apt-get install -y --no-install-recommends \
    ffmpeg \
    git \
    curl \
    fontconfig \
    fonts-dejavu-core \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /opt/worker

# Deps Python (pin de versões pra reproducibilidade)
RUN pip install --no-cache-dir \
    faster-whisper==1.2.1 \
    opencv-python-headless==4.13.0.88 \
    numpy \
    pillow \
    psutil \
    httpx

# Código do worker (modo render-only - não precisa do app.py / orchestrator)
COPY render_worker.py engine.py transcriber.py subtitle_fixer.py _whisper_subprocess.py /opt/worker/
COPY rules /opt/worker/rules

# Defaults (override via env vars no pod)
ENV VPS_URL="http://85.239.243.215:8500" \
    POLL_INTERVAL=5 \
    TEMP_DIR=/workspace/temp \
    CACHE_DIR=/workspace/cache \
    EXPORT_BASE=/workspace/exports \
    ASSETS_BASE_REMAP="F:/Canal Dark=/workspace/assets|F:\\Canal Dark=/workspace/assets" \
    REMOTE_MODE=true

# Entrypoint
COPY docker/entrypoint.sh /entrypoint.sh
RUN chmod +x /entrypoint.sh

CMD ["/entrypoint.sh"]
