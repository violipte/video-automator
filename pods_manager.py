"""
Lifecycle de pods RunPod — gerenciamento automático.

Funções:
    listar_pods()             - lista pods da conta com status/custo
    start_pods(n=3)            - sobe N pods PRO 4500 + aguarda workers conectarem
    stop_all_pods()            - para todos os pods (preserva config, $0 GPU)
    delete_all_pods()          - deleta todos os pods (zero custo, perde config)
    auto_shutdown_check()      - chamado por cron: para pods se idle por mais de IDLE_MIN

Estado interno em /opt/video-automator/pods_state.json:
    last_activity_ts: timestamp do último job ativo conhecido
    pods_ativos: lista de pod_ids que esperamos estar rodando

Configuração via config.json:
    runpod_api_key  - bearer token (mascarado em /api/config)
"""
import json
import os
import subprocess
import threading
import time
import urllib.error
import urllib.request
from datetime import datetime
from pathlib import Path

import scriptwriter

BASE_DIR = Path(__file__).parent
STATE_FILE = BASE_DIR / "pods_state.json"
_lock = threading.RLock()

# Config defaults
GPU_TYPE_ID = "NVIDIA RTX PRO 4500 Blackwell"
NETWORK_VOLUME_ID = "a0krcdr0og"
IMAGE = "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404"
CONTAINER_DISK_GB = 50
BOOTSTRAP_URL = "http://85.239.243.215:8502/bootstrap.sh"
IDLE_MINUTES_BEFORE_SHUTDOWN = 5

GRAPHQL_URL = "https://api.runpod.io/graphql"
REST_URL = "https://rest.runpod.io/v1"
HEADERS_GQL = {"Content-Type": "application/json", "User-Agent": "curl/8.0.1"}


def _api_key() -> str:
    cfg = scriptwriter.carregar_config()
    return cfg.get("runpod_api_key", "")


def _worker_token() -> str:
    cfg = scriptwriter.carregar_config()
    return cfg.get("render_worker_token", "")


def _public_key() -> str:
    """Public key SSH pra cadastrar nos pods (pra eu poder dar SSH e disparar bootstrap).
    Ordem: config.json > ~/.ssh/id_rsa.pub > /root/.ssh/id_rsa.pub.
    """
    cfg = scriptwriter.carregar_config()
    k = cfg.get("runpod_public_key", "").strip()
    if k:
        return k
    p = Path.home() / ".ssh" / "id_rsa.pub"
    if p.exists():
        return p.read_text().strip()
    p2 = Path("/root/.ssh/id_rsa.pub")
    if p2.exists():
        return p2.read_text().strip()
    return ""


def _gql(query: str, timeout: int = 30):
    key = _api_key()
    if not key:
        raise RuntimeError("runpod_api_key não configurada em config.json")
    body = json.dumps({"query": query}).encode()
    headers = {**HEADERS_GQL, "Authorization": f"Bearer {key}"}
    req = urllib.request.Request(GRAPHQL_URL, data=body, headers=headers)
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _rest_get(path: str, timeout: int = 15):
    key = _api_key()
    req = urllib.request.Request(f"{REST_URL}{path}", headers={"Authorization": f"Bearer {key}"})
    with urllib.request.urlopen(req, timeout=timeout) as resp:
        return json.loads(resp.read().decode())


def _rest_delete(path: str, timeout: int = 15):
    key = _api_key()
    req = urllib.request.Request(f"{REST_URL}{path}",
                                 headers={"Authorization": f"Bearer {key}"},
                                 method="DELETE")
    try:
        urllib.request.urlopen(req, timeout=timeout)
        return True
    except Exception:
        return False


def _load_state() -> dict:
    if not STATE_FILE.exists():
        return {"last_activity_ts": 0, "pods_ativos": [], "last_action": ""}
    try:
        return json.loads(STATE_FILE.read_text())
    except Exception:
        return {"last_activity_ts": 0, "pods_ativos": [], "last_action": ""}


def _save_state(s: dict):
    STATE_FILE.write_text(json.dumps(s, indent=2))


def listar_pods() -> list:
    """Lista pods da conta + custo por hora."""
    try:
        data = _rest_get("/pods")
        pods = data if isinstance(data, list) else data.get("data", [])
        out = []
        for p in pods:
            pm = p.get("portMappings") or {}
            out.append({
                "id": p.get("id"),
                "name": p.get("name"),
                "status": p.get("desiredStatus"),  # RUNNING / EXITED
                "gpu_type": (p.get("machine") or {}).get("gpuDisplayName") or "?",
                "ip": p.get("publicIp"),
                "ssh_port": pm.get("22"),
                "cost_per_hr": p.get("costPerHr", 0),
                "created_at": p.get("createdAt"),
                "last_started_at": p.get("lastStartedAt"),
            })
        return out
    except Exception as e:
        return [{"erro": str(e)}]


def _resume_one_pod(pod_id: str) -> tuple:
    """Tenta resumir um pod EXITED. Retorna (ok, msg).
    Pode falhar se RunPod realocou a GPU. Caller deve fallback pra criar novo.
    """
    mut = f'mutation {{ podResume(input: {{podId: "{pod_id}"}}) {{ id desiredStatus }} }}'
    try:
        r = _gql(mut)
    except Exception as e:
        return False, f"exception: {e}"
    if "errors" in r:
        return False, r["errors"][0].get("message", str(r["errors"]))
    return True, "resumed"


def _create_one_pod(name: str, pubkey: str) -> dict | None:
    token = _worker_token()
    if not token:
        return {"erro": "render_worker_token não configurado em config.json"}
    mut = """
mutation {
  podFindAndDeployOnDemand(input: {
    cloudType: SECURE
    gpuTypeId: %s
    networkVolumeId: %s
    name: %s
    imageName: %s
    containerDiskInGb: %d
    volumeMountPath: "/workspace"
    ports: "22/tcp,8888/http"
    env: [
      { key: "WORKER_TOKEN", value: %s }
      { key: "PUBLIC_KEY", value: %s }
      { key: "JUPYTER_PASSWORD", value: "automator" }
    ]
    gpuCount: 1
  }) { id name desiredStatus }
}
""" % (json.dumps(GPU_TYPE_ID), json.dumps(NETWORK_VOLUME_ID),
       json.dumps(name), json.dumps(IMAGE), CONTAINER_DISK_GB,
       json.dumps(token), json.dumps(pubkey))
    try:
        r = _gql(mut)
    except Exception as e:
        return {"erro": str(e)}
    if "errors" in r:
        return {"erro": r["errors"][0].get("message", str(r["errors"]))}
    return r["data"]["podFindAndDeployOnDemand"]


def _wait_pod_ready(pod_id: str, timeout: int = 240) -> tuple:
    """Aguarda pod ter publicIp + portMappings[22]. Retorna (ip, port) ou (None, None)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            p = _rest_get(f"/pods/{pod_id}")
        except Exception:
            time.sleep(5)
            continue
        ip = p.get("publicIp")
        port = (p.get("portMappings") or {}).get("22")
        if ip and port:
            return ip, port
        time.sleep(5)
    return None, None


def _trigger_bootstrap(ip: str, port: int) -> tuple:
    """SSH no pod e dispara bootstrap em tmux session 'bootstrap'.
    Usa bash -s via stdin pra evitar escape hell de aspas em comando inline."""
    ssh_key = "/root/.ssh/id_rsa"
    if not Path(ssh_key).exists():
        ssh_key = str(Path.home() / ".ssh" / "id_rsa")
    token = _worker_token()
    # Script remoto: usa heredoc 'BSEND' (single-quoted) pra impedir expansão no bash remoto.
    # WORKER_TOKEN é interpolado pelo Python antes de virar string.
    script = f"""set -e
tmux kill-session -t bootstrap 2>/dev/null || true
rm -f /workspace/bootstrap.log
cat > /tmp/bs.sh <<'BSEND'
#!/bin/bash
export WORKER_TOKEN={token}
curl -fsSL {BOOTSTRAP_URL} | bash
BSEND
chmod +x /tmp/bs.sh
tmux new-session -d -s bootstrap "/tmp/bs.sh > /workspace/bootstrap.log 2>&1"
sleep 1
tmux ls
"""
    cmd = ["ssh",
           "-o", "StrictHostKeyChecking=no",
           "-o", "UserKnownHostsFile=/dev/null",
           "-o", "LogLevel=ERROR",
           "-o", "ConnectTimeout=15",
           "-T",
           "-i", ssh_key, "-p", str(port), f"root@{ip}", "bash -s"]
    try:
        proc = subprocess.run(cmd, input=script, capture_output=True, text=True, timeout=30)
        return ("bootstrap:" in proc.stdout), (proc.stdout + proc.stderr).strip()
    except Exception as e:
        return False, str(e)


def start_pods(n: int = 3, aguardar_polls: bool = True, max_wait_polls_s: int = 300) -> dict:
    """Garante N pods rodando, **resumindo EXITED primeiro** (mais rápido +
    barato: ~30s vs 3min de bootstrap fresco). Se resume falhar (sem GPU
    no host antigo), cria pod novo como fallback.

    Retorna {ok: bool, pods_criados, pods_resumidos, log: [...]}.
    """
    pubkey = _public_key()
    if not pubkey:
        return {"ok": False, "msg": "Public key SSH não encontrada (~/.ssh/id_rsa.pub)"}

    # Verifica se já tem pods ativos — não cria duplicado
    todos = listar_pods()
    atuais = [p for p in todos if p.get("status") == "RUNNING"]
    if len(atuais) >= n:
        return {"ok": True, "pods": atuais, "msg": f"{len(atuais)} pods ja rodando, nada a fazer"}

    pra_subir = n - len(atuais)
    log = []
    log.append(f"Tem {len(atuais)} pods rodando, vou subir +{pra_subir}")

    # PASSO 1: tenta RESUMIR pods EXITED com nome Pod 1/2/3 (priorizando ordem)
    exited = sorted(
        [p for p in todos if p.get("status") == "EXITED" and (p.get("name") or "").startswith("Pod ")],
        key=lambda p: p.get("last_started_at", ""), reverse=True,  # mais recentes primeiro
    )
    criados = []      # pods recém-criados (precisam de bootstrap fresco — vai herdar do template)
    resumidos = []    # pods resumidos (precisam de re-trigger bootstrap)
    for p in exited[:pra_subir]:
        ok, msg = _resume_one_pod(p["id"])
        if ok:
            log.append(f"Resumido: {p.get('name')} ({p['id']}) — {msg}")
            resumidos.append(p)
        else:
            log.append(f"Resume falhou pra {p.get('name')}: {msg[:80]}")
            # Não vai pro fallback aqui — o restante (n - len(atuais) - len(resumidos)) será criado abaixo

    # PASSO 2: pra completar até N, cria pods novos (fallback se resume falhou)
    pra_criar = pra_subir - len(resumidos)
    for idx in range(pra_criar):
        name = f"Pod {len(atuais) + len(resumidos) + idx + 1}"
        info = _create_one_pod(name, pubkey)
        if info and "erro" not in info:
            criados.append(info)
            log.append(f"Criado: {name} ({info.get('id')})")
        else:
            log.append(f"FALHA criando {name}: {info.get('erro') if info else 'unknown'}")

    todos_pra_setup = criados + resumidos
    if not todos_pra_setup:
        return {"ok": False, "msg": "Nenhum pod novo nem resumido", "log": log}

    # Aguarda IP+SSH em cada pod (tanto novos quanto resumidos)
    prontos = []
    for p in todos_pra_setup:
        ip, port = _wait_pod_ready(p["id"])
        if not ip:
            log.append(f"TIMEOUT IP/SSH pra {p['name']} ({p['id']})")
            continue
        log.append(f"{p['name']}: IP/SSH em {ip}:{port}")
        # Dispara bootstrap (tanto pra criados quanto pra resumidos — o tmux session morre quando pod stop)
        ok, msg = _trigger_bootstrap(ip, port)
        if ok:
            log.append(f"{p['name']}: bootstrap disparado")
            prontos.append({**p, "ip": ip, "port": port})
        else:
            log.append(f"{p['name']}: bootstrap falhou - {msg[:200]}")

    # Atualiza state
    with _lock:
        st = _load_state()
        st["pods_ativos"] = [p["id"] for p in (atuais + prontos)]
        st["last_action"] = "start_pods"
        st["last_activity_ts"] = time.time()
        _save_state(st)

    # (Opcional) aguarda primeiro worker pollar /api/render-worker/next-job
    polls_detected = False
    if aguardar_polls and prontos:
        try:
            import production_log
        except Exception:
            production_log = None
        deadline = time.time() + max_wait_polls_s
        # Heuristica: poll do orchestrator log "Worker" recebido (não temos contador direto)
        # Como proxy, aguarda 60s mínimo e considera OK
        time.sleep(60)
        polls_detected = True
        log.append(f"Aguardado {60}s; workers devem estar pollando agora")

    return {
        "ok": bool(prontos),
        "pods_criados": len(criados),
        "pods_resumidos": len(resumidos),
        "pods_prontos": len(prontos),
        "polls_detected": polls_detected,
        "log": log,
    }


def stop_all_pods() -> dict:
    """Para TODOS os pods da conta (preserva config). Retorna {ok, parados, log}."""
    pods = listar_pods()
    parados = []
    log = []
    for p in pods:
        if p.get("status") != "RUNNING":
            continue
        pid = p["id"]
        try:
            mut = f'mutation {{ podStop(input: {{podId: "{pid}"}}) {{ id desiredStatus }} }}'
            r = _gql(mut)
            if "data" in r and r["data"].get("podStop"):
                parados.append(p)
                log.append(f"Stopped {p.get('name')} ({pid})")
            else:
                log.append(f"FALHA stop {p.get('name')}: {r}")
        except Exception as e:
            log.append(f"FALHA stop {p.get('name')}: {e}")

    with _lock:
        st = _load_state()
        st["pods_ativos"] = []
        st["last_action"] = "stop_all_pods"
        _save_state(st)

    return {"ok": True, "parados": len(parados), "log": log}


def delete_all_pods() -> dict:
    """Deleta TODOS os pods (zero custo, perde config)."""
    pods = listar_pods()
    deletados = []
    log = []
    for p in pods:
        pid = p.get("id")
        if not pid:
            continue
        if _rest_delete(f"/pods/{pid}"):
            deletados.append(p)
            log.append(f"Deleted {p.get('name')} ({pid})")
        else:
            log.append(f"FALHA delete {p.get('name')} ({pid})")
    with _lock:
        st = _load_state()
        st["pods_ativos"] = []
        st["last_action"] = "delete_all_pods"
        _save_state(st)
    return {"ok": True, "deletados": len(deletados), "log": log}


def marcar_atividade():
    """Reseta o timer de idle. Chame a cada job ativo / poll de worker."""
    with _lock:
        st = _load_state()
        st["last_activity_ts"] = time.time()
        _save_state(st)


def watchdog_check() -> dict:
    """Detecta condição inaceitável: pods RUNNING + jobs aguardando + zero polls de worker.

    Threshold: 300s (5min) — apt+pip+bundle do bootstrap leva 2-3min normalmente.

    Se essa condição persistir por 300s sem nenhum poll:
      1. Re-dispara bootstrap nos pods (auto-recovery)
      2. Se 2ª violação consecutiva (10min total): MATA pods (anti-vazamento)

    Chamada pelo cron a cada 60s.
    """
    try:
        import production_log
        import render_queue
    except Exception:
        return {"ok": False, "msg": "import error"}

    state = production_log.obter_estado() or {}
    if not state.get("ativo"):
        return {"ok": True, "msg": "sem produção ativa"}

    # Conta jobs remaining
    jobs = list(getattr(render_queue, "_remote_jobs", []) or [])
    pending = [j for j in jobs if j.get("status") == "pending"]
    processing = [j for j in jobs if j.get("status") == "processing"]
    if not pending and not processing:
        return {"ok": True, "msg": "sem jobs aguardando"}

    # Pods rodando?
    pods = listar_pods()
    rodando = [p for p in pods if p.get("status") == "RUNNING"]
    if not rodando:
        return {"ok": True, "msg": "sem pods rodando, esperando lifecycle subir"}

    # Tem polls de worker chegando? Conferir last_poll_ts em cada job processing
    # ou via render_queue._workers_seen
    workers_seen = getattr(render_queue, "_workers_seen", {}) or {}
    now = time.time()
    THRESHOLD_S = 300  # 5min — tempo razoável pra apt+pip+bundle do bootstrap
    polls_recentes = [w for w, ts in workers_seen.items() if (now - ts) < THRESHOLD_S]

    # Estado:
    #   - há jobs pending E pods rodando E zero polls em 300s = ANOMALIA
    if not polls_recentes:
        with _lock:
            st = _load_state()
            violacoes = st.get("watchdog_violacoes", 0) + 1
            st["watchdog_violacoes"] = violacoes
            _save_state(st)

        production_log.adicionar_log(
            f"WATCHDOG: ANOMALIA — {len(rodando)} pods RUNNING + {len(pending) + len(processing)} jobs"
            f" + 0 polls em {THRESHOLD_S}s. Violacao #{violacoes}"
        )

        if violacoes == 1:
            # Tenta recovery: re-dispara bootstrap nos pods
            production_log.adicionar_log("WATCHDOG: tentando re-disparar workers nos pods...")
            for p in rodando:
                ip = p.get("ip")
                port = p.get("ssh_port")
                if not ip or not port:
                    continue
                ok, msg = _trigger_bootstrap(ip, port)
                if ok:
                    production_log.adicionar_log(f"WATCHDOG: {p['name']} bootstrap re-disparado")
                else:
                    production_log.adicionar_log(f"WATCHDOG: {p['name']} bootstrap falhou: {msg[:100]}")
            return {"ok": True, "msg": "violação #1 — bootstrap re-disparado"}
        else:
            # Violação repetida: MATA tudo (anti-vazamento)
            production_log.adicionar_log(
                f"WATCHDOG: VIOLACAO #{violacoes} — matando pods (anti-vazamento). "
                f"Refaça o disparo da produção quando diagnosticar a causa."
            )
            stop_all_pods()
            return {"ok": False, "msg": f"violação #{violacoes} — pods abatidos"}

    # Tudo OK — reset contador
    with _lock:
        st = _load_state()
        if st.get("watchdog_violacoes"):
            st["watchdog_violacoes"] = 0
            _save_state(st)
    return {"ok": True, "msg": f"OK — {len(polls_recentes)} workers ativos"}


def auto_scale_down_check(workers_per_pod: int = 2) -> dict:
    """Verifica se há pods sobrando vs fila atual e desliga os ociosos.

    Política:
      - Conta jobs remaining (pending + processing) em render_queue
      - Calcula pods_needed = max(1, ceil(jobs_remaining / workers_per_pod))
      - Se RUNNING pods > pods_needed E produção ainda ativa, desliga pods OCIOSOS
        (sem job em processing) até bater pods_needed
      - Mantém pelo menos 1 pod enquanto há jobs

    Chamada pelo cron a cada 60s. Não atua se produção não está ativa
    (a fim normal já é tratado pelo hook do orchestrator).
    """
    try:
        import production_log
        import render_queue
    except Exception:
        return {"ok": False, "msg": "import error"}

    state = production_log.obter_estado() or {}
    if not state.get("ativo"):
        return {"ok": True, "msg": "produção não ativa, scale-down delegado pro hook do orchestrator"}

    # Conta jobs remaining
    jobs = list(getattr(render_queue, "_remote_jobs", []) or [])
    pending = [j for j in jobs if j.get("status") == "pending"]
    processing = [j for j in jobs if j.get("status") == "processing"]
    jobs_remaining = len(pending) + len(processing)

    if jobs_remaining == 0:
        return {"ok": True, "msg": "sem jobs remaining, deixando hook do orchestrator parar tudo"}

    # Pods rodando
    pods = listar_pods()
    rodando = [p for p in pods if p.get("status") == "RUNNING"]
    if len(rodando) <= 1:
        return {"ok": True, "msg": f"{len(rodando)} pod(s) rodando, mantém"}

    # Quantos pods precisamos pra essa fila?
    import math
    pods_needed = max(1, math.ceil(jobs_remaining / workers_per_pod))
    if len(rodando) <= pods_needed:
        return {"ok": True, "msg": f"{len(rodando)} pods, {pods_needed} necessários, OK"}

    # Identifica pods OCIOSOS — que não tem job em processing
    pods_processing_ids = set()
    for j in processing:
        wid = j.get("worker_id")
        if wid:
            pods_processing_ids.add(wid)

    # Worker_id pode ser "Pod 1#A" (pod + worker letter). Pegamos o nome do pod.
    pods_ocupados_names = set()
    for wid in pods_processing_ids:
        # split simples — qualquer convenção do worker
        nome_pod = wid.split("#")[0].split(":")[0].strip()
        pods_ocupados_names.add(nome_pod)

    pods_ociosos = [p for p in rodando if p.get("name") not in pods_ocupados_names]
    pra_desligar = len(rodando) - pods_needed
    pra_desligar = min(pra_desligar, len(pods_ociosos))

    if pra_desligar <= 0:
        return {"ok": True, "msg": f"todos {len(rodando)} pods ocupados, ninguém pra desligar"}

    # Desliga os primeiros N ociosos
    parados = []
    for p in pods_ociosos[:pra_desligar]:
        pid = p["id"]
        try:
            mut = f'mutation {{ podStop(input: {{podId: "{pid}"}}) {{ id desiredStatus }} }}'
            r = _gql(mut)
            if "data" in r and r["data"].get("podStop"):
                parados.append(p["name"])
                production_log.adicionar_log(
                    f"AUTO-SCALE: {p['name']} desligado (idle, fila tem {jobs_remaining} jobs, "
                    f"{pods_needed} pods bastam)"
                )
        except Exception:
            pass

    return {"ok": True, "msg": f"scale-down: parou {len(parados)} pods ociosos, "
                                f"sobrou {len(rodando)-len(parados)} ativos pra {jobs_remaining} jobs",
            "parados": parados}


def auto_shutdown_check(idle_minutes: float = IDLE_MINUTES_BEFORE_SHUTDOWN) -> dict:
    """Chamada por cron a cada N min. Para todos pods se idle por mais de N min.

    Idle = produção não ativa E render_queue vazia E último activity timestamp > N min atrás.
    """
    try:
        import production_log
        import render_queue
    except Exception:
        return {"ok": False, "msg": "import error"}

    state = production_log.obter_estado() or {}
    if state.get("ativo"):
        marcar_atividade()
        return {"ok": True, "msg": "produção ativa, nada a fazer"}

    # Render queue tem job ativo?
    try:
        rqueue_ativo = bool(getattr(render_queue, "_remote_jobs", []))
    except Exception:
        rqueue_ativo = False

    if rqueue_ativo:
        marcar_atividade()
        return {"ok": True, "msg": "fila com jobs, nada a fazer"}

    # Verifica idle
    with _lock:
        st = _load_state()
        last = st.get("last_activity_ts", 0)

    idle_s = time.time() - last
    if idle_s < idle_minutes * 60:
        return {"ok": True, "msg": f"idle apenas {int(idle_s)}s, threshold {int(idle_minutes*60)}s"}

    # Tem pods rodando?
    pods = listar_pods()
    rodando = [p for p in pods if p.get("status") == "RUNNING"]
    if not rodando:
        return {"ok": True, "msg": "sem pods rodando, nada a fazer"}

    # Para todos
    r = stop_all_pods()
    return {"ok": True, "msg": f"shutdown automatico — {r['parados']} pods parados (idle {int(idle_s)}s)"}
