# Setup 3 Pods RunPod Paralelo — Render Worker

> Este doc é o passo-a-passo pra subir 3 pods 4090 RunPod renderizando em paralelo,
> consumindo a fila de jobs do servidor VPS (`85.239.243.215:8500`).
>
> **Abordagem**: bootstrap on-boot (sem registry Docker custom). O pod usa imagem
> oficial RunPod PyTorch e na inicialização baixa um script do VPS que instala
> ffmpeg + deps + worker Python. Boot ~3min, depois é só rodar.

## Arquitetura

```
[VPS Contabo 85.239.243.215]
  ├── porta 8500: app.py (UI + render_queue + job claim API)
  └── porta 8502: bootstrap server (serve bootstrap.sh + render-worker.tar.gz)
       │
       └── pollam GET /api/render-worker/next-job a cada 5s
            │
        ┌───┴───────┬───────────┬───────────┐
        ▼           ▼           ▼           ▼
    [Pod #1]   [Pod #2]    [Pod #3]   (até N pods, escalável)
    4090       4090        4090
        │           │           │
        └───┬───────┴───────────┘
            ▼
    [Network Volume RunPod 50GB → CA-MTL-1]
      /workspace/assets/Imagens/...     ← imagens Ken Burns (sincronizadas 1x)
      /workspace/assets/Video/...       ← CTA mp4 + overlays
      /workspace/assets/Music/...       ← trilhas sonoras
      /workspace/temp/                  ← scratch
      /workspace/cache/                 ← Ken Burns clips
      /workspace/exports/               ← MP4 finais (worker grava aqui)
```

Cada pod = **1 worker independente**. Workers pollam a VPS a cada 5s; quem chega
primeiro pega o próximo job. **Sem coordenação entre pods** — o lock está na VPS
em `proximo_job_remoto()`.

## Checklist do Piter

### 1. Criar Network Volume (~5min) — fazer agora

1. Acessar https://www.runpod.io/console/user/storage
2. Clicar **"Create Network Volume"**
3. Configurar:
   - **Name**: `automator-assets`
   - **Size**: 50 GB
   - **Region**: `CA-MTL-1` (mesma do benchmark anterior — CA Montreal)
4. **Custo**: $3.50/mês (~R$18/mês)

### 2. Sync dos assets pro Network Volume (~30min)

Quando o volume estiver criado:

1. **Cria 1 pod temporário** (qualquer GPU barata, ex: RTX A4000 $0.17/hr) com:
   - Image: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04` (ou similar)
   - Network Volume: anexar `automator-assets` em `/workspace`
   - SSH terminal: ✅ marcado
2. Pega IP + porta SSH do pod (painel RunPod mostra na aba SSH)
3. No teu PC, no Git Bash:
   ```bash
   bash docker/sync-assets.sh root@<pod-ip> <ssh-port>
   ```
   Ex: `bash docker/sync-assets.sh root@69.30.85.171 22177`
4. Sync de ~9GB demora 20-40min dependendo da banda
5. Mata o pod temporário (volume persiste)

O script `sync-assets.sh` envia:
- `F:/Canal Dark/Imagens/Chosen One/Estilo Chosen One/`
- `F:/Canal Dark/Imagens/Chosen One/Starseeds/`
- `F:/Canal Dark/Imagens/Chosen One/Frames/`
- `F:/Canal Dark/Video/*.mp4` (CTAs + overlays)
- `F:/Canal Dark/Music/*.mp3` + `*.wav` (trilhas sonoras)

### 3. Criar 3 pods 4090 de produção (~5min)

Quando assets estiverem sincronizados, na RunPod:

1. **New Pod → Custom Template**

2. **Image**: `runpod/pytorch:2.4.0-py3.11-cuda12.4.1-devel-ubuntu22.04`
   *(ou outra `runpod/pytorch:*-cuda12.*-devel-*`. Tem que ser **devel**, não runtime, pra ter ferramentas de build se algum pip wheel falhar.)*

3. **GPU**: RTX 4090 (Community tier, $0.34/hr)

4. **Network Volume**: anexar `automator-assets` em mount path `/workspace`

5. **Container disk**: 30 GB (sobra espaço pra apt deps + pip cache)

6. **Docker Start Command** (override do default):
   ```
   bash -c "curl -fsSL http://85.239.243.215:8502/bootstrap.sh | bash"
   ```

7. **Environment Variables** (clica "+ Add Env Variable"):
   ```
   WORKER_TOKEN = 51dd755eb34a2cbdec24917a3afbb236f8b63059e286d0d1aeb8aae633cfabb7
   VPS_URL      = http://85.239.243.215:8500
   ```

8. SSH terminal access: ✅ (útil pra debug)

9. **Deploy → repete 3x** (3 pods iguais)

Cada pod sobe em ~3 min:
- ~30s: download imagem PyTorch RunPod (cached entre pods da mesma região)
- ~60s: apt-get install ffmpeg + fontes
- ~30s: pip install faster-whisper + opencv + httpx
- ~5s: baixar `render-worker.tar.gz` do VPS
- worker conecta no VPS e começa a pollar

### 4. Disparar produção

Quando os 3 pods estiverem rodando, você me avisa. Eu disparo:

```
POST /api/producao-completa/iniciar { data_idx: <linha 09/05>, loop: true }
```

E os 3 pods começam a renderizar paralelo. Pra você é transparente — só
vê em http://85.239.243.215:8500/v2/monitor os 3 workers consumindo jobs.

## Atualizar o código do worker (sem rebuild)

Quando eu modifico render_worker.py / engine.py / subtitle_fixer.py / etc:

1. Faço push pro VPS (já tá em `/opt/video-automator/`)
2. Rodo no VPS:
   ```
   bash /opt/video-automator/docker/refresh-bundle.sh
   ```
3. Pods novos pegam a versão atualizada. Pods antigos precisam ser
   reiniciados na RunPod pra puxar a versão nova.

## Variáveis de ambiente — referência completa

A bootstrap aceita estes envs (todos opcionais, com defaults):

```bash
WORKER_TOKEN          # OBRIGATÓRIO - Bearer token pro worker autenticar na VPS
VPS_URL               # default: http://85.239.243.215:8500
BUNDLE_URL            # default: http://85.239.243.215:8502/render-worker.tar.gz
POLL_INTERVAL         # default: 5  (segundos entre polls)
TEMP_DIR              # default: /workspace/temp
CACHE_DIR             # default: /workspace/cache  (Ken Burns clips)
EXPORT_BASE           # default: /workspace/exports  (MP4 finais)
ASSETS_BASE_REMAP     # default: "F:/Canal Dark=/workspace/assets|F:\\Canal Dark=/workspace/assets"
                      # remapeia paths Windows do template pra paths Linux do volume
```

## Troubleshooting

**Pod não pega jobs**: verifica logs do pod. Se aparecer "VPS nao respondeu /api/health"
no bootstrap, a VPS está fora ou o WORKER_TOKEN está errado.

**Render falha "file not found"**: ASSETS_BASE_REMAP não está mapeando algum path
do template. Acessa SSH do pod e roda:
```
ls /workspace/assets/Imagens/Chosen\ One/
```
Se vazio, refaz o sync com `sync-assets.sh`.

**FFmpeg sem NVENC**: a imagem PyTorch RunPod tem ffmpeg sem NVENC built-in
mesmo. Mas o engine usa NVENC via runtime CUDA do nvenc encoder, não via
ffmpeg compilation flag. Verifique com `ffmpeg -encoders | grep nvenc` —
deve listar `h264_nvenc` na pod com GPU.

**Pip install falha**: a imagem PyTorch já tem PyTorch + CUDA + Python 3.11.
Os deps que faltam são apenas faster-whisper, opencv-python-headless, httpx
e fontes/ffmpeg. Se algum wheel exigir build (raro), troca a imagem por
`runpod/pytorch:*-cuda12.*-devel-*`.

## Custo estimado

- **Network Volume 50GB**: $3.50/mês fixo
- **3 pods 4090 Community**: $0.34/hr × 3 = $1.02/hr enquanto rodando
- Pra produção típica de 30 vídeos/dia (~5h GPU/dia ÷ 3 paralelo = ~1.7h por
  pod), custo seria ~$1.70/dia ≈ **$50/mês** + $3.50 volume.

## Próximos passos (quando estiver tudo rodando)

- Auto-shutdown: VPS desliga pods após N min idle (Fase 2 backlog)
- Auto-scale: VPS sobe pods sob demanda (cron) e desliga depois
- Monitoring + alertas Telegram
- Backup S3 dos outputs

(documentado no backlog seção "FASE 2 — MIGRACAO 100% CLOUD-NATIVE")
