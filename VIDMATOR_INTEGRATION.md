# VidMator — Inteligência Completa da Integração (atualizado 2026-07-03)

**Status: EM PRODUÇÃO.** Canais EST, TTM e TTM2 produzem em série pelo fluxo natural do Automator
(validado pelo operador em 2026-07-02). DOC (documentário) plugado, aguardando voz clonada.

## 1. O que é

VidMator = motor de **edição superdinâmica** (Remotion/React + "Director" Python) plugado no Automator
como um **motor escolhível por template**, sem tocar na fábrica FFmpeg existente.

```
template.motor = "simples"  (default; engine.py/FFmpeg — canais espirituais legados, INTACTOS)
               | "vidmator" (Remotion/Director — EST, TTM, TTM2, DOC)
               | "hibrido"  (= vidmator; nome reservado p/ variações futuras)
```

O campo `motor` é lido **exclusivamente no `render_worker.py` local** (~linha 557). O código do VPS
(app.py/orchestrator) **não conhece `motor`** — por isso plugar um canal VidMator NÃO exige deploy:
basta template (dado via POST /api/templates) + worker local atualizado.

**Regra: 1 template POR CANAL** (`ttm_vidmator`, `ttm2_vidmator`, `est_vidmator`, `doc_vidmator`) —
o `video_nome` usa a TAG do template; template compartilhado = canais se sobrescrevem (lição #3).
Templates VidMator são específicos por NICHO (preset próprio, trilha própria, voz própria).

## 2. Fluxo ponta-a-ponta (por célula do grid)

1. **Roteiro** — orchestrator (VPS) roda a pipeline nativa da coluna via scriptwriter
   (`ttm-somatic`, `estoicismo-5passos`, `doc-historias` — todas `cli_claude`, $0).
   Fonte de verdade = `EXPORT_BASE/<YYYY-MM-DD>/Roteiros/<TAG>.txt` (≥5000 bytes → reaproveita).
2. **Narração** — worker local puxa `/api/narration-worker/next-job` → `narrator_chatterbox`
   (voz clonada por `narracao_voz.voice_ref`, GPU local) → upload mp3 pro VPS.
3. **Render** — worker puxa `/api/render-worker/next-job`; `motor in (vidmator,hibrido)` →
   `vidmator_render.render_vidmator(tmpl, narr_local, roteiro_local, video_path)`.
4. **Entrega** — MP4 em `EXPORT_BASE/<data>/Videos/<TAG>_<YYYYMMDD>_01.mp4`; validação/upload do
   worker idênticos ao motor simples.

Disparo seletivo: `POST /api/producao-completa/iniciar {data_idx, ordem:[cols], loop:false}` —
`ordem` limita às colunas listadas (outros canais intocados).

## 3. O bridge (`vidmator_render.py`) — anatomia

- **LOCK de serialização** (`teste/.vidmator_lock`, mutex via `os.mkdir` atômico, stale 3h): o Director
  usa UM workspace compartilhado (`roteiro_en.txt`, `narracao_joanne.mp3`, `timeline.json`) — 2 jobs
  vidmator em paralelo se corrompem (lição #4). Jobs simples NÃO passam pelo lock (seguem paralelos).
- Copia roteiro+mp3 pro workspace → whisper (venv Chatterbox/CUDA) → **14 passes** do Director (py3.14):
  `montar_timeline resolver_cascata epoca detectar_mapas pessoas datas topicos trilha efeitos fontes
  imagens ilustrar apresentar produto_cta`
- `preparar_render.py` (cwd=remotion) → `timeline_render.json`
- **GATE 4.5 (regra de NEGÓCIO)**: se `preset.produto_cta.ativo` e o timeline não tem a janela →
  **RuntimeError ANTES do render**. O CTA do eBook é o core business; falha ruidosa, nunca silêncio.
- Render `render-broll.mjs` em chunks resumíveis (RENDER_CHUNKS=6, retry 6×).
- **Cold-open typewriter** (gate `preset.cold_open`): `coldopen_quote.py` escolhe citação REAL a partir
  do roteiro (LLM; fallback fixo por nicho) → `render-comp.mjs TypewriterQuote` via env `COMP_PROPS`
  (JSON `{quote, author, cps}`) → concat `-c copy`. Nunca derruba o job se falhar.
- Env pro Director: `NICHO` (chave do preset), `MUSICA_PASTA` (template.trilha_pasta),
  `FONTE_IMAGENS`/`PASTA_IMAGENS_LOCAL` (modo híbrido).

⚠️ O worker **CACHEIA** o módulo `vidmator_render` (import lazy + sys.modules) — edits no bridge exigem
**restart dos 2 workers**. Os passes/resolver rodam como subprocess (sempre frescos — a EDIÇÃO em si é
hot-swap: mexer em preset/pass/composição vale no próximo job sem restart).

⚠️ A lista de PASSES existe em **3 cópias que andam juntas**: `producao.py` e `_prod_*.sh`
(drivers locais do track experimental) e `vidmator_render.PASSES` (este repo). Lição #2.

## 4. Inteligência de edição (Director) — onde mora

Código em `F:/Canal Dark/Aplicativo de Edição/banco-videos/teste` (passes Python) e `.../remotion`
(composições React) — **repo privado separado** (a inteligência de edição e a estratégia de produto
NÃO ficam neste repo público).

Capacidades por nicho (`teste/presets.json`, knob `nicho` do template):
- **ttm** (somático-espiritual): `video_frac 0.5` (balanceio vídeo/imagem uniforme via Bresenham) +
  `vet_relevancia` (Vision veta vídeo atmosférico "nada a ver" → troca por imagem, nunca IA);
  **banco híbrido local** (`fonte_imagens: hibrido` — nível `Lb-banco` na cascata do resolver: ~7k
  imagens próprias taggeadas por GPT Vision em `index_ttm_imagens.json`; match por score de tags vs
  beat, penalidade por reuso); **`produto_cta`** (takeover eBook+QR+LIMITED TIME sincronizado ao pitch
  FALADO — âncora por FRASE do nome do produto em words.json); cold-open; glitch OFF.
- **estoicismo**: calmo — glitch/riser/whoosh OFF (`glitch_topico:false`), fonte clean, cold-open,
  trilha mood-match por tópico (tense/dark/mysterious/somber/neutral).
- **documentario**: vintage/B&W (LightLeak/FilmBurn/TensionVignette), **banco de ÉPOCA** (clipes PD do
  Commons vetados por GPT Vision, `catalogo.json` no Drive, pass `epoca` mapeia beats STOCK → era),
  cards de pessoa (`pessoas`), DateStamp (`datas`), fontes serif/typewriter, `video_frac 0.6` + vet.

Cascata do resolver (por beat): L1 archive (off) → L2 Commons-identidade → L2t Commons-época →
**Lb banco local (híbrido)** → L4i Pexels foto → L3 Pexels vídeo → L5 fallback amplo.

## 5. Multi-BASE (coringa por grupo) — desde 2026-07-02

- Colunas base: `tipo:"coringa"` + `grupo` (`geral` = BASE espiritual legada @idx 0; `estoicismo` =
  BASE-EST no FIM do grid). Novas bases de nicho = append no fim (sem shift de células).
- Itens do Backlog têm `grupo` (dropdown na UI de adicionar; `POST /api/backlog {grupo}`).
- Distribuição: cada base distribui SÓ pros canais com `coringa_grupo` correspondente
  (`atualizar_config_canal` aceita `grupo`). CO*/NARC/NPD = Fase 3, só grupo geral. Sem vazamento.
- Arquivos: `coringa_distribuidor.py` (garantir/distribuir/processar por grupo),
  `backlog_temas_db.py` (campo grupo + migration), `app.py` (endpoint + dropdown).

## 6. Runbook de operação

| Situação | Ação |
|---|---|
| Produzir colunas específicas | `POST /api/producao-completa/iniciar {data_idx, ordem:[cols], loop:false}` |
| "Ja renderizado" indevido | VPS `video_log_db.json`: `vids['YYYY-MM-DD__TAG']['render']={}` + `POST /api/producao-completa/reset` |
| Re-render limpo | apagar MP4 + chunks `remotion/out/_chunks/<TAG>_*.part*` (render-broll reusa por existsSync) |
| Editei o bridge | **restart dos 2 workers** (processos `render_worker.py`) |
| Mudei o grid estruturalmente | pedir **F5 na aba do Automator** + re-VERIFICAR (GET: nº colunas/template_id) antes de produzir — a SPA salva o grid INTEIRO; aba stale CLOBBERA mudanças (lição #5) |
| Disco enchendo | `remotion/_tmp` auto-limpa (>3h) nos renderers; chunks órfãos e caches podáveis |
| Deploy VPS | `bash deploy.sh <arquivos>` (SEM arg "prod" — o script não faz shift dos args) ou scp manual + `systemctl restart video-automator`. NUNCA com `producao_ativa:true` |
| Entrega de vídeo TTM | verificar frame do pitch falado (eBook+QR visível) ANTES de entregar — regra de negócio |
| Vision em lote | usar GPT (`gpt-4o-mini`, detail:low ~$0.0005/img) — Gemini free = 20 req/min, 429 em massa vira rejeição silenciosa |

## 7. Registro de lições (bugs reais do 1º ciclo e2e, todos corrigidos com trava)

1. **Roteiro download 404**: path VPS (Linux) montado com `Path()` no Windows → backslashes → 404 no
   `/api/render-worker/download`. Fix: `PurePosixPath` (render_worker ~linha 590).
2. **Pass fora da lista**: `produto_cta` faltava no PASSES do bridge → vídeo publicável SEM o CTA do
   eBook, em silêncio. Fix: 3 gates independentes (pass `exit(1)` se produto não falado / gate 4.5 no
   bridge / gate no driver local) — qualquer um aborta ruidosamente.
3. **Colisão de nomes**: `video_nome` usa a TAG DO TEMPLATE → 2 canais no mesmo template se
   sobrescrevem E reusam chunks um do outro (vídeo-quimera). Fix: 1 template por canal.
4. **Race de workspace**: 2 workers rodaram 2 jobs vidmator simultâneos no MESMO workspace do Director.
   Fix: lock de serialização (seção 3).
5. **Clobber por UI stale**: save da SPA com aba antiga apagou colunas/configs novas do grid.
   Mitigação: runbook (F5 + re-verificar). Fix definitivo (backlog): merge server-side/versionamento.
6. **Artefatos stale nos drivers locais**: `[ -f arquivo ]` satisfeito por narração/chunk de OUTRO
   run → vídeo velho entregue como novo. Fix: purga por-run no início dos drivers.

## 8. Cadeia de arquivos (este repo)

- `render_worker.py` — branch do motor, narração Chatterbox local, download de roteiro (PurePosixPath)
- `vidmator_render.py` — bridge completo (lock, 14 passes, gate de negócio, cold-open)
- `coringa_distribuidor.py` / `backlog_temas_db.py` / `app.py` — multi-base por grupo
- Pipelines/templates/colunas são DADOS no VPS (POST /api/pipelines, /api/templates, /api/temas)
