"""
Auto-download dos vídeos renderizados do Network Volume RunPod → PC do Piter.

Roda em loop infinito. A cada DOWNLOAD_INTERVAL segundos:
  1. Pega o primeiro pod RUNNING via RunPod API
  2. Lista arquivos remotos em /workspace/exports/ via SSH
  3. Compara com arquivos locais (skip se size bater)
  4. Baixa só os novos via scp

Sem dependências extras (usa só stdlib). SSH/SCP do Git Bash.

Uso:
    python docker/auto-download.py

Variáveis de ambiente (opcionais):
    RUNPOD_API_KEY  – default: chave do Piter já hardcoded
    DEST            – default: F:/Canal Dark/Automator Exports
    SSH_KEY         – default: ~/.ssh/id_rsa
    INTERVAL        – default: 60 (segundos)
"""
import json
import os
import subprocess
import sys
import time
import urllib.request
from datetime import datetime
from pathlib import Path

API_KEY = os.environ.get("RUNPOD_API_KEY", "")
if not API_KEY:
    raise RuntimeError("RUNPOD_API_KEY env var obrigatoria (sem fallback hardcoded por seguranca)")
DEST = Path(os.environ.get("DEST", "F:/Canal Dark/Automator Exports"))
SSH_KEY = os.environ.get("SSH_KEY", str(Path.home() / ".ssh" / "id_rsa"))
INTERVAL = int(os.environ.get("INTERVAL", "60"))
REMOTE_BASE = "/workspace/exports"

SSH_BASE = [
    "ssh",
    "-o", "StrictHostKeyChecking=no",
    "-o", "UserKnownHostsFile=/dev/null",
    "-o", "LogLevel=ERROR",
    "-o", "ConnectTimeout=15",
    "-o", "ServerAliveInterval=30",
    "-i", SSH_KEY,
]


def log(msg):
    ts = datetime.now().strftime("%H:%M:%S")
    print(f"[{ts}] {msg}", flush=True)


def get_active_pod():
    """Pega o primeiro pod RUNNING com SSH publico configurado."""
    req = urllib.request.Request(
        "https://rest.runpod.io/v1/pods",
        headers={"Authorization": f"Bearer {API_KEY}"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            data = json.loads(resp.read().decode())
    except Exception as e:
        log(f"  ERRO API RunPod: {e}")
        return None
    pods = data if isinstance(data, list) else data.get("data", [])
    for p in pods:
        if p.get("desiredStatus") != "RUNNING":
            continue
        ip = p.get("publicIp")
        port = (p.get("portMappings") or {}).get("22")
        if ip and port:
            return {"name": p.get("name", "?"), "ip": ip, "port": port}
    return None


def list_remote_files(pod):
    """Lista arquivos em /workspace/exports/ via SSH. Retorna [(path, size), ...]."""
    cmd = SSH_BASE + [
        "-p", str(pod["port"]),
        f"root@{pod['ip']}",
        f"find {REMOTE_BASE} -type f -printf '%s\\t%P\\n' 2>/dev/null",
    ]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=30)
    except subprocess.TimeoutExpired:
        log("  TIMEOUT listando arquivos remotos")
        return []
    if proc.returncode != 0:
        log(f"  Erro ssh ls: {proc.stderr.strip()[:200]}")
        return []
    files = []
    for line in proc.stdout.splitlines():
        if not line.strip():
            continue
        try:
            size_str, rel = line.split("\t", 1)
            files.append((rel, int(size_str)))
        except ValueError:
            continue
    return files


def download_file(pod, rel_path):
    """Baixa /workspace/exports/{rel_path} → DEST/{rel_path}. Cria dirs."""
    local = DEST / rel_path
    local.parent.mkdir(parents=True, exist_ok=True)
    remote = f"root@{pod['ip']}:{REMOTE_BASE}/{rel_path}"
    cmd = [
        "scp",
        "-o", "StrictHostKeyChecking=no",
        "-o", "UserKnownHostsFile=/dev/null",
        "-o", "LogLevel=ERROR",
        "-o", "ConnectTimeout=15",
        "-i", SSH_KEY,
        "-P", str(pod["port"]),
        "-q",
        remote,
        str(local),
    ]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=600)
    return proc.returncode == 0, proc.stderr.strip()[:300]


# Tracking de sizes vistos na iteração anterior (pra detectar arquivos em escrita)
_last_sizes = {}

def sync_once(pod):
    """Sync incremental + DEFESA contra arquivo em escrita.

    Pra cada arquivo remoto:
      1. Skip se ja existe local com mesmo size (ja baixado).
      2. Skip se size MUDOU desde a iteracao anterior (worker ainda escrevendo).
      3. So baixa quando size estabilizou (igual entre 2 iteracoes consecutivas).
    Isso elimina download de MP4 sem moov atom (corrompido).
    """
    log(f"Listando remoto via {pod['name']} ({pod['ip']}:{pod['port']})...")
    remote_files = list_remote_files(pod)
    if not remote_files:
        log("  Nenhum arquivo remoto (ou erro listando).")
        return 0, 0
    log(f"  {len(remote_files)} arquivos no Network Volume.")

    novos = 0
    pulados = 0
    em_escrita = 0
    erros = 0
    new_sizes = {}
    for rel, remote_size in remote_files:
        new_sizes[rel] = remote_size
        local = DEST / rel
        # Skip se já existe local com mesmo size (já baixado)
        if local.exists() and local.stat().st_size == remote_size:
            pulados += 1
            continue
        # DEFESA: se size mudou desde última iteração, está em escrita — skip
        last_size = _last_sizes.get(rel)
        if last_size is not None and last_size != remote_size:
            log(f"  ⏳ {rel} ({remote_size / 1_000_000:.1f}MB) ainda em escrita (anterior {last_size / 1_000_000:.1f}MB), aguardando estabilizar")
            em_escrita += 1
            continue
        # DEFESA extra: size precisa ter sido visto antes (1ª aparição = ainda em escrita provavelmente)
        if last_size is None:
            log(f"  ⏳ {rel} ({remote_size / 1_000_000:.1f}MB) primeira aparição, aguardando próxima iteração pra confirmar estabilidade")
            em_escrita += 1
            continue
        log(f"  ↓ {rel} ({remote_size / 1_000_000:.1f}MB)...")
        ok, err = download_file(pod, rel)
        if ok:
            # Validação extra pós-download: tamanhos batem
            if local.exists() and abs(local.stat().st_size - remote_size) < 1024:
                novos += 1
            else:
                log(f"    AVISO: size local {local.stat().st_size if local.exists() else 0} != remoto {remote_size}, vai retentar próxima iteração")
                erros += 1
        else:
            erros += 1
            log(f"    ERRO: {err}")
    _last_sizes.clear()
    _last_sizes.update(new_sizes)
    log(f"  Resumo: {novos} novos baixados, {pulados} já presentes, {em_escrita} aguardando estabilizar, {erros} erros")
    return novos, erros


def main():
    # Forca utf-8 no stdout pra nao quebrar com setas/emojis no Windows cp1252
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

    DEST.mkdir(parents=True, exist_ok=True)
    print("=" * 64)
    print("Auto-download: Network Volume RunPod -> PC")
    print(f"  Destino:  {DEST}")
    print(f"  SSH key:  {SSH_KEY}")
    print(f"  Interval: {INTERVAL}s")
    print("=" * 64)

    iteracao = 0
    while True:
        iteracao += 1
        log(f"=== Iteração #{iteracao} ===")
        pod = get_active_pod()
        if not pod:
            log("Nenhum pod RUNNING acessível. Aguardando próximo ciclo.")
        else:
            try:
                sync_once(pod)
            except Exception as e:
                log(f"  ERRO: {e}")
        log(f"Próxima em {INTERVAL}s...")
        time.sleep(INTERVAL)


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        print("\n[CTRL+C] Encerrado.")
        sys.exit(0)
