"""Esteira pos-render — task-store em disco (formato PUXADO, regra Piter 30/07).

Sequencia por tema:  render -> THUMB (fila, 1 por vez) -> UPLOAD (paralelo entre
canais, serial dentro do canal) -> PIN (fila global, AdsPower) -> done.

Este modulo e' so a FILA: 1 arquivo JSON por (alias, data) em _esteira/tarefas/.
Quem consome e' o esteira_worker.py. O render_worker so ENFILEIRA (write de um
JSON) e volta pro proximo job — hoje ele fica ~15min parado esperando
thumb+upload inline.

REGRAS DE OURO (Piter 30/07):
  - THUMB NUNCA BLOQUEIA: falhou -> thumb_status='pendente' e o tema SEGUE pro
    upload sem thumb. O retry e' do upload_daily_check.
  - PIN NUNCA BLOQUEIA: falhou -> pin_status='pendente', video continua
    agendado. Retry idem.

Por que arquivos e nao fila em memoria: sobrevive a reboot/crash, e os
produtores (4 render workers) sao PROCESSOS separados. Writes atomicos
(tmp+os.replace) como todo o resto do Automator.

⚠️ _esteira/ contem titulos de videos nao publicados -> .gitignore (repo publico).
"""
import json
import os
import threading
import time
from datetime import datetime
from pathlib import Path

DIR = Path(__file__).parent / "_esteira"
TAREFAS = DIR / "tarefas"
FEITAS = DIR / "feitas"          # tarefas done movem pra ca (auditoria, 30 dias)
HEARTBEAT = DIR / "worker_heartbeat.txt"
FLOW_PROJETOS = DIR / "flow_projects.json"   # data_iso -> proj_id (projeto por DATA)

# Etapas em ordem. 'falha' = dead-letter (upload falhou de verdade; thumb/pin
# pendentes NAO sao falha — o tema termina 'done' com a pendencia anotada).
ETAPAS = ("thumb", "upload", "pin", "done", "falha")

_lock = threading.Lock()


def _log(m):
    print(f"[esteira] {m}", flush=True)


def _path(alias: str, data_iso: str) -> Path:
    return TAREFAS / f"{alias.upper()}_{data_iso}.json"


def _retry_win(fn, tentativas: int = 40, espera: float = 0.05):
    """Windows: unlink/replace falham com WinError 32 se OUTRO thread estiver
    com o arquivo aberto pra leitura naquele instante — e os loops da esteira
    escaneiam as tarefas o tempo todo. Leituras duram milissegundos; insistir
    ~2s resolve na pratica. Levanta a ultima excecao se nao resolver."""
    ultima = None
    for _ in range(tentativas):
        try:
            return fn()
        except PermissionError as e:
            ultima = e
            time.sleep(espera)
    raise ultima


def _write_atomic(path: Path, obj: dict):
    path.parent.mkdir(parents=True, exist_ok=True)
    tmp = path.with_suffix(f".tmp{os.getpid()}")
    tmp.write_text(json.dumps(obj, ensure_ascii=False, indent=1), encoding="utf-8")
    _retry_win(lambda: os.replace(str(tmp), str(path)))


def carregar(alias: str, data_iso: str) -> dict | None:
    for base in (TAREFAS, FEITAS):
        p = base / f"{alias.upper()}_{data_iso}.json"
        if p.exists():
            try:
                return json.loads(p.read_text(encoding="utf-8"))
            except Exception as e:
                _log(f"tarefa ilegivel {p.name}: {type(e).__name__}")
    return None


def salvar(task: dict):
    """Persiste a tarefa. etapa='done'/'falha' move pra feitas/ (tira da fila)."""
    alias, data_iso = task["alias"], task["data"]
    task["atualizado"] = datetime.now().isoformat(timespec="seconds")
    with _lock:
        ativo = _path(alias, data_iso)
        if task.get("etapa") in ("done", "falha"):
            _write_atomic(FEITAS / ativo.name, task)
            try:
                _retry_win(lambda: ativo.unlink(missing_ok=True))
            except PermissionError:
                # leitor persistente segurou o arquivo por >2s (raro). O
                # registro em feitas/ ja existe -> pendentes() detecta o
                # fantasma pelo 'criado' igual e se auto-cura na proxima.
                _log(f"unlink de {ativo.name} bloqueado — feitas/ ja tem o registro")
        else:
            _write_atomic(ativo, task)


def enfileirar(alias: str, data_iso: str, video_path: str, titulo: str = "") -> dict:
    """Cria (ou retoma) a tarefa do tema. Idempotente:
    - ja 'done'  -> nao recria (video ja subiu; re-render nao re-sobe sozinho)
    - em andamento -> so atualiza o video_path (re-render substitui o MP4)
    """
    alias = alias.upper()
    existente = carregar(alias, data_iso)
    if existente and existente.get("etapa") == "done":
        _log(f"{alias} {data_iso}: ja done — nao re-enfileiro")
        return existente
    if existente and existente.get("etapa") not in ("falha", None):
        existente["video_path"] = video_path
        salvar(existente)
        _log(f"{alias} {data_iso}: ja na fila (etapa={existente['etapa']}) — video_path atualizado")
        return existente
    task = {
        "alias": alias, "data": data_iso, "video_path": video_path,
        "titulo": titulo or "", "etapa": "thumb",
        "thumb_path": "", "thumb_status": "", "pin_status": "",
        "video_id": "", "tentativas": {}, "erro": "",
        "criado": datetime.now().isoformat(timespec="seconds"),
    }
    salvar(task)
    _log(f"{alias} {data_iso}: ENFILEIRADO (etapa=thumb)")
    return task


def pendentes(etapa: str | None = None) -> list[dict]:
    """Tarefas ativas, mais antigas primeiro (FIFO real da esteira).

    AUTO-CURA de fantasma (WinError 32): se o unlink do salvar() ficou
    bloqueado por um leitor, sobra em tarefas/ uma copia velha de tarefa que
    JA terminou (feitas/ tem registro com o MESMO 'criado'). Aqui o fantasma
    e' filtrado E removido — nunca volta pra fila."""
    if not TAREFAS.exists():
        return []
    out = []
    for p in TAREFAS.glob("*.json"):
        try:
            t = json.loads(p.read_text(encoding="utf-8"))
        except Exception:
            continue
        feita = FEITAS / p.name
        if feita.exists():
            try:
                f = json.loads(feita.read_text(encoding="utf-8"))
                if f.get("criado") == t.get("criado"):
                    p.unlink(missing_ok=True)     # fantasma — limpa e pula
                    continue
            except (OSError, json.JSONDecodeError):
                pass
        out.append(t)
    if etapa:
        out = [t for t in out if t.get("etapa") == etapa]
    out.sort(key=lambda t: t.get("criado") or "")
    return out


# ------------------------------------------------------------ heartbeat
def bater_coracao():
    """esteira_worker chama a cada ciclo. E' o que o render_worker olha pra
    decidir entre enfileirar (worker vivo) ou cair no fluxo inline legado."""
    try:
        DIR.mkdir(parents=True, exist_ok=True)
        HEARTBEAT.write_text(str(time.time()), encoding="utf-8")
    except OSError:
        pass


def worker_vivo(max_idade_seg: int = 600) -> bool:
    try:
        return (time.time() - float(HEARTBEAT.read_text().strip())) < max_idade_seg
    except (OSError, ValueError):
        return False


# ------------------------------------------------------------ projeto Flow por DATA
def flow_proj_da_data(data_iso: str) -> str:
    try:
        d = json.loads(FLOW_PROJETOS.read_text(encoding="utf-8"))
        return d.get(data_iso) or ""
    except (OSError, json.JSONDecodeError):
        return ""


def flow_proj_gravar(data_iso: str, proj_id: str):
    """Registra o projeto da data. proj_id vazio APAGA o registro (projeto
    quebrado/apagado no Flow -> proxima geracao do dia cria um novo)."""
    with _lock:
        try:
            d = json.loads(FLOW_PROJETOS.read_text(encoding="utf-8"))
        except (OSError, json.JSONDecodeError):
            d = {}
        if proj_id:
            if d.get(data_iso) == proj_id:
                return
            d[data_iso] = proj_id
            _log(f"projeto Flow da data {data_iso}: {proj_id[:12]}…")
        else:
            if data_iso not in d:
                return
            d.pop(data_iso, None)
            _log(f"projeto Flow da data {data_iso}: registro APAGADO (projeto quebrado)")
        _write_atomic(FLOW_PROJETOS, d)


def limpar_feitas(dias: int = 30):
    """Poda auditoria antiga (chamado pelo worker 1x/dia)."""
    if not FEITAS.exists():
        return
    corte = time.time() - dias * 86400
    for p in FEITAS.glob("*.json"):
        try:
            if p.stat().st_mtime < corte:
                p.unlink()
        except OSError:
            pass
