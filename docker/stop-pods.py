"""
Para e (opcional) deleta TODOS os pods da conta RunPod.

Uso:
    python docker/stop-pods.py             # lista e pede confirmação
    python docker/stop-pods.py --yes       # sem confirmação (CI)
    python docker/stop-pods.py --delete    # deleta em vez de parar
"""
import json, os, sys, urllib.request

API_KEY = os.environ.get("RUNPOD_API_KEY", "")
if not API_KEY:
    raise RuntimeError("RUNPOD_API_KEY env var obrigatoria (sem fallback hardcoded por seguranca)")
YES = "--yes" in sys.argv or "-y" in sys.argv
DELETE = "--delete" in sys.argv

def get(path, method="GET"):
    req = urllib.request.Request(f"https://rest.runpod.io/v1{path}",
        headers={"Authorization": f"Bearer {API_KEY}"}, method=method)
    with urllib.request.urlopen(req, timeout=15) as resp:
        ct = resp.headers.get("content-type","")
        body = resp.read().decode()
        return json.loads(body) if ct.startswith("application/json") and body else None

pods_resp = get("/pods")
pods = pods_resp if isinstance(pods_resp, list) else pods_resp.get("data", [])
print(f"Total pods: {len(pods)}")
for p in pods:
    print(f"  [{p.get('desiredStatus','?'):8s}] {p.get('name','?'):20s}  {p.get('id')}  ${p.get('costPerHr')}/hr")

if not pods:
    sys.exit(0)

action = "DELETAR" if DELETE else "PARAR"
if not YES:
    r = input(f"\n{action} todos os {len(pods)} pods? [y/N] ").strip().lower()
    if r not in ("y", "yes", "s", "sim"):
        sys.exit("Cancelado.")

ok, fail = 0, 0
for p in pods:
    pid = p["id"]
    try:
        if DELETE:
            get(f"/pods/{pid}", method="DELETE")
        else:
            # POST /pods/{id}/stop não existe na REST atual; usa GraphQL
            req = urllib.request.Request(
                "https://api.runpod.io/graphql",
                data=json.dumps({"query": f'mutation {{ podStop(input: {{podId: "{pid}"}}) {{ id desiredStatus }} }}'}).encode(),
                headers={"Authorization": f"Bearer {API_KEY}", "Content-Type": "application/json", "User-Agent":"curl/8.0.1"}
            )
            urllib.request.urlopen(req, timeout=15).read()
        print(f"  {action} {p.get('name')}: OK")
        ok += 1
    except Exception as e:
        print(f"  {action} {p.get('name')}: FALHOU ({e})")
        fail += 1

print(f"\n{ok}/{ok+fail} sucesso.")
sys.exit(0 if fail == 0 else 1)
