# 🎼 Operação Multi-Claude — Video Automator

> **TL;DR:** O Automator é operado por 4 Claudes simultâneos com escopos exclusivos. Esta é a referência rápida local. A versão canônica vive no [Notion](https://www.notion.so/35b770cbb8de8186846ceb8daace2ae6).

## Os 4 Claudes

| Claude | Pasta | Identidade | Branch | Notion |
|---|---|---|---|---|
| 🎩 **Maestro** | `video-automator/` | `CLAUDE-MAESTRO.md` | qualquer (read) | [link](https://www.notion.so/35b770cbb8de81b08d76f603ad3f9cf2) |
| 🟢 **PROD** | `video-automator/` | `CLAUDE-PROD.md` | `master` | [link](https://www.notion.so/35b770cbb8de8179b095cf0a9e68c9a3) |
| 🔵 **DEV** | `automator-dev/` (worktree git) | `CLAUDE-DEV.md` | `develop` | [link](https://www.notion.so/35b770cbb8de81979939c949238ff125) |
| 🟡 **OBS** | `video-automator/` (mesma pasta, identidade distinta) | `CLAUDE-OBS.md` | read-only em `master` | [link](https://www.notion.so/35b770cbb8de81a0aadcea7911e19f39) |

## Mapa de escopo (quem escreve em quê)

| Recurso | Owner | Outros podem editar? |
|---|---|---|
| `temas.json` | PROD | Maestro (raro, com lock) |
| `production_state.json`, `historico.json` | PROD (auto) | — |
| `BACKLOG.txt` (marca [x]) | DEV/PROD após deploy | — |
| `BACKLOG.txt` (acrescentar `[OBS]`) | OBS | — |
| `BACKLOG.txt` (rearranjo prioridade) | Maestro | — |
| Código (`.py`, `.jsx`, `.css`) | DEV | — |
| Branch `master` | PROD (merge develop→master) | — |
| Branch `develop` | DEV | — |
| `templates.json`, `pipelines.json` | DEV | PROD com lock (emergência) |
| `CLAUDE-*.md` | Maestro | — |
| `.claude-lock.json` | todos (com regras) | — |
| RunPod API | PROD | — |
| `systemctl restart` | PROD | — |
| `logs/monitor-*.jsonl` | OBS | — |
| Notion páginas | quem é dono daquela página | — |

## Como Piter abre cada sessão

```bash
# Terminal 1 — Maestro
cd "F:\Canal Dark\Aplicativo de Edição\video-automator"
claude
# Cola: ver "Prompt de onboarding" em CLAUDE-MAESTRO.md

# Terminal 2 — PROD
cd "F:\Canal Dark\Aplicativo de Edição\video-automator"
claude
# Cola: ver "Prompt de onboarding" em CLAUDE-PROD.md

# Terminal 3 — DEV
cd "F:\Canal Dark\Aplicativo de Edição\automator-dev"
claude
# Cola: ver "Prompt de onboarding" em CLAUDE-DEV.md

# Terminal 4 — OBS (mesma pasta, identidade distinta)
cd "F:\Canal Dark\Aplicativo de Edição\video-automator"
claude
# Cola: ver "Prompt de onboarding" em CLAUDE-OBS.md
```

Os prompts de onboarding prontos (copiar-colar) estão em `MULTI-CLAUDE-ONBOARDING.md`.

## Setup já feito (2026-05-09)

```
Branch develop criada apontando pra master HEAD (377baac)
Worktree DEV: F:\Canal Dark\Aplicativo de Edição\automator-dev\ (branch develop)

OBS NÃO tem worktree próprio. Usa a mesma pasta video-automator/ que Maestro/PROD,
distinguido pela identidade (CLAUDE-OBS.md) e regra read-only.
Motivo: arquivos que OBS lê (historico.json, logs/, production_state.json) são
gitignored, então worktree separado seria pior — OBS perderia acesso a eles.
```

**Atenção:** o worktree `automator-dev/` foi criado a partir do master HEAD em `377baac`. Arquivos **modificados** ou **untracked** na pasta `video-automator/` (incluindo `frontend2/`, `pods_manager.py`, `coringa_distribuidor.py`, etc.) **não estão no worktree DEV**. Se DEV precisa deles para implementar uma feature:
1. Commitar no master primeiro (PROD/Maestro decide quando)
2. DEV faz `git pull origin master` no worktree
3. OU copiar arquivos manualmente para `automator-dev/`

## Lockfile

`.claude-lock.json` na raiz. Schema completo em `CLAUDE-MAESTRO.md` ou no Notion.

```json
{
  "locks": [
    {
      "claude": "PROD",
      "resource": "temas.json",
      "action": "reset canal CON 09/05",
      "acquired_at": "2026-05-09T14:32:11",
      "ttl_seconds": 300
    }
  ],
  "updated_at": "2026-05-09T14:32:11"
}
```

**Regras:**
1. TTL máximo: 600s
2. Read sempre livre, write precisa lock
3. Lock de outro Claude = espera ou conversa via Maestro/Piter
4. Lock expirado = stale, próximo Claude pode quebrar (mas registra incidente)

## Fluxo de deploy DEV → PROD

```
DEV implementa em develop
   ↓
DEV abre página Notion "Pronto pra deploy: [feature]"
   ↓
Piter avisa PROD ("vai lá merge isso")
   OU Maestro arbitra se houver dúvida
   ↓
PROD lê página, valida checklist (12 passos em CLAUDE-PROD.md)
   ↓
PROD: git checkout main && git merge develop
PROD: ./deploy.sh [arquivos]
PROD: smoke test
   ↓
PROD registra em "📜 Histórico de Deploys" (Notion)
DEV marca [x] no BACKLOG.txt
```

## Escalação — quando chamar quem

| Situação | Quem você chama |
|---|---|
| Custo do dia > $10 | PROD → Piter |
| Vídeo corrompido após retries | PROD → Piter |
| Item ambíguo no backlog | DEV → Piter |
| Mudança arquitetural grande | DEV → Maestro → Piter |
| Métrica anômala >30% | OBS → Piter (ou Maestro se urgente) |
| Conflito de lockfile entre 2 Claudes | qualquer → Maestro |
| Onboardar Claude novo | Piter → Maestro |
| Decisão estratégica de produto | qualquer → Maestro → Piter |

## Notion IDs

- **Hub Canal Dark:** `353770cb-b8de-807a-9bb8-f333596477f5`
- **Video Automator:** `354770cb-b8de-81f2-a2b5-c6cf0ef72b8c`
- **🎼 Operação Multi-Claude:** `35b770cb-b8de-8186-846c-eb8daace2ae6`
- 🎩 Maestro: `35b770cb-b8de-81b0-8d76-f603ad3f9cf2`
- 🟢 PROD: `35b770cb-b8de-8179-b095-cf0a9e68c9a3`
- 🔵 DEV: `35b770cb-b8de-8197-9939-c949238ff125`
- 🟡 OBS: `35b770cb-b8de-81a0-aadc-ea7911e19f39`
- 📋 Status & Lockfile: `35b770cb-b8de-8161-8a7d-eefbcd1001c9`
- 📜 Histórico de Deploys: `35b770cb-b8de-816f-97d1-cd2422193b57`
- 🚨 Incidentes: `35b770cb-b8de-810d-a15e-fe9e2a0dfb64`
