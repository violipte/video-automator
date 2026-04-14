# Video Automator - Technical Intelligence File

## What It Is

Local automation tool that replaces Adobe Premiere Pro for YouTube video production. Produces 12+ videos/day across 6+ channels using Python + FFmpeg. Operator (Piter) runs channels in EN, DE, PT, ES covering spiritual content (Chosen One / Starseed niches). The goal is 100% automation: from script generation through narration, subtitle, video assembly, and (future) upload.

## Architecture

**Single-file SPA**: FastAPI backend (`app.py`, ~7800 lines) serves an inline HTML/JS dashboard via a `DASHBOARD_HTML` string. No separate frontend build. All JavaScript lives inside Python string literals in `app.py`. Server runs on port 8500.

**Backend modules**:
- `engine.py` (~850 lines) - Video assembly engine (OpenCV + FFmpeg)
- `transcriber.py` - Whisper audio-to-SRT transcription
- `subtitle_fixer.py` - SRT correction rules engine
- `scriptwriter.py` (~680 lines) - Multi-step LLM pipeline executor + credential system + isolated execution
- `narrator.py` (~400 lines) - TTS via ai33.pro API (ElevenLabs + Minimax), chunking sequencial
- `orchestrator.py` (~600 lines) - Production orchestrator (3-phase pipeline: parallel roteiros + narration→render pipeline)
- `production_log.py` (~140 lines) - Persistent production state (thread-safe with RLock)
- `render_queue.py` (~100 lines) - Shared render queue (auto + manual modes, 1 worker)

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
  orchestrator.py     # Production orchestrator (3-phase pipeline)
  production_log.py   # Persistent production state (thread-safe)
  render_queue.py     # Shared render queue (auto + manual)
  link_tracker.py     # Link tracker integration
  watchdog.bat        # Auto-restart server on crash
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
- **Gemini**: `POST generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` (maxOutputTokens=65536, timeout=300s)

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
- **Video background pre-concatenation**: For `tipo_fundo="videos"`, clips are pre-concatenated into a single `bg_concat.mp4` using FFmpeg concat demuxer. Pre-concatenation now re-encodes with `-vf fps={fps},scale={w}:{h}` and `libx264 -preset ultrafast -crf 18` instead of `-c copy`. This normalizes fps (source videos may be 24fps while template is 30fps) and resolution. Without this, fps mismatch caused video to freeze early. This prevents OOM (was 21GB RAM with 200+ inputs). The shuffled list is written to `bg_concat.txt` then concatenated with `-t duration` to trim.
- **Video loop option**: `template.video_loop` (boolean, default true). ON: repeats shuffled videos to fill audio duration. OFF: plays videos once and freezes last frame. Checkbox in Fundo tab.

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
- Output: H.264 NVENC p1, CQ 24, AAC 192k, faststart
- **Metadata stripping**: After successful render, runs `ffmpeg -map_metadata -1 -fflags +bitexact` to strip encoder/software/timestamps from final MP4. Prevents YouTube automation detection.

### FFmpeg Resilience
- **Auto-retry on failure**: Up to 2 retries with 5s delay. Corrupted files auto-deleted before retry.
- **FFmpeg watchdog**: If no progress for 10 minutes (`stall_timeout=600s`), kills the process. Prevents infinite hangs.
- **Orphan FFmpeg cleanup**: On fatal thread crash, runs `taskkill /F /IM ffmpeg.exe` to clean up stuck processes.

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
- Filter includes `fps={fps}` and `setpts=PTS-STARTPTS` to ensure the moldura starts from frame 0 and stays synced throughout. Overlay uses `shortest=1`.
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

### TTS chunking (Minimax):
- For texts >8000 chars, auto-splits into chunks by paragraph
- Sends each chunk as a separate TTS task, downloads all audio files
- Concatenates with FFmpeg using `concat_list.txt` with relative paths (avoids encoding issues)

### Anti-duplication:
- **Backend**: `iniciar_narracao()` checks `estado_narracao["ativo"]` and returns error if already active. Prevents concurrent TTS jobs.
- **Frontend**: Flag prevents double-click on batch generate button.

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
- Cancel/skip individual items during batch ("Cancelar Tudo" button + "Pular" per card)
- Timer per job tracking generation time
- Credits tracking and display
- Date-based naming: `{tag}_{YYYYMMDD}_{sequence}.mp3`
- Preview mode: generates but does not save to disk
- Cards are draggable to reorder
- **Batch MP3 persistence**: Production batch MP3 paths saved in localStorage (key: `batchMp3Values`). Survive page refresh. "Limpar Áudios" button clears all. Individual audio clear button (X) per production row.

---

## Full Production Orchestrator ("Produzir Tudo")

The "Produzir Tudo" button in the Temas tab runs the complete production pipeline for all columns of a selected date. Located at the top of the Temas grid.

### Flow per column:
1. **Roteiro (Script)**: If `cel.roteiro` exists, skip. Otherwise run the column's pipeline with Temas variables. Wait for pipeline queue to be free first. Save result back to cell.
2. **Narracao (Narration)**: Check if MP3 already exists in narracoes/ folder. If yes, skip. Otherwise generate TTS using template's `narracao_voz` config. Poll until done.
3. **Video Production**: Check if MP4 already exists in template's output folder. If yes, skip. Otherwise run batch production with the template and MP3.

### Key behaviors:
- **Skip existing files**: Each step checks for existing output before running
- **Pipeline execution queue**: Both batch roteiro and "Produzir Tudo" wait for previous pipeline to finish before starting next (prevents 409 conflicts). Polls up to 30 times at 2s intervals.
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

- **FFmpeg memory with large filter graphs**: Very long videos with many overlays + CTA can cause FFmpeg to use excessive memory. Filter graph is written to file (`-filter_complex_script`) to avoid command-line length limits but memory usage still scales. (Video background OOM with 200+ inputs was fixed via pre-concatenation - see engine section.)
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

### video_loop in _montar_final()
- `video_loop` must be read from `self.template` inside `_montar_final()` since it is not passed as a parameter

### Variable substitution (scriptwriter.py)
- Uses regex with space tolerance: `\{\{\s*chave\s*\}\}` matches `{{chave}}`, `{{ chave }}`, etc.
- Variables are substituted in both `system_message` and `prompt` fields

### Date-based output folders
- Videos now saved to `{pasta_saida}/{YYYY-MM-DD}/{TAG}_{DATA}_{SEQ}.mp4`. Subfolders created automatically.

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

### Thumbnail AI (ai33.pro image generation)
- `GET  /api/thumbnail/ai/models` - List available image generation models
- `POST /api/thumbnail/ai/price` - Calculate credits cost before generating
- `POST /api/thumbnail/ai/generate` - Generate image from prompt (returns task_id)
- `POST /api/thumbnail/ai/generate-from-template` - Generate using template's thumb_config (auto-picks pools)
- `GET  /api/thumbnail/ai/status/{task_id}` - Poll image generation status

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

## Render Performance (engine.py)

### Ken Burns clips
- Generated via OpenCV `warpAffine` (subpixel interpolation, zero jitter)
- Encoded with **NVENC p1 CQ 26** (GPU, 1 worker sequential — RTX 3060 limit)
- Fallback to libx264 ultrafast if NVENC unavailable
- Pre-concatenated into single `bg_clips_concat.mp4` before render final
- This makes image-based templates render as fast as video-based ones
- **IMPORTANT**: RTX 3060 supports max 2 concurrent NVENC sessions. Never use >1 worker for clip encoding.

### Clip duration scaling
Clips use escalated durations to reduce count without losing visual variety:
- 0:00-2:00 = 10s per image (fast rhythm for hook)
- 2:00-15:00 = 15s per image (medium)
- 15:00+ = 20s per image (relaxed)
- Result: ~139 clips for 40min video (vs ~241 with fixed 10s)

### Render final
- NVENC p1, CQ 24 (reserved for final assembly only)
- 16 FFmpeg threads for CPU-bound filters
- NVENC limit: RTX 3060 = 2 concurrent sessions (do NOT exceed)

### Metadata stripping
- `ffmpeg -map_metadata -1 -fflags +bitexact` after render
- Removes encoder/software/timestamps from final MP4

---

## Production Modes (Separated)

### Auto Mode (orchestrator.py → "Produzir Tudo")
- 3-phase pipeline: roteiros parallel (3 workers) → narration+render pipeline
- Narration uses `estado_narracao_auto` (independent from manual)
- Renders enqueued via `render_queue.enfileirar(fonte="auto")`
- Canals with existing MP3 go directly to render queue (don't wait for narration)

### Manual Mode (app.py batch → aba Produção)
- Individual channel control
- Narration uses `estado_narracao` (independent from auto)
- Renders also use render_queue (shared, 1 worker)
- Both modes can coexist without blocking each other

### Render Queue (render_queue.py)
- Single worker thread consuming jobs FIFO
- Both auto and manual modes enqueue here
- Guarantees only 1 render at a time (GPU/NVENC protection)
- Callbacks: `on_done`, `on_error` per job

---

## LLM Provider System (scriptwriter.py)

### Providers
- `claude` — Anthropic API (paid, $3-15/1M tokens)
- `claude_cli` — Claude CLI `-p` mode (Max plan, $0 extra, ~1000 Sonnet/5h)
- `gpt` — OpenAI API (paid)
- `gemini` — Google AI Studio API (free tier: 500 RPD for Flash)

### Current Pipeline Config (all channels)
- **Hooker + Developer**: `claude_cli` (Sonnet 4.6, Max plan, $0)
- **Amplifier**: `gemini` (Flash 2.5, free tier, $0)
- **Translator DE**: `gemini` (Flash 2.5, free tier, $0)

### Fallback Chain
When a provider fails (429, timeout, error), tries next in order:
1. `claude` (Claude API)
2. `gemini` (Gemini Flash)
3. `gpt` (GPT 5.2)
Skips the provider that already failed.

### Pipeline abort on LLM failure
If an LLM step fails (even with fallback), the pipeline **aborts** instead of continuing with bad data. Prevents short/garbage scripts.

### Variable cleanup
`_substituir_variaveis` automatically removes unsubstituted `{{vars}}` from output, preventing literal variable names from being narrated.

---

## Narration Chunking (narrator.py)

### Sequential chunking (Minimax texts >8000 chars)
- Sends 1 chunk → polls until done → downloads → next chunk
- NOT all-at-once (avoids overloading ai33.pro API)
- Each chunk has 10min timeout
- Download: 300s timeout + 3 retries
- Task polling: 5 retries on 502/503/429

### Separate states
- `estado_narracao` — manual mode (aba Narração/Produção)
- `estado_narracao_auto` — auto mode (orchestrator)
- Both can operate independently

---

## Chat System (app.py)

### Direct API (not Claude CLI)
- Uses Anthropic API directly via httpx (not subprocess)
- Model: claude-sonnet-4-6, max_tokens: 8000
- System message loaded from `agents/{agent}/CLAUDE.md`
- Only current message sent (no history in API context — prevents contamination)

### Per-agent history
- Each agent has separate `agents/{agent}/historico.json`
- Temas and Títulos histories don't mix
- History saved for UI display only, not sent to API
- Survives page refresh (F5)

---

## Resilience

### Production
- Auto-resume disabled on startup (manual control)
- Health monitor thread (60s) detects dead threads during active production
- Timeouts: roteiro 10min, narração 40min, vídeo 90min
- Narration state always reset on error/timeout (prevents blocking)
- FFmpeg orphan cleanup: 3x taskkill on timeout

### Render Queue
- Single worker prevents GPU contention
- Both auto and manual modes share the queue safely

### Server
- PID displayed in sidebar footer
- `/api/health` endpoint with production status + render queue status
- `watchdog.bat` auto-restarts on crash

---

## Thumbnail AI System (app.py)

### ai33.pro Image Generation (/v1i endpoints)
- Uses `api.ai33.pro/v1i/` prefix for image endpoints
- Auth: same `xi-api-key` header as TTS
- Model: `bytedance-seedream-4.5` (supports reference images, up to 10 assets)
- Aspect ratios: 16:9, 4:3, 1:1, 3:4, 9:16
- Resolutions: 2K, 4K
- GPT models use `quality` param instead of `resolution`
- Task polling: reuses common `GET /v1/task/{task_id}` endpoint (type: `imagen2`)

### Per-template thumb_config
Templates can store `thumb_config` for AI thumbnail generation:
```json
{
  "prompt_base": "A cosmic scene with [CENA] and [TEXTO DE CIMA]...",
  "pools": {
    "CENA": ["nebula", "galaxy", "aurora"],
    "ESTILO": ["watercolor", "photorealistic"]
  },
  "model_id": "gpt-image-1.5",
  "aspect_ratio": "16:9",
  "resolution": "2K"
}
```
- Pool items are randomly selected and substituted into `prompt_base` as `[POOL_NAME]`
- `[TEXTO DE CIMA]` and `[TEXTO DE BAIXO]` are replaced with user-provided text
- `generate-from-template` endpoint handles pool randomization automatically

### Orchestrator roteiro source of truth
- `.txt` file in `Roteiros/` folder is the **sole source of truth** for whether a roteiro exists
- `temas.json` cell `roteiro` field is no longer checked as fallback
- If `.txt` exists, its content is loaded into the cell for downstream use

---

## Backlog / Pending Items

See `BACKLOG.txt` for the full list. Key pending items by priority:

### HIGH PRIORITY
- Upload system (YouTube API, OAuth per channel, proxy, pinned comments)
- Monitor: detailed render progress (clips count, ETA)
- Parallel narrations (Etapa B)

### DONE (this session)
- Thumbnail AI generation (ai33.pro /v1i endpoints, per-template thumb_config with prompt pools)
- Claude CLI --system-prompt as list args (no shell=True, no temp files)
- Claude CLI output cleanup (regex strips preambles, timestamps, section headers)
- Gemini maxOutputTokens increased to 65536
- Orchestrator: .txt file is sole source of truth for roteiro (no temas.json fallback)
- Reset producao: also resets auto narration state and loop flag
- JS syntax fix: pool remove button onclick quote escaping
- Produzir Tudo em Loop (checkbox, auto-advance to next date)
- Separated auto/manual modes (render_queue, narrator states)
- Automator Exports folder structure (Roteiros/, Narracoes/, Videos/)
- Claude CLI as LLM provider ($0 cost with Max plan)
- Gemini Flash free tier for Amplifiers
- Per-tab refresh button
- Char counters on cell editor fields
- Cancelar + Reset button on Monitor
- Historico tab removed from sidebar

### MEDIUM PRIORITY
- Cloudflare Tunnel / Tailscale for remote access
- Notification webhooks (Telegram/Discord)
- Sticky grid headers (freeze date column + channel row)
- Chat panel not blocking grid

### LOW / FUTURE
- DaVinci Resolve scripting API for render
- Mobile-responsive UI
- Dashboard with production stats
- Autonomous agent (monitor.py + Claude scheduled)
