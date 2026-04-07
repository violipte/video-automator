# Video Automator - Technical Intelligence File

## What It Is

Local automation tool that replaces Adobe Premiere Pro for YouTube video production. Produces 12+ videos/day across 6+ channels using Python + FFmpeg. Operator (Piter) runs channels in EN, DE, PT, ES covering spiritual content (Chosen One / Starseed niches).

## Architecture

**Single-file SPA**: FastAPI backend (`app.py`, ~4200 lines) serves an inline HTML/JS dashboard via a `DASHBOARD_HTML` string. No separate frontend build. All JavaScript lives inside Python string literals in `app.py`.

**Backend modules**:
- `engine.py` - Video assembly engine (OpenCV + FFmpeg)
- `transcriber.py` - Whisper audio-to-SRT transcription
- `subtitle_fixer.py` - SRT correction rules engine
- `scriptwriter.py` - Multi-step LLM pipeline executor
- `narrator.py` - TTS via ai33.pro API (ElevenLabs + Minimax)

**Data files** (all JSON, no database):
- `templates.json` - Template configurations
- `pipelines.json` - LLM pipeline definitions
- `credentials.json` - API credentials (Claude, GPT, Gemini)
- `config.json` - App settings (ai33_api_key, sync settings)
- `temas.json` - Temas grid data
- `historico.json` - Production history

**Directories**:
```
video-automator/
  app.py              # FastAPI server + inline SPA (DASHBOARD_HTML)
  engine.py           # Video engine (OpenCV clips + FFmpeg assembly)
  transcriber.py      # Whisper transcription (faster-whisper + fallback)
  subtitle_fixer.py   # SRT correction (rules, line breaking, hesitations)
  scriptwriter.py     # LLM pipelines (Claude/GPT/Gemini)
  narrator.py         # TTS via ai33.pro
  templates.json      # Template configs
  config.json         # App config (ai33_api_key)
  credentials.json    # LLM API keys
  temas.json          # Temas grid state
  historico.json      # Production history
  rules/              # Subtitle correction rules per language
    en.json, de.json, pt.json, es.json
  agents/
    temas/
      CLAUDE.md       # Agent instructions for tema generation
  cache/              # Cached Ken Burns clips (MD5-keyed .mp4)
  temp/               # Temporary files (SRT, ASS, filter scripts)
  narracoes/          # Generated narration audio files
  scripts/            # Generated script text files
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

## How the Video Engine Works

### Two-pass rendering (engine.py > VideoEngine.montar):

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

**Pass 2: Final assembly (65-100% progress)**
- Concatenate all cached clips with FFmpeg concat filter
- Apply visual adjustments (Lumetri-style):
  - `eq` filter: gamma (exposure), brightness, contrast, saturation
  - `curves` filter: highlights, shadows, whites, blacks (luminance curve points)
  - `colorbalance` filter: temperature + tint
  - `vignette` filter: angle-based strength
  - Optional randomization: subtle variations centered on base values
- Apply overlays (black background):
  - CPU: `colorkey=black:0.15:0.15` + `colorchannelmixer=aa={opacity}` + `overlay`
  - GPU path exists but disabled (`_tem_cuda_filters` returns False)
- Burn ASS subtitles: `ass='{path}'` filter
- Apply CTA overlay (green screen):
  - `chromakey=0x00FF00:0.2:0.1` + scale to percentage + overlay with `enable` expression
  - Appears at intervals: `between(t,start,end)` expressions joined with `+`
- Mix audio: narration volume + background music with `amix` (duration=first, normalize=0)
- Write filter graph to `temp/filter_complex.txt` (avoids cmd line length limits)
- Output: H.264 NVENC, CRF 20, AAC 192k, faststart

### FFmpeg Priority
All FFmpeg processes get `BELOW_NORMAL_PRIORITY_CLASS` via psutil or ctypes fallback. Prevents server/system from becoming unresponsive during heavy renders.

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
     - Remove hesitations by language (uh, um, ah for EN; aeh, oeh for DE; etc)
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
- `custom`: Y% maps to alignment zone (top/middle/bottom), X% adjusts MarginL/MarginR

## How Overlays Work

### Regular overlays (black background -> transparent)
- **CPU path**: `colorkey=black:0.15:0.15` removes near-black pixels, then `colorchannelmixer=aa={opacity}` sets transparency, then `overlay=0:0`
- **GPU path** (disabled): `hwupload_cuda` -> `chromakey_cuda=0x000000:0.15:0.0` -> `overlay_cuda`
- Multiple overlays stack sequentially, each composited on top of previous result

### CTA overlay (green screen -> transparent)
- `chromakey=0x00FF00:0.2:0.1` removes green background
- Scaled to `{video_width * cta_escala}x{video_height * cta_escala}`
- Positioned at one of 7 predefined spots (bottom-right, bottom-center, etc)
- Appears periodically via FFmpeg `enable` expression:
  - `enable='between(t,30.0,38.0)+between(t,330.0,338.0)+...'`

## How Narration Works (narrator.py)

### ai33.pro API (proxy to ElevenLabs + Minimax)
- Base URL: `https://api.ai33.pro`
- Auth header: `xi-api-key: {api_key}`
- API key stored in `config.json` as `ai33_api_key`

### Voice listing:
- ElevenLabs: `GET /v2/voices?page_size=100`
- Minimax: `POST /v1m/voice/list` with page/page_size/tag_list
- Cloned voices: `GET /v1m/voice/clone`

### TTS generation:
- ElevenLabs: `POST /v1/text-to-speech/{voice_id}?output_format=mp3_44100_128`
  - Body: `{text, model_id, with_transcript: true}`
  - Models: `eleven_multilingual_v2`, `eleven_v3`, etc
- Minimax: `POST /v1m/task/text-to-speech`
  - Body: `{text, model, voice_setting: {voice_id, vol, pitch, speed}, language_boost: "Auto"}`
  - Models: `speech-2.6-hd`, turbo, etc

### Task flow:
1. Start TTS -> receive `task_id`
2. Poll `GET /v1/task/{task_id}` until status=`done`
3. Extract `audio_url` and `srt_url` from `metadata`
4. Download audio to `narracoes/` or template output folder
5. Track remaining credits via `ec_remain_credits`

### Batch narration:
- Per-template cards with voice/text selection
- Date-based naming: `{tag}_{YYYYMMDD}_{sequence}`
- Preview mode: generates but does not save to disk

## How Pipelines Work (scriptwriter.py)

Multi-step LLM chains for script generation:

1. Pipeline = ordered list of `etapas` (steps)
2. Each step has: `nome`, `credencial` (ref to credentials.json), `modelo`, `system_message`, `prompt`
3. Variable substitution in system_message and prompt:
   - `{{entrada}}` - original user input
   - `{{saida_anterior}}` - output of previous step
   - `{{saida_etapa_N}}` - output of step N (1-indexed)
   - `{{roteiro_atual}}` - latest completed output
4. Execution: sequential, each step calls the appropriate LLM API
5. Supports Claude (Anthropic API), GPT (OpenAI API), Gemini (Google API)
6. State tracked per-step: aguardando -> processando -> concluido/erro
7. Final output saved to `scripts/{pipeline_name}_{timestamp}.txt`
8. Cancellation supported mid-pipeline

### LLM API calls:
- Claude: `POST api.anthropic.com/v1/messages` (max_tokens=8192)
- GPT: `POST api.openai.com/v1/chat/completions` (max_tokens=8192)
- Gemini: `POST generativelanguage.googleapis.com/v1beta/models/{model}:generateContent` (maxOutputTokens=8192)

## How the Temas Grid Works

Notion-style grid for organizing video themes:

```json
{
  "colunas": [{"id": "col1", "nome": "Semana 1"}, ...],
  "linhas": [{"id": "row1", "nome": "Canal EN"}, ...],
  "celulas": {
    "col1_row1": {"tema": "...", "titulo": "...", "thumb": "..."},
    ...
  }
}
```

- Columns and rows are drag-and-droppable (reorderable)
- Each cell has: tema (theme), titulo (title), thumb (thumbnail path)
- Persisted in `temas.json`

## Known Issues

- **FFmpeg memory with large filter graphs**: Very long videos with many overlays + CTA can cause FFmpeg to use excessive memory. Filter graph is written to file (`-filter_complex_script`) to avoid command-line length limits but memory usage still scales.
- **Server timeout during heavy rendering**: Long renders (30+ min videos) may cause HTTP timeouts on the polling side. The render continues in background thread but the UI may need manual refresh.
- **GPU overlay disabled**: `_tem_cuda_filters()` returns False. The chromakey_cuda/overlay_cuda path exists but was disabled because CPU colorkey is stable and fast (~141fps).
- **VRAM contention**: Whisper GPU and FFmpeg NVENC both need GPU. Transcriber releases VRAM after use but if both run simultaneously, OOM is possible.

## Important Conventions

### JavaScript in Python strings
- All JS lives inside the `DASHBOARD_HTML` string in `app.py`
- JS `\n` in Python strings MUST be escaped as `\\n` (otherwise Python interprets it)
- After editing app.py JS, verify syntax: `node --check temp/ck.js` (copy JS to temp file first)

### Cache keys include effect name
- Cache key = MD5 of `{img_path}|{dur}|{WxH}|{fps}|{zoom_ratio}|{effect_name}`
- Changing effect name invalidates cache for that image (old cache won't interfere)

### FFmpeg process priority
- All FFmpeg subprocesses get `BELOW_NORMAL_PRIORITY_CLASS`
- Uses psutil if available, fallback to ctypes kernel32

### ASS subtitle resolution
- PlayResX/PlayResY = actual video resolution (e.g. 1920x1080)
- This means all pixel values (FontSize, Outline, Shadow, Margins) are in real pixels
- Template values are in libass-default scale (288px) and get multiplied by `resolution/288`

### ai33.pro API key
- Stored in `config.json` as `ai33_api_key`
- Used as `xi-api-key` header for all ai33.pro requests

### Claude CLI chat
- Runs from `agents/temas/` directory
- Environment vars cleaned before launch: all `CLAUDE_CODE_*` and `ANTHROPIC_*` vars removed
- This prevents "nested session" error when Claude CLI detects it's inside another Claude instance
- Agent instructions in `agents/temas/CLAUDE.md`

### File naming
- Output videos: `{tag}_{YYYYMMDD}_{sequence}.mp4`
- Narrations: `{tag}_{YYYYMMDD}_{sequence}.mp3`
- Scripts: `{pipeline_name}_{YYYYMMDD_HHMMSS}.txt`

## API Endpoints Reference

### Templates
- `GET  /api/templates` - List all templates
- `POST /api/templates` - Create template
- `PUT  /api/templates/{id}` - Update template
- `DELETE /api/templates/{id}` - Delete template

### Production
- `POST /api/produce` - Start single video production
- `POST /api/batch` - Start batch production
- `GET  /api/batch/status` - Poll batch progress
- `POST /api/batch/cancel` - Cancel batch

### Preview
- `GET  /api/preview/frame` - Preview video frame
- `GET  /api/preview/image` - Preview single image
- `POST /api/preview/audio` - Generate audio preview
- `GET  /api/preview/audio/play` - Play audio preview

### Subtitles
- `GET  /api/rules/{idioma}` - Get rules for language
- `GET  /api/rules/{idioma}/{template_id}` - Get merged rules
- `PUT  /api/rules/{idioma}/{template_id}` - Update rules

### Narration
- `GET  /api/narration/voices` - List voices (ElevenLabs + Minimax)
- `POST /api/narration/generate` - Start TTS generation
- `GET  /api/narration/status` - Poll narration status
- `GET  /api/narration/credits` - Check remaining credits

### Pipelines (Roteiros)
- `GET  /api/pipelines` - List pipelines
- `POST /api/pipelines` - Create pipeline
- `PUT  /api/pipelines/{id}` - Update pipeline
- `DELETE /api/pipelines/{id}` - Delete pipeline
- `POST /api/pipelines/{id}/executar` - Execute pipeline
- `GET  /api/pipelines/execucao` - Poll execution status
- `POST /api/pipelines/execucao/cancelar` - Cancel execution

### Credentials
- `GET  /api/credenciais` - List credentials
- `POST /api/credenciais` - Create credential
- `PUT  /api/credenciais/{id}` - Update credential
- `DELETE /api/credenciais/{id}` - Delete credential
- `POST /api/credenciais/{id}/refresh` - Test + refresh model list

### Temas
- `GET  /api/temas` - Get temas grid
- `POST /api/temas` - Save temas grid

### Chat (Claude CLI)
- `GET  /api/chat/instructions` - Get agent instructions
- `PUT  /api/chat/instructions` - Update agent instructions
- `POST /api/chat` - Send message to Claude CLI

### Config
- `GET  /api/config` - Get app config
- `PUT  /api/config` - Update app config

### Utilities
- `GET  /api/browse` - File browser
- `GET  /api/fonts` - List system fonts
- `GET  /api/historico` - Get production history
- `DELETE /api/historico` - Clear history

### Pages
- `GET /` - Dashboard (HTML SPA)
- `GET /dashboard` - Dashboard (alias)

## Development Workflow

1. Edit `app.py` (backend logic or inline JS/HTML)
2. If JS was changed, verify syntax:
   - Extract JS to a temp file
   - Run `node --check temp/ck.js`
3. Restart the server: kill uvicorn and re-run
4. Server start: `python app.py` or `uvicorn app:app --host 0.0.0.0 --port 8000`
5. Access at `http://localhost:8000`

### Startup files
- `iniciar.bat` - Start script
- `run_hidden.vbs` - Run without console window
- `starter.pyw` - Python launcher (windowless)
- `instalar_servico.bat` / `desinstalar_servico.bat` - Windows service install/uninstall
