"""Gatilho automatico: ao fim do loop de junho (linha 82 = 30/06), dispara
producao do canal ENO de 01/06 -> 30/06 em loop.

Roda como systemd timer a cada 5min. Idempotente: cria flag apos disparar
pra nao disparar de novo. Se voce quiser re-disparar manualmente, basta
remover /opt/video-automator/_gatilho_eno_disparado.

Decisao Piter (29/05/2026): voltar pro ENO 01/06+ que estava sem tema na
primeira passada de junho. ordem_colunas=[8] (idx do ENO) garante que
NENHUM outro canal e re-processado.
"""
import json
import os
import sys
import urllib.request

FLAG = "/opt/video-automator/_gatilho_eno_disparado"
ENO_COL_IDX = 8           # indice da coluna ENO em temas.colunas
DATA_IDX_INICIO = 53      # linha 01/06/2026
DATA_IDX_FIM = 82         # linha 30/06/2026

if os.path.exists(FLAG):
    # Ja disparou. Sair silenciosamente.
    sys.exit(0)

try:
    with urllib.request.urlopen("http://localhost:8500/api/health", timeout=10) as r:
        d = json.loads(r.read())
except Exception as e:
    print(f"[gatilho-eno] erro lendo /api/health: {e}", flush=True)
    sys.exit(0)

ativa = bool(d.get("producao_ativa"))
loop = bool(d.get("loop"))
thread = bool(d.get("thread_alive"))
data_at = int(d.get("loop_data_atual", -1) or -1)

# CONDICAO: producao atual ESTAVA em loop e CHEGOU em 30/06 (linha 82) e
# fechou tudo (sem thread, sem loop, sem ativa). So entao dispara o ENO.
# Se o user cancelou antes de chegar a 30/06, NAO dispara (data_at < 82).
if ativa or thread or loop:
    # producao ainda rodando, espera
    sys.exit(0)

if data_at < DATA_IDX_FIM:
    # producao parada mas nao chegou em 30/06 (cancelada antes, ou outro motivo).
    # Nao dispara automaticamente.
    print(
        f"[gatilho-eno] aguardando: producao parada mas data_atual={data_at} < {DATA_IDX_FIM}. "
        "Nao disparando (talvez cancelada antes).",
        flush=True,
    )
    sys.exit(0)

# Disparar producao ENO 01/06 -> 30/06 loop
print(
    f"[gatilho-eno] junho fechou (data_at={data_at}). "
    f"Disparando ENO {DATA_IDX_INICIO}->{DATA_IDX_FIM} ordem=[{ENO_COL_IDX}] loop=True",
    flush=True,
)
body = json.dumps({
    "data_idx": DATA_IDX_INICIO,
    "ordem": [ENO_COL_IDX],
    "loop": True,
}).encode("utf-8")
req = urllib.request.Request(
    "http://localhost:8500/api/producao-completa/iniciar",
    data=body,
    headers={"Content-Type": "application/json"},
    method="POST",
)
try:
    with urllib.request.urlopen(req, timeout=15) as r:
        resp = json.loads(r.read())
    print(f"[gatilho-eno] resposta /iniciar: {resp}", flush=True)
    if resp.get("ok"):
        with open(FLAG, "w", encoding="utf-8") as f:
            f.write(f"disparado apos data_at={data_at}, ENO 01/06->30/06 loop\n")
        print(f"[gatilho-eno] flag criada em {FLAG}", flush=True)
    else:
        print(f"[gatilho-eno] /iniciar retornou nao-ok: {resp}", flush=True)
except Exception as e:
    print(f"[gatilho-eno] erro disparar /iniciar: {e}", flush=True)
