"""Watchdog de produção — detecta stalls e loga alertas visíveis.

Roda como systemd timer a cada 5 min. Quando detecta stall, escreve WARNING
em journalctl + atualiza /opt/video-automator/_watchdog_status.json (que o
/api/health expõe pra dashboard ver).

Tipos de stall detectados:
  1. PROD-IDLE: production_state.json não atualiza ha N min com producao_ativa=True
  2. NARR-PENDING: job de narração em fila pending > N min sem job_atual (worker offline)
  3. NARR-STUCK: job_atual ativo mas sem progresso ha N min

Saída:
  - stdout: linhas WARNING/OK pro journalctl
  - _watchdog_status.json: snapshot serializado pro dashboard

NÃO age automaticamente (v1). Versão v2 forçará Inworld fallback quando
detectar stall confirmado.
"""
import json
import os
import sys
import time
from pathlib import Path

BASE = Path(__file__).parent
STATE_FILE = BASE / "production_state.json"
STATUS_FILE = BASE / "_watchdog_status.json"

# Thresholds (segundos)
PROD_STALL = 10 * 60          # 10 min sem update do state file durante producao ativa
NARR_PENDING_STALL = 10 * 60  # 10 min com job em pending sem worker pegar
NARR_STUCK_STALL = 15 * 60    # 15 min com job_atual ativo sem progresso

alerts = []
agora = time.time()


def log(level, msg):
    print(f"{level} [watchdog-prod] {msg}", flush=True)
    alerts.append({"ts": agora, "level": level, "msg": msg})


# === Check 1: production_state.json stale ===
prod_ativa = False
prod_data_ref = ""
if STATE_FILE.exists():
    try:
        state = json.load(open(STATE_FILE, encoding="utf-8"))
        prod_ativa = bool(state.get("ativo"))
        prod_data_ref = state.get("data_ref", "")
        mtime = STATE_FILE.stat().st_mtime
        idle = agora - mtime

        if prod_ativa and idle > PROD_STALL:
            # Identifica canal travado
            canais = state.get("canais", [])
            travado = next(
                (c for c in canais
                 if c.get("etapa") in ("roteiro", "narracao", "video", "render")
                 and c.get("etapa") != "concluido"),
                None,
            )
            tag = travado.get("tag", "?") if travado else "?"
            etapa = travado.get("etapa", "?") if travado else "?"
            det = (travado.get("etapa_detalhe", "") if travado else "")[:80]
            log(
                "WARNING",
                f"PROD-IDLE: state file sem update ha {idle/60:.1f}min "
                f"(producao_ativa=True, data={prod_data_ref}). "
                f"Provavel canal travado: {tag} [{etapa}] {det}",
            )
        elif prod_ativa:
            log(
                "OK",
                f"prod ativa data={prod_data_ref}, ultimo update {idle:.0f}s atras",
            )
        else:
            log("OK", "prod inativa")
    except Exception as e:
        log("ERROR", f"falha lendo {STATE_FILE.name}: {e}")
else:
    log("OK", "state file ausente (sem producao)")


# === Check 2 + 3: narration queue ===
try:
    sys.path.insert(0, str(BASE))
    import narration_queue
    estado = narration_queue.obter_estado()
    pendentes = narration_queue.jobs_remotos_pendentes() or []
    job_atual = estado.get("job_atual", None)

    # NARR-PENDING: jobs sem ser pegos
    for j in pendentes:
        if j.get("status") != "pending":
            continue
        ts = j.get("ts", agora)
        age = agora - ts
        if age > NARR_PENDING_STALL:
            log(
                "WARNING",
                f"NARR-PENDING: job {j.get('id','?')} ha {age/60:.1f}min em fila "
                f"sem worker pegar. Verifique render_worker.py local "
                f"(F:/Canal Dark/.../video-automator/render_worker_starter.pyw).",
            )

    # NARR-STUCK: job_atual mas sem progresso
    if job_atual:
        started_at = job_atual.get("started_at", 0) or 0
        last_progress = job_atual.get("last_progress_ts", started_at) or started_at
        if started_at:
            since_progress = agora - last_progress
            if since_progress > NARR_STUCK_STALL:
                log(
                    "WARNING",
                    f"NARR-STUCK: job {job_atual.get('id','?')} sem progresso ha "
                    f"{since_progress/60:.1f}min (worker pode ter morrido mid-job).",
                )

    if not pendentes and not job_atual:
        log("OK", "narration queue vazia")
except Exception as e:
    log("ERROR", f"falha checando narration_queue: {e}")


# === Persistir snapshot pro dashboard ler ===
try:
    snapshot = {
        "ts": agora,
        "iso": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(agora)),
        "prod_ativa": prod_ativa,
        "data_ref": prod_data_ref,
        "alerts": alerts,
        "warnings": [a for a in alerts if a["level"] == "WARNING"],
        "n_warnings": sum(1 for a in alerts if a["level"] == "WARNING"),
    }
    STATUS_FILE.write_text(
        json.dumps(snapshot, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
except Exception as e:
    log("ERROR", f"falha escrevendo {STATUS_FILE.name}: {e}")


# Exit code 1 se houver warnings (pra systemd marcar service como degraded se quiser)
sys.exit(1 if any(a["level"] == "WARNING" for a in alerts) else 0)
