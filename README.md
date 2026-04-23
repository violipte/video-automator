# Video Automator

Automated YouTube video production pipeline. Replaces Adobe Premiere Pro with Python + FFmpeg for high-volume content creation.

**Produces 12+ videos/day** across multiple channels with 1-click automation.

## What It Does

Complete pipeline from theme to published video:

1. **Script Generation** - Multi-step LLM pipelines (Claude, GPT, Gemini) generate long-form scripts
2. **Narration** - TTS via ai33.pro (ElevenLabs + Minimax) with automatic chunking for long texts, and Inworld TTS as fallback provider
3. **Video Assembly** - Ken Burns effects on images, subtitle burning, overlays, audio mixing
4. **Production** - "Produzir Tudo" runs the entire pipeline for all channels of a date

## Features

- **Temas Grid** - Notion-style grid organizing channels x dates with themes, titles, scripts
- **LLM Pipelines** - Multi-step script generation with 3 step types (LLM, Text, Code)
- **Multiple LLM Providers** - Claude API, Claude CLI (Max plan, $0), GPT, Gemini (free tier)
- **Fallback Chain** - If one provider fails, automatically tries the next
- **TTS Narration** - ElevenLabs + Minimax (primary) with Inworld TTS fallback (automatic on primary failure), voice cloning on both platforms
- **Ken Burns Effects** - Smooth zoom/pan on images (OpenCV warpAffine, zero jitter)
- **Subtitle System** - Whisper transcription → SRT correction → ASS with custom styling
- **Visual Adjustments** - Exposure, contrast, saturation, color balance, vignette
- **Overlays** - Black-key overlays, green-screen CTA, frame overlays (moldura)
- **Metadata Stripping** - Removes encoder fingerprints from final MP4
- **Parallel Processing** - Roteiros generated in parallel, render queue with pipeline
- **Auto/Manual Modes** - Separated states, shared render queue, no conflicts
- **Remote Render Worker** - VPS coordinates jobs, local GPU worker (RTX 3060) renders, stale-job recovery on worker crash
- **Whisper Crash Isolation** - Transcription in subprocess, auto-fallback to CPU on native cuDNN crash
- **Loop Repass** - End-of-loop reprocessing of dates with errors (max 2 retries per date)
- **Resilience** - Auto-resume, health monitor, timeouts, retry on failures
- **Chat Assistant** - Integrated Claude chat for theme/title generation

## Tech Stack

- **Python 3.13+** with FastAPI + uvicorn
- **FFmpeg** - Video assembly, encoding (NVENC + libx264)
- **OpenCV** - Ken Burns effect rendering
- **faster-whisper** - GPU-accelerated transcription
- **httpx** - HTTP client for all APIs

## Requirements

- Windows 10/11
- NVIDIA GPU (RTX series recommended for NVENC)
- Python 3.13+
- FFmpeg in PATH
- CUDA toolkit (for faster-whisper GPU mode)

## Setup

1. Clone the repository
2. Install dependencies:
```bash
pip install fastapi uvicorn httpx opencv-python numpy pillow psutil faster-whisper
```
3. Copy example configs:
```bash
copy credentials.example.json credentials.json
copy config.example.json config.json
copy templates.example.json templates.json
copy pipelines.example.json pipelines.json
```
4. Edit the config files with your API keys and paths
5. Start the server:
```bash
python app.py
```
6. Open http://localhost:8500

## Configuration

### API Keys Required
- **ai33.pro** - Primary TTS (ElevenLabs + Minimax proxy)
- **Inworld TTS** - Secondary TTS fallback (optional, triggers automatically on primary failure)
- **Claude/GPT/Gemini** - Script generation (at least one)
- **Claude CLI** - Free with Max plan (optional, recommended)

### Templates
Each template defines a video style: resolution, background images/videos, overlays, subtitle style, voice, output folder. Configure via the Templates tab in the UI.

### Pipelines
Multi-step LLM chains for script generation. Each step can be LLM (API call), Text (variable substitution), or Code (Python execution). Configure via the Roteiros tab.

## Architecture

Single-file SPA: FastAPI backend serves an inline HTML/JS dashboard. All UI lives inside `app.py` as a Python string.

Key modules:
- `app.py` - Server + UI (~8000 lines)
- `engine.py` - Video assembly engine
- `orchestrator.py` - Production orchestrator (3-phase pipeline + end-of-loop repass)
- `scriptwriter.py` - LLM pipeline executor
- `narrator.py` - Primary TTS narration (ai33.pro) with chunking
- `narrator_inworld.py` - Fallback TTS narration (Inworld)
- `render_queue.py` - Shared render queue with stale-job recovery
- `render_worker.py` - Remote GPU worker (runs on operator's PC)
- `_whisper_subprocess.py` - Isolated Whisper transcription (crash protection)
- `production_log.py` - Persistent production state

See `CLAUDE.md` for full technical documentation.

## License

Private project. Not for redistribution without permission.
