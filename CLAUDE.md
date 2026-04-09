# Video Automator - Technical Intelligence File

## What It Is

Local automation tool that replaces Adobe Premiere Pro for YouTube video production. Produces 12+ videos/day across 6+ channels using Python + FFmpeg. Operator (Piter) runs channels in EN, DE, PT, ES covering spiritual content (Chosen One / Starseed niches). The goal is 100% automation: from script generation through narration, subtitle, video assembly, and (future) upload.

## Architecture

**Single-file SPA**: FastAPI backend (`app.py`, ~5800 lines) serves an inline HTML/JS dashboard via a `DASHBOARD_HTML` string. No separate frontend build. All JavaScript lives inside Python string literals in `app.py`. Server runs on port 8500.

**Backend modules**:
- `engine.py` (~780 lines) - Video assembly engine (OpenCV + FFmpeg)
- `transcriber.py` - Whisper audio-to-SRT transcription
- `subtitle_fixer.py` - SRT correction rules engine
- `scriptwriter.py` (~420 lines) - Multi-step LLM pipeline executor + credential system + Supabase/Sheets sync
- `narrator.py` (~340 lines) - TTS via ai33.pro API (ElevenLabs + Minimax)

**Data files** (all JSON, no database):
- `templates.json` - Template configurations (includes narracao_voz, moldura, etc.)
- `pipelines.json` - LLM pipeline definitions
- `credentials.json` - API credentials (Claude, GPT, Gemini) - multiple keys per provider
- `config.json` - App settings (ai33_api_key, supabase, sheets sync)
- `temas.json` - Temas grid data (central production hub)
- `historico.json` - Production history (max 500 entries)

**Directories**:
```
video-automator/
  app.py              # FastAPI server + inline SPA (DASHBOARD_HTML)
  engine.py           # Video engine (OpenCV clips + FFmpeg assembly)
  transcriber.py      # Whisper transcription (faster-whisper + fallback)
  subtitle_fixer.py   # SRT correction (rules, line breaking, hesitations)
  scriptwriter.py     # LLM pipelines (Claude/GPT/Gemini) + credentials
  narrator.py         # TTS via ai33.pro
  templates.json      # Template configs
  config.json         # App config (ai33_api_key, sync settings)
  credentials.json    # LLM API keys (multiple per provider)
  pipelines.json      # LLM pipeline definitions
  temas.json          # Temas grid state
  historico.json      # Production history
  BACKLOG.txt         # Full backlog with done/pending items
  rules/              # Subtitle correction rules per language
    en.json, de.json, pt.json, es.json
  agents/
    temas/
      CLAUDE.md       # Agent instructions for tema generation
      historico.json  # Chat conversation history (max 100 messages)
  cache/              # Cached Ken Burns clips (MD5-keyed .mp4)
  temp/               # Temporary files (SRT, ASS, filter scripts)
  narracoes/          # Generated narration audio files
  scripts/            # Generated script text files
  logs/               # Persistent pipeline execution logs
  static/             # (empty - frontend is inline)
```

## Tech Stack

- **Python 3.13+** with FastAPI + uvicorn
- **FFmpeg** - Video assembly, audio mixing, subtitle burning, overlay compositing
- **OpenCV (cv2)** - Ken Burns effect rendering (warpAffine, subpixel interpolation)
- **NumPy** - Frame coordinate math
- **Pillow** - Image processing (referenced in engine design)
- **faster-whisper** - GPU-accelerated audio transcription (CTranslate2)
- **httpx** - HTTP client for all external APIs (LLMs, ai33.pro)
- **psutil** - Process priority management (fallback: ctypes/kernel32)

## UI Sidebar Order

Follows the production flow:
1. **Temas** (central hub - grid with channels x dates)
2. **Roteiros** (script generation via LLM pipelines)
3. **Narracao** (TTS narration batch)
4. **Templates** (video template configuration)
5. **Producao** (batch video production)
6. **Historico** (production history log)
7. **Config** (API keys, sync settings)

Theme: dark background with green accent color.

---

## How the Temas Grid Works

The Temas grid is the **central production hub** - a Notion-style grid organizing all video production.

### Structure
```json
{
  "colunas": [
    {
      "nome": "Canal EN",
      "pipeline_id": "pipe_abc",
      "template_id": "tmpl_xyz",
      "voice_id": "voice_123",
      "voice_provider": "minimax_clone"
    }
  ],
  "linhas": [
    {
      "data": "08/04/2026"
    }
  ],
  "celulas": {
    "0_0": {
      "tema": "The 7 Signs You're Chosen",
      "titulo": "7 Signs You Are Chosen By God",
      "thumb": "path/to/thumb.jpg",
      "roteiro": "Full script text...",
      "pipeline_id": "override_pipeline",
      "synced": true
    }
  }
}
```

### Key concepts:
- **Columns = Channels**: Each column represents a YouTube channel with its own pipeline, template, and voice config
- **Rows = Dates**: Each row is a production date (DD/MM/YYYY format)
- **Cells**: Contain tema (theme), titulo (title), thumb (thumbnail path), roteiro (script text)
- **Cell key format**: `{rowIndex}_{colIndex}` (e.g., "0_0", "1_2")
- **Column config**: `pipeline_id`, `template_id`, `voice_id`, `voice_provider` - inherited by all cells in that column
- **Cell override**: Cell can have its own `pipeline_id` that overrides the column's

### Features:
- **Drag & drop**: Columns and rows are reorderable
- **Replicate function**: Copy a cell field (tema, titulo, thumb) to other cells across dates/channels. "Replicar Tudo" copies all fields at once. Modal lets you pick destination dates and channels.
- **Undo**: "Desfazer" button restores previous grid state (hidden until an undo is available)
- **Date-based rows**: Rows are organized by date, used for production scheduling
- **Supabase sync**: On save, cells with titulo that haven't been synced get pushed to Supabase

### Variables available from Temas grid:
When running pipelines from Temas, these variables are injected:
- `{{tema}}` - Cell's tema field
- `{{titulo}}` - Cell's titulo field
- `{{thumb}}` - Cell's thumb field
- `{{canal}}` - Column's nome (channel name)
- `{{data}}` - Row's data (date)

---

## How Pipelines Work (scriptwriter.py)

Multi-step LLM chains for script generation.

### Pipeline Step Types

Each step (`etapa`) has a `tipo` field:

1. **`llm`** (default) - Calls an LLM API (Claude/GPT/Gemini)
   - Requires: `credencial` (reference to credentials.json), `modelo`, `system_message`, `prompt`
   - Calls the appropriate API based on the credential's provider

2. **`texto`** (Text) - Static text with variable substitution
   - Only uses `prompt` field - substitutes variables and outputs the result
   - No API call, instant execution
   - Useful for formatting, combining outputs, building final templates

3. **`code`** (Code) - Python execution
   - Executes the `prompt` field as Python code
   - Has access to: `entrada`, `saida_anterior`, `roteiro_atual`, `variaveis` dict
   - Built-in functions: `len`, `str`, `int`, `float`, `re` module
   - Must set `resultado` variable for output
   - Useful for text manipulation, counting, validation

### Pipeline Variables

Variable substitution in `system_message` and `prompt` fields uses regex with space tolerance (`{{ chave }}` and `{{chave}}` both work):

- `{{entrada}}` - Original user input (the tema text)
- `{{tema}}` - Alias for entrada
- `{{saida_anterior}}` - Output of the immediately previous step
- `{{saida_etapa_N}}` - Output of step N (1-indexed, e.g., `{{saida_etapa_3}}`)
- `{{roteiro_atual}}` - Latest completed output (same as saida_anterior for most cases)
- `{{canal}}` - Channel name (from Temas grid context)
- `{{data}}` - Date (from Temas grid context)
- `{{titulo}}` - Title (from Temas grid context)
- `{{thumb}}` - Thumbnail path (from Temas grid context)

### Pipeline Testing

Two testing modes available in the UI:

1. **Test individual step** (`POST /api/pipelines/testar-etapa`): Tests a single step with provided input. All Temas variables are set to test defaults.

2. **Test until here / Chain test** (`POST /api/pipelines/testar-cadeia`): Runs all steps sequentially up to a chosen step. Each step's output feeds into the next. Returns results array with status per step.

### Pipeline Execution

- Sequential execution, each step calls the appropriate handler
- State tracked per-step: `aguardando` -> `processando` -> `concluido`/`erro`/`cancelado`
- Cancellation supported mid-pipeline
- Final output = last completed step's result
- Result saved to `scripts/{pipeline_name}_{YYYYMMDD_HHMMSS}.txt`

### Pipeline Queue

When running from "Produzir Tudo", the frontend waits for the previous pipeline execution to finish before starting the next one. It polls `GET /api/pipelines/execucao` up to 30 times (2s intervals) to check if `ativo` is false. This prevents 409 conflicts from concurrent pipeline executions.

### Persistent Logs

After each pipeline execution, a log file is saved to `logs/pipeline_{id}_{timestamp}.log` containing:
- Pipeline ID and timestamp
- Per-step: name, status, result character count
- Error details if any
- Final result character count

### LLM API calls:
- **Claude**: `POST api.anthropic.com/v1/messages` (max_tokens=32000, timeout=300s)
- **GPT**: `POST api.openai.com/v1/chat/completions` (max_tokens=32000, timeout=300s)
- **Gemini**: `POST generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` (maxOutputTokens=32000, timeout=300s)

---

## How the Video Engine Works (engine.py)

### Two-pass rendering (VideoEngine.montar):

**Pass 1: Pre-render Ken Burns clips (0-60% progress)**
- Lists background images from template's `pasta_imagens`
- Calculates how many clips needed based on narration duration / `duracao_por_imagem`
- Shuffles image order, expands list to fill duration
- Alternates effects from pool: `zoom_in`, `zoom_out`, `pan_left`, `pan_right`
- For each image:
  - Check cache by MD5 hash of `{path}|{dur}|{WxH}|{fps}|{zoom_ratio}|{effect}`
  - If not cached: load with OpenCV, scale to `zoom_ratio * output` size using INTER_LANCZOS4
  - Pre-calculate crop boxes per frame with ease-in-out cubic: `t * t * (3.0 - 2.0 * t)`
  - Pipe raw BGR24 frames to FFmpeg via stdin (subprocess PIPE)
  - Each frame: `cv2.warpAffine` with float coordinates = zero jitter
  - Encoder: try h264_nvenc first, fallback to libx264
  - Save to `cache/{hash}.mp4`
- Supports two background types: `imagens` (with Ken Burns zoom) and `videos` (with scale+pad+fps, no zoom)

**Pass 2: Final assembly (65-100% progress)**
- Concatenate all cached clips with FFmpeg concat filter
- Apply visual adjustments (Lumetri-style):
  - `eq` filter: gamma (exposure), brightness, contrast, saturation
  - `curves` filter: highlights, shadows, whites, blacks (luminance curve points)
  - `colorbalance` filter: temperature + tint
  - `vignette` filter: angle-based strength (only if >0.05 to avoid ghost vignette from randomization)
  - Optional randomization: subtle variations centered on base values
- Apply overlays (black background):
  - CPU: `colorkey=black:0.15:0.15` + `colorchannelmixer=aa={opacity}` + `overlay`
  - GPU path exists but disabled (`_tem_cuda_filters` returns False)
- Burn ASS subtitles: `ass='{path}'` filter
- Apply Moldura (frame overlay) - see section below
- Apply CTA overlay (green screen) - see section below
- Mix audio: `amix=inputs=2:duration=first:dropout_transition=2:weights=1 0.5:normalize=0`
  - `normalize=0` preserves narration volume (prevents amix from normalizing down)
  - `weights=1 0.5` gives narration full volume, background music half
- Write filter graph to `temp/filter_complex.txt` (avoids cmd line length limits)
- Output: H.264 NVENC, CQ 20, AAC 192k, faststart

### FFmpeg Priority
All FFmpeg processes get `BELOW_NORMAL_PRIORITY_CLASS` via psutil or ctypes fallback. Prevents server/system from becoming unresponsive during heavy renders.

---

## How Subtitles Work

### Pipeline: Narration MP3 -> Whisper -> SRT -> subtitle_fixer -> ASS

1. **Transcription** (transcriber.py):
   - `faster-whisper` with GPU (CUDA float16), falls back to CPU (int8)
   - Falls back to `openai-whisper` if faster-whisper not installed
   - After transcription, releases VRAM: `del model` + `gc.collect()` + `torch.cuda.empty_cache()`
   - Output: SRT file in temp/

2. **SRT Correction** (subtitle_fixer.py):
   - Loads rules: template-embedded rules take priority over `rules/{idioma}.json`
   - JSON rules support `_global` + template-specific overrides (merged)
   - Processing per subtitle block:
     - Remove hesitations by language (uh, um, ah for EN; aeh, oeh for DE; etc.)
     - Remove custom words
     - Apply word substitutions (case-insensitive regex)
     - Capitalize sentence beginnings
     - Convert to UPPERCASE if template requires
     - Smart line breaking: balanced 2-line split, never cuts words, respects max_chars

3. **ASS Generation** (engine.py > _gerar_ass):
   - Sets `PlayResX` / `PlayResY` = actual video resolution for pixel-perfect positioning
   - `ScaledBorderAndShadow: yes`
   - Converts SRT timestamps to ASS format (centiseconds)
   - Line breaks: `\n` -> `\N` (ASS syntax)
   - Written with UTF-8 BOM (`utf-8-sig`)

### Font Scaling Formula
```
FontSize_pixels = template.legenda_config.tamanho * (video_height / 288)
```
Same formula applies to Outline and Shadow thickness. The 288 is libass default PlayResY; since we set PlayResY to actual resolution, we scale up.

### Subtitle Positioning
- `bottom` (default): Alignment=2, MarginV=50
- `top`: Alignment=8, MarginV=50
- `center`: Alignment=5, MarginV=0
- `custom`: Y% maps to alignment zone (top/middle/bottom thirds), X% adjusts MarginL/MarginR

---

## How Overlays Work

### Regular overlays (black background -> transparent)
- **CPU path**: `colorkey=black:0.15:0.15` removes near-black pixels, then `colorchannelmixer=aa={opacity}` sets transparency, then `overlay=0:0`
- **GPU path** (disabled): `hwupload_cuda` -> `chromakey_cuda=0x000000:0.15:0.0` -> `overlay_cuda`
- Multiple overlays stack sequentially, each composited on top of previous result

### Moldura (Frame Overlay)
Static image overlay that sits permanently on top of the video (like a picture frame or border). Configured per template.

Template field: `template.moldura`
```json
{
  "arquivo": "path/to/frame.png",
  "tipo": "chromakey" | "alpha",
  "opacidade": 1.0
}
```

- **`alpha` type**: PNG with transparency. Uses `format=yuva420p` + `colorchannelmixer=aa={opacidade}`
- **`chromakey` type**: Image with green background. Uses `chromakey=0x00FF00:0.2:0.1` to remove green, then applies opacity
- Scaled to match video resolution (`scale={w}:{h}`)
- Applied after subtitles but before CTA overlay in the filter chain
- Input via `-loop 1 -t {duration}` for static image looping

### CTA overlay (green screen -> transparent)
- `chromakey=0x00FF00:0.2:0.1` removes green background
- Scaled to `{video_width * cta_escala}x{video_height * cta_escala}`
- Positioned at one of 7 predefined spots (bottom-right, bottom-center, bottom-left, top-right, top-center, top-left, center)
- Appears periodically via FFmpeg `enable` expression:
  - `enable='between(t,30.0,38.0)+between(t,330.0,338.0)+...'`
- Config: `cta.inicio` (first appearance), `cta.duracao` (how long), `cta.intervalo` (gap between appearances), `cta.posicao`, `cta.escala`

---

## How Narration Works (narrator.py)

### ai33.pro API (proxy to ElevenLabs + Minimax)
- Base URL: `https://api.ai33.pro`
- Auth header: `xi-api-key: {api_key}`
- API key stored in `config.json` as `ai33_api_key`

### Voice listing:
- **ElevenLabs own voices**: `GET /v2/voices?page_size=100` - returns voices with `is_bookmarked` flag
- **ElevenLabs Voice Library (shared)**: `GET /v1/shared-voices?page_size=100` - community shared voices, tagged as `provider: "elevenlabs_shared"`
- **Minimax voices**: `POST /v1m/voice/list` with page/page_size/tag_list
- **Cloned voices**: `GET /v1m/voice/clone` - Minimax cloned voices

### TTS generation:
- **ElevenLabs**: `POST /v1/text-to-speech/{voice_id}?output_format=mp3_44100_128`
  - Body: `{text, model_id, with_transcript: true}`
  - Models: `eleven_multilingual_v2`, `eleven_v3`, etc.
- **Minimax**: `POST /v1m/task/text-to-speech`
  - Body: `{text, model, voice_setting: {voice_id, vol, pitch, speed}, language_boost: "Auto", with_transcript: true}`
  - Models: `speech-2.6-hd`, turbo, etc.

### Task flow:
1. Start TTS -> receive `task_id`
2. Poll `GET /v1/task/{task_id}` until status=`done`
3. Extract `audio_url` and `srt_url` from `metadata`
4. Download audio to output folder (or skip if preview mode)
5. Track remaining credits via `ec_remain_credits`

### Anti-duplication:
- **Backend**: `iniciar_narracao()` checks `estado_narracao["ativo"]` and returns error if already active. Prevents concurrent TTS jobs.
- **Frontend**: Should also have a flag to prevent double-click on generate button.

### Voice Config Per Template
Each template stores voice settings in `template.narracao_voz`:
```json
{
  "voice_id": "abc123",
  "provider": "minimax_clone",
  "speed": 1.0,
  "pitch": 0
}
```
This is the source of truth for which voice a template uses. The narration batch UI and "Produzir Tudo" orchestrator read from this config. Voice priority: template's `narracao_voz` > column's `voice_id`.

### Batch narration:
- Per-template cards in the Narracao tab
- Voice comes fixed from template's `narracao_voz` config
- Skip existing MP3s (checks output folder)
- Cancel/skip individual items during batch
- Timer per job tracking generation time
- Credits tracking and display
- Date-based naming: `{tag}_{YYYYMMDD}_{sequence}.mp3`
- Preview mode: generates but does not save to disk
- Cards are draggable to reorder

---

## Full Production Orchestrator ("Produzir Tudo")

The "Produzir Tudo" button in the Temas tab runs the complete production pipeline for all columns of a selected date. Located at the top of the Temas grid.

### Flow per column:
1. **Roteiro (Script)**: If `cel.roteiro` exists, skip. Otherwise run the column's pipeline with Temas variables. Wait for pipeline queue to be free first. Save result back to cell.
2. **Narracao (Narration)**: Check if MP3 already exists in narracoes/ folder. If yes, skip. Otherwise generate TTS using template's `narracao_voz` config. Poll until done.
3. **Video Production**: Check if MP4 already exists in template's output folder. If yes, skip. Otherwise run batch production with the template and MP3.

### Key behaviors:
- **Skip existing files**: Each step checks for existing output before running
- **Pipeline queue**: Waits for previous pipeline to finish before starting next (polls up to 30 times at 2s intervals)
- **Error handling**: If any step fails for a column, logs the error and `continue` to next column
- **Progress UI**: Shows count (e.g., "2/6"), progress bar, and detailed log per step per column
- **Auto-save**: After completion, saves temas grid (with roteiro data) and re-renders
- **Naming**: Narration = `{colName} {DD-MM}.mp3`, Video = `{tag}_{YYYYMMDD}_01.mp4`

### Variables sent to pipeline:
```json
{
  "entrada": "cell tema",
  "tema": "cell tema",
  "canal": "column name",
  "data": "row date",
  "titulo": "cell titulo",
  "thumb": "cell thumb"
}
```

---

## Claude CLI Chat

Integrated chat in the Temas tab for AI-assisted theme generation.

### Setup:
- Runs from `agents/temas/` directory
- Agent instructions in `agents/temas/CLAUDE.md` (editable via UI)
- Conversation history in `agents/temas/historico.json` (max 100 messages)

### Execution:
- Command: `claude -p --continue --output-format text "{prompt}"`
- `--continue` maintains session persistence (Claude CLI remembers context across messages)
- Environment vars cleaned before launch: all `CLAUDE_CODE_*` and `ANTHROPIC_*` vars removed from env to prevent "nested session" error
- Working directory set to `agents/temas/`
- Claude CLI path: `%APPDATA%/npm/claude.cmd` (fallback to `claude` on PATH)
- Timeout: 120 seconds
- Shell mode enabled (`shell=True`)

### Endpoints:
- `GET /api/chat/instructions` - Read agent CLAUDE.md
- `PUT /api/chat/instructions` - Update agent CLAUDE.md
- `GET /api/chat/history` - Get conversation history
- `DELETE /api/chat/history` - Clear history
- `POST /api/chat` - Send message, returns `{resposta: "..."}"`

---

## Credential System

Multiple API keys per LLM provider, stored in `credentials.json` as an array.

### Structure:
```json
[
  {
    "id": "cred_abc123",
    "nome": "Claude Main",
    "provedor": "claude",
    "api_key": "sk-ant-..."
  }
]
```

### Auto model listing:
When a credential is tested/refreshed, the system queries the provider's API for available models:
- **Claude**: `GET api.anthropic.com/v1/models` (all models returned, sorted reverse)
- **GPT**: `GET api.openai.com/v1/models` (filtered to prefixes: gpt-4, gpt-3.5, o1, o3, o4)
- **Gemini**: `GET generativelanguage.googleapis.com/v1beta/models` (filtered to those supporting `generateContent`)

### Test endpoint:
`POST /api/credenciais/{id}/refresh` tests the key and returns `{ok: true, modelos: [...]}` or `{ok: false, erro: "..."}`.

---

## Known Issues

- **FFmpeg memory with large filter graphs**: Very long videos with many overlays + CTA can cause FFmpeg to use excessive memory. Filter graph is written to file (`-filter_complex_script`) to avoid command-line length limits but memory usage still scales.
- **Server timeout during heavy rendering**: Long renders (30+ min videos) may cause HTTP timeouts on the polling side. The render continues in background thread but the UI may need manual refresh.
- **GPU overlay disabled**: `_tem_cuda_filters()` returns False. The chromakey_cuda/overlay_cuda path exists but was disabled because CPU colorkey is stable and fast (~141fps).
- **VRAM contention**: Whisper GPU and FFmpeg NVENC both need GPU. Transcriber releases VRAM after use but if both run simultaneously, OOM is possible.
- **Single narration at a time**: Backend blocks concurrent narrations via `estado_narracao["ativo"]` check. This is by design to prevent ai33.pro API conflicts.
- **Pipeline single execution**: Only one pipeline can run at a time (global `estado_execucao`). The orchestrator handles this by queuing.

---

## Important Conventions

### JavaScript in Python strings
- All JS lives inside the `DASHBOARD_HTML` string in `app.py`
- JS `\n` in Python strings MUST be escaped as `\\n` (otherwise Python interprets it)
- Same for `\\` in regex patterns within JS strings
- After editing app.py JS, verify syntax: `node --check temp/ck.js` (copy JS to temp file first)

### Cache keys include effect name
- Cache key = MD5 of `{img_path}|{dur}|{WxH}|{fps}|{zoom_ratio}|{effect_name}`
- Changing effect name invalidates cache for that image (old cache won't interfere)

### FFmpeg process priority
- All FFmpeg subprocesses get `BELOW_NORMAL_PRIORITY_CLASS`
- Uses psutil if available, fallback to ctypes kernel32

### ASS subtitle resolution
- PlayResX/PlayResY = actual video resolution (e.g., 1920x1080)
- This means all pixel values (FontSize, Outline, Shadow, Margins) are in real pixels
- Template values are in libass-default scale (288px) and get multiplied by `resolution/288`

### ai33.pro API key
- Stored in `config.json` as `ai33_api_key`
- Used as `xi-api-key` header for all ai33.pro requests

### Variable substitution (scriptwriter.py)
- Uses regex with space tolerance: `\{\{\s*chave\s*\}\}` matches `{{chave}}`, `{{ chave }}`, etc.
- Variables are substituted in both `system_message` and `prompt` fields

### File naming
- Output videos: `{tag}_{YYYYMMDD}_{sequence}.mp4`
- Narrations: `{tag}_{YYYYMMDD}_{sequence}.mp3` or `{colName} {DD-MM}.mp3` (from orchestrator)
- Scripts: `{pipeline_name}_{YYYYMMDD_HHMMSS}.txt`
- Pipeline logs: `logs/pipeline_{id}_{YYYYMMDD_HHMMSS}.log`

### Config key masking
- `GET /api/config` masks API keys: shows only `...{last4}` for any field containing "key" in its name
- `PUT /api/config` skips values starting with "..." to avoid overwriting with masked values

---

## API Endpoints Reference

### Templates
- `GET  /api/templates` - List all templates
- `POST /api/templates` - Create template
- `PUT  /api/templates/{id}` - Update template
- `DELETE /api/templates/{id}` - Delete template

### Production
- `POST /api/produce` - Start single video production (transcribe + fix SRT + render)
- `POST /api/batch` - Start batch production (multiple template+mp3 jobs)
- `GET  /api/batch/status` - Poll batch progress
- `POST /api/batch/cancel` - Cancel batch

### Full Production Orchestrator
- `GET  /api/producao-completa/status` - Poll orchestrator state (etapa, coluna_atual, progresso, log)

### Preview
- `GET  /api/preview/frame` - Preview video frame (base64 JPEG)
- `GET  /api/preview/image` - Preview single image
- `POST /api/preview/audio` - Generate audio preview
- `GET  /api/preview/audio/play` - Play audio preview

### Subtitles
- `GET  /api/rules/{idioma}` - Get rules for language
- `GET  /api/rules/{idioma}/{template_id}` - Get merged rules
- `PUT  /api/rules/{idioma}/{template_id}` - Update rules

### Narration
- `GET  /api/narration/voices` - List all voices (cloned + ElevenLabs + shared + Minimax)
- `POST /api/narration/generate` - Start TTS generation
- `GET  /api/narration/status` - Poll narration status
- `GET  /api/narration/credits` - Check remaining credits

### Pipelines (Roteiros)
- `GET  /api/pipelines` - List pipelines
- `POST /api/pipelines` - Create pipeline
- `PUT  /api/pipelines/{id}` - Update pipeline
- `DELETE /api/pipelines/{id}` - Delete pipeline
- `POST /api/pipelines/{id}/executar` - Execute pipeline (with entrada + context vars)
- `GET  /api/pipelines/execucao` - Poll execution status
- `POST /api/pipelines/execucao/cancelar` - Cancel execution
- `POST /api/pipelines/testar-etapa` - Test single step
- `POST /api/pipelines/testar-cadeia` - Test chain of steps ("Test until here")

### Credentials
- `GET  /api/credenciais` - List credentials
- `POST /api/credenciais` - Create credential
- `PUT  /api/credenciais/{id}` - Update credential
- `DELETE /api/credenciais/{id}` - Delete credential
- `POST /api/credenciais/{id}/refresh` - Test + refresh model list from API

### Temas
- `GET  /api/temas` - Get temas grid (supports legacy array format, returns grid object)
- `POST /api/temas` - Save temas grid (with optional Supabase sync)

### Chat (Claude CLI)
- `GET  /api/chat/instructions` - Get agent instructions (CLAUDE.md)
- `PUT  /api/chat/instructions` - Update agent instructions
- `GET  /api/chat/history` - Get conversation history
- `DELETE /api/chat/history` - Clear conversation history
- `POST /api/chat` - Send message to Claude CLI

### Config
- `GET  /api/config` - Get app config (keys masked)
- `PUT  /api/config` - Update app config (masked values skipped)

### Utilities
- `GET  /api/browse` - File browser (with per-field last-folder memory)
- `GET  /api/fonts` - List system fonts (PowerShell, cached)
- `GET  /api/historico` - Get production history
- `DELETE /api/historico` - Clear history

### Pages
- `GET /` - Dashboard (HTML SPA)
- `GET /dashboard` - Dashboard (alias)

---

## Development Workflow

1. Edit `app.py` (backend logic or inline JS/HTML)
2. If JS was changed, verify syntax:
   - Extract JS to a temp file
   - Run `node --check temp/ck.js`
3. Restart the server: kill uvicorn and re-run
4. Server start: `python app.py` or `uvicorn app:app --host 0.0.0.0 --port 8500`
5. Access at `http://localhost:8500`

### Startup files
- `iniciar.bat` - Start script
- `run_hidden.vbs` - Run without console window
- `starter.pyw` - Python launcher (windowless)
- `instalar_servico.bat` / `desinstalar_servico.bat` - Windows service install/uninstall

---

## Backlog / Pending Items

See `BACKLOG.txt` for the full list. Key pending items by priority:

### HIGH PRIORITY
- **Full production resilience**: Retry per step (3x with exponential delay), timeout per step (roteiro 5min, narracao 10min, video 30min)
- **Emergency buttons**: Cancel/skip individual + cancel batch on all batch operations (roteiro, narracao, video, produzir tudo)
- **Watchdog**: Auto-restart server if uvicorn dies (nssm Windows Service or .bat loop)
- **Production state persistence**: Save state to `producao_estado.json`, resume after restart
- **monitor.py**: Independent Python script that runs in loop - health check, stuck detection, auto-start production at scheduled time, webhook notifications

### MEDIUM PRIORITY
- Cloudflare Tunnel / Tailscale for remote access
- Git repository initialization
- Supabase/Sheets sync testing
- Google Sheets read/write
- Thumbnail text generation
- Completion/error webhooks (Telegram, Discord)

### LOW / FUTURE
- YouTube upload via Data API v3
- Upload scheduling
- Link Tracker integration
- Sound effects / music generation via ai33.pro
- Voice cloning from the app
- Dubbing (translate narrations)
- Mobile-responsive UI
- Dashboard with production stats

### Autonomous Agent Options (Future)
1. **Claude Code Scheduled Agent**: Cron-based, checks health every 30min
2. **Agent SDK**: Continuous monitoring, intelligent decisions, quality validation
3. **monitor.py** (preferred first step): Simple Python loop, health checks, auto-restart, scheduled production, webhook alerts
4. **Combination**: monitor.py for stability + Claude agent for intelligence
