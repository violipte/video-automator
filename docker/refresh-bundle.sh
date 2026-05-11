#!/bin/bash
# Atualiza o bundle do render-worker que os pods baixam ao subir.
# Roda no VPS quando algum dos arquivos do worker (engine.py, render_worker.py,
# transcriber.py, subtitle_fixer.py, _whisper_subprocess.py, rules/*) é modificado.
#
# Uso (no VPS):
#   bash /opt/video-automator/docker/refresh-bundle.sh
#
# Resultado:
#   /opt/worker-bootstrap/render-worker.tar.gz atualizado
#   Bootstrap server (porta 8502) serve esse arquivo. Pods que sobem depois
#   pegam a versão nova; pods já rodando precisam de restart.

set -e

STAGING_DIR="/opt/render-worker-build"
PROD_DIR="/opt/video-automator"
BUNDLE_DIR="/opt/worker-bootstrap"
BUNDLE_FILE="$BUNDLE_DIR/render-worker.tar.gz"

if [ ! -d "$STAGING_DIR" ]; then
    echo "ERRO: $STAGING_DIR nao existe. Criando..."
    mkdir -p "$STAGING_DIR/rules"
fi

mkdir -p "$BUNDLE_DIR"

# Sincroniza do prod (engine.py, subtitle_fixer.py existem em ambos) → staging
# Os arquivos render-only (transcriber, _whisper_subprocess) ficam só no staging.
for f in render_worker.py engine.py subtitle_fixer.py; do
    if [ -f "$PROD_DIR/$f" ]; then
        cp -u "$PROD_DIR/$f" "$STAGING_DIR/$f"
    fi
done
if [ -d "$PROD_DIR/rules" ]; then
    cp -ru "$PROD_DIR/rules/." "$STAGING_DIR/rules/"
fi

echo "Empacotando worker de $STAGING_DIR..."

cd "$STAGING_DIR"
tar -czf "$BUNDLE_FILE" \
    render_worker.py \
    engine.py \
    transcriber.py \
    subtitle_fixer.py \
    _whisper_subprocess.py \
    rules/

echo "Bundle atualizado:"
ls -la "$BUNDLE_FILE"
echo ""
echo "Conteudo:"
tar -tzf "$BUNDLE_FILE"
