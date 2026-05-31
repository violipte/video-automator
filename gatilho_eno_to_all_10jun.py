"""Gatilho automatico: quando o ENO-loop atual chegar em 10/06 (linha 62),
cancela o filtro-ENO e religa producao normal (todos os canais) a partir
de 10/06 em diante. Idempotente: cria flag apos disparar.

Decisao Piter (29/05/2026): rodar ENO catch-up de 01/06 ate 09/06, e a
partir de 10/06 voltar a producao normal de junho (que estava em 10/06
quando cancelamos pra fazer o ENO catch-up).

Roda como systemd timer a cada 5min. Logs em journalctl.
"""
import json
import os
import sys
import time
import urllib.request

FLAG = "/opt/video-automator/_gatilho_eno_to_all_disparado"
ENO_COL_IDX = 8
DATA_IDX_ALVO = 62  # linha 10/06/2026 — quando ENO-loop chegar aqui, vira producao normal

if os.path.exists(FLAG):
    sys.exit(0)  # ja disparou

BASE = "http://localhost:8500"


def _get(path):
    with urllib.request.urlopen(BASE + path, timeout=10) as r:
        return json.loads(r.read())


def _post(path, body):
    req = urllib.request.Request(
        BASE + path,
        data=json.dumps(body).encode("utf-8"),
        headers={"Content-Type": "application/json"},
        method="POST",
    )
    with urllib.request.urlopen(req, timeout=15) as r:
        return json.loads(r.read())


try:
    health = _get("/api/health")
except Exception as e:
    print(f"[gatilho-eno-to-all] erro /api/health: {e}", flush=True)
    sys.exit(0)

ativa = bool(health.get("producao_ativa"))
loop = bool(health.get("loop"))
data_at = int(health.get("loop_data_atual", -1) or -1)

if not ativa or not loop:
    # producao parada/sem loop. Nao acao.
    sys.exit(0)

if data_at < DATA_IDX_ALVO:
    # ainda nao chegou em 10/06. Aguarda.
    sys.exit(0)

# Confirma que e o ENO-loop (ordem_colunas == [8]) pelo state file.
try:
    with open("/opt/video-automator/production_state.json", encoding="utf-8") as f:
        state = json.load(f)
    ordem = state.get("ordem_colunas")
except Exception as e:
    print(f"[gatilho-eno-to-all] erro lendo production_state: {e}", flush=True)
    sys.exit(0)

if ordem != [ENO_COL_IDX]:
    # producao corrente nao e ENO-loop (talvez ja virou normal, ou eh outra coisa).
    # Cria flag pra nao tentar de novo.
    with open(FLAG, "w", encoding="utf-8") as f:
        f.write(f"skip: ordem_colunas={ordem} nao eh [{ENO_COL_IDX}] em data_at={data_at}\n")
    print(f"[gatilho-eno-to-all] producao corrente nao e ENO-only (ordem={ordem}). Flag criada.", flush=True)
    sys.exit(0)

print(
    f"[gatilho-eno-to-all] ENO-loop chegou em data_at={data_at} (10/06). "
    "Cancelando ENO-loop e religando producao NORMAL a partir de 10/06.",
    flush=True,
)

# 1. Cancelar producao ENO-loop
try:
    _post("/api/producao-completa/cancelar", {})
except Exception as e:
    print(f"[gatilho-eno-to-all] erro cancelar: {e}", flush=True)
    sys.exit(0)

# 2. Esperar thread morrer (com fix do wait cancelavel, ~10-60s)
t0 = time.time()
parou = False
for _ in range(36):  # 36 * 5s = 180s = 3min max
    time.sleep(5)
    try:
        h = _get("/api/health")
        if not h.get("thread_alive"):
            parou = True
            break
    except Exception:
        pass

if not parou:
    print(
        f"[gatilho-eno-to-all] thread nao morreu em {int(time.time()-t0)}s. "
        "Abortando — opera manualmente.",
        flush=True,
    )
    sys.exit(0)

print(f"[gatilho-eno-to-all] thread parou em {int(time.time()-t0)}s. Religando.", flush=True)

# 3. Religar producao NORMAL (sem ordem = todos os canais) a partir de 10/06
try:
    resp = _post("/api/producao-completa/iniciar", {
        "data_idx": DATA_IDX_ALVO,
        "loop": True,
        # ordem omitido = todos os canais
    })
    print(f"[gatilho-eno-to-all] /iniciar resposta: {resp}", flush=True)
    if resp.get("ok"):
        with open(FLAG, "w", encoding="utf-8") as f:
            f.write(f"disparado em data_at={data_at}, religou normal a partir de linha {DATA_IDX_ALVO}\n")
        print(f"[gatilho-eno-to-all] flag criada. Producao normal rodando.", flush=True)
except Exception as e:
    print(f"[gatilho-eno-to-all] erro religar: {e}", flush=True)
