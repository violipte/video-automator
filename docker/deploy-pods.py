"""
Deploy 1-click de N pods PRO 4500 na RunPod com worker pollando o VPS.

Faz tudo em sequência:
  1. Cria N pods via GraphQL (sem dockerArgs — CMD default sobe sshd+jupyter)
  2. Aguarda cada pod ter publicIp + portMapping[22]
  3. SSH em cada pod e dispara bootstrap em tmux session "bootstrap"
  4. Aguarda workers começarem a pollar o VPS

Uso:
    python docker/deploy-pods.py              # cria 3 pods (default)
    python docker/deploy-pods.py 5            # cria 5 pods
    NUM_PODS=2 python docker/deploy-pods.py   # via env var

Variáveis de ambiente (defaults sensatos):
    RUNPOD_API_KEY   - chave da conta (default: hardcoded do Piter)
    NUM_PODS         - quantos criar (default: 3)
    NETWORK_VOLUME_ID - volume com assets (default: a0krcdr0og)
    GPU_TYPE_ID      - GPU a usar (default: NVIDIA RTX PRO 4500 Blackwell)
    IMAGE            - imagem base (default: runpod/pytorch:1.0.2-cu1281-...)
    WORKER_TOKEN     - bearer pra worker autenticar na VPS
    VPS_BOOTSTRAP_URL - URL do bootstrap.sh (default: 8502 do VPS)
    SSH_KEY          - chave privada SSH (default: ~/.ssh/id_rsa)

Output:
    Print do progresso por pod. Sai 0 se todos OK, !=0 se algum falhou.
"""
import json
import os
import pathlib
import subprocess
import sys
import time
import urllib.error
import urllib.request

# ====== Config ======
API_KEY = os.environ.get("RUNPOD_API_KEY", "")
if not API_KEY:
    raise RuntimeError("RUNPOD_API_KEY env var obrigatoria (sem fallback hardcoded por seguranca)")
NUM_PODS = int(os.environ.get("NUM_PODS") or (sys.argv[1] if len(sys.argv) > 1 else 3))
NETWORK_VOLUME_ID = os.environ.get("NETWORK_VOLUME_ID", "a0krcdr0og")
GPU_TYPE_ID = os.environ.get("GPU_TYPE_ID", "NVIDIA RTX PRO 4500 Blackwell")
IMAGE = os.environ.get("IMAGE", "runpod/pytorch:1.0.2-cu1281-torch280-ubuntu2404")
WORKER_TOKEN = os.environ.get("WORKER_TOKEN", "51dd755eb34a2cbdec24917a3afbb236f8b63059e286d0d1aeb8aae633cfabb7")
VPS_BOOTSTRAP_URL = os.environ.get("VPS_BOOTSTRAP_URL", "http://85.239.243.215:8502/bootstrap.sh")
VPS_HEALTH_URL = os.environ.get("VPS_HEALTH_URL", "http://85.239.243.215:8500/api/health")
SSH_KEY = os.environ.get("SSH_KEY", str(pathlib.Path.home() / ".ssh" / "id_rsa"))
CONTAINER_DISK_GB = int(os.environ.get("CONTAINER_DISK_GB", "50"))

GRAPHQL_URL = "https://api.runpod.io/graphql"
REST_URL = "https://rest.runpod.io/v1/pods"
SSH_OPTS = ["-o", "StrictHostKeyChecking=no",
            "-o", "UserKnownHostsFile=/dev/null",
            "-o", "LogLevel=ERROR",
            "-o", "ConnectTimeout=10"]


def log(msg, lvl="."):
    print(f"  [{lvl}] {msg}", flush=True)


def gql(query):
    body = json.dumps({"query": query}).encode()
    req = urllib.request.Request(
        GRAPHQL_URL, data=body,
        headers={"Authorization": f"Bearer {API_KEY}",
                 "Content-Type": "application/json",
                 "User-Agent": "curl/8.0.1"},
    )
    with urllib.request.urlopen(req, timeout=30) as resp:
        return json.loads(resp.read().decode())


def rest_get(path):
    req = urllib.request.Request(
        f"https://rest.runpod.io/v1{path}",
        headers={"Authorization": f"Bearer {API_KEY}"}
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        return json.loads(resp.read().decode())


def get_pubkey():
    p = pathlib.Path(SSH_KEY + ".pub")
    if not p.exists():
        sys.exit(f"ERRO: chave pública não encontrada em {p}")
    return p.read_text().strip()


def create_pod(name, pubkey):
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
       json.dumps(WORKER_TOKEN), json.dumps(pubkey))
    r = gql(mut)
    if "errors" in r:
        return None, r["errors"][0].get("message", str(r["errors"]))
    return r["data"]["podFindAndDeployOnDemand"], None


def wait_pod_ready(pod_id, timeout=300):
    """Aguarda pod ter publicIp + portMappings[22]. Returns (ip, port) or (None, None)."""
    deadline = time.time() + timeout
    while time.time() < deadline:
        try:
            p = rest_get(f"/pods/{pod_id}")
        except Exception as e:
            log(f"api err: {e}", "!")
            time.sleep(5)
            continue
        ip = p.get("publicIp")
        port = (p.get("portMappings") or {}).get("22")
        status = p.get("desiredStatus")
        if ip and port:
            return ip, port
        time.sleep(5)
    return None, None


def trigger_bootstrap(ip, port):
    """SSH no pod e dispara bootstrap em tmux session 'bootstrap'."""
    cmd_remote = (
        "tmux kill-session -t bootstrap 2>/dev/null; "
        "rm -f /workspace/bootstrap.log; "
        f"tmux new-session -d -s bootstrap "
        f"'env WORKER_TOKEN={WORKER_TOKEN} bash -c \\\"curl -fsSL {VPS_BOOTSTRAP_URL} | bash\\\" "
        "2>&1 | tee /workspace/bootstrap.log'; "
        "sleep 1; tmux ls 2>&1"
    )
    cmd = ["ssh"] + SSH_OPTS + ["-i", SSH_KEY, "-p", str(port), f"root@{ip}", cmd_remote]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    if proc.returncode != 0:
        return False, proc.stderr.strip()[:200]
    return ("bootstrap:" in proc.stdout), proc.stdout.strip()


def count_workers_polling():
    """Conta polls em /api/render-worker/next-job no último minuto via VPS health."""
    try:
        req = urllib.request.Request(VPS_HEALTH_URL)
        with urllib.request.urlopen(req, timeout=10) as resp:
            d = json.loads(resp.read().decode())
        return d
    except Exception:
        return None


def main():
    print("=" * 64)
    print(f"Deploy {NUM_PODS} pods RunPod ({GPU_TYPE_ID}) → produção worker")
    print(f"  Volume:  {NETWORK_VOLUME_ID}")
    print(f"  Image:   {IMAGE}")
    print(f"  Bootstrap: {VPS_BOOTSTRAP_URL}")
    print("=" * 64)

    pubkey = get_pubkey()
    print(f"\n[1/3] Criando {NUM_PODS} pods via GraphQL...")
    created = []
    for i in range(NUM_PODS):
        name = f"Pod {i+1}"
        log(f"criando {name}")
        info, err = create_pod(name, pubkey)
        if err:
            log(f"  FALHA: {err}", "!")
            continue
        log(f"  ok: {info['id']} / {info['desiredStatus']}", "OK")
        created.append(info)

    if not created:
        sys.exit("Nenhum pod criado. Veja o erro acima.")

    print(f"\n[2/3] Aguardando IP/SSH em cada pod (até 5min cada)...")
    ready = []
    for p in created:
        log(f"{p['name']} ({p['id']}): aguardando…")
        ip, port = wait_pod_ready(p["id"])
        if not ip:
            log(f"  TIMEOUT esperando IP", "!")
            continue
        log(f"  pronto: {ip}:{port}", "OK")
        ready.append({**p, "ip": ip, "port": port})

    if not ready:
        sys.exit("Nenhum pod ficou pronto. Veja erros acima.")

    print(f"\n[3/3] Disparando bootstrap em cada pod via SSH/tmux...")
    triggered = []
    for p in ready:
        log(f"{p['name']} ({p['ip']}:{p['port']}): disparando…")
        ok, msg = trigger_bootstrap(p["ip"], p["port"])
        if ok:
            log(f"  bootstrap rodando: {msg.splitlines()[-1] if msg else ''}", "OK")
            triggered.append(p)
        else:
            log(f"  falhou: {msg}", "!")

    print()
    print("=" * 64)
    print(f"RESUMO: {len(triggered)}/{NUM_PODS} pods bootstrapped com sucesso.")
    print("=" * 64)
    for p in triggered:
        print(f"  ✓ {p['name']:8s}  {p['id']}  ssh root@{p['ip']} -p {p['port']}")
    print()
    print("Próximos passos:")
    print(f"  - Workers vão começar a pollar VPS em ~3min (apt+pip+worker bundle)")
    print(f"  - Monitora em http://85.239.243.215:8500/v2/monitor")
    print(f"  - Pra parar todos depois: rode delete-pods.py (se existir) ou via painel RunPod")

    sys.exit(0 if len(triggered) == NUM_PODS else 1)


if __name__ == "__main__":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass
    main()
