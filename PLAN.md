# Interview Transcriber — Build Plan

Offline interview recorder + transcriber for Windows, built on **whisper.cpp**.

## Decisions (confirmed)

| Topic | Decision |
|---|---|
| Language | English only → use English-only Whisper models (`*.en`), which are more accurate and faster for English |
| Setting | In-person interviews → one microphone (laptop mic or USB/lapel mic) |
| Output | Plain transcript with timestamps (speaker labels later; the data model already has a `speaker` field) |
| Hardware | No GPU → CPU build of whisper.cpp, quantized models |
| Platform | Windows 10/11 |
| Users | A team → installer, per-user settings, shared interviews folder option, consistent export layout |

## Model choice for CPU-only laptops

Rough speeds on a 2-core/4-thread laptop CPU (e.g. i7-7500U, 8 GB RAM) for a **1-hour interview**:

| Model | Size | Approx. time | Use |
|---|---|---|---|
| `ggml-base.en-q5_1` | ~57 MB | ~6–12 min | **Default** — fast, good for clear audio |
| `ggml-small.en-q5_1` | ~181 MB | ~20–35 min | Better accuracy; noisy rooms, accents |
| `ggml-medium.en-q5_0` | ~514 MB | 1 h+ | Only for important recordings, overnight |

Users pick the model per interview. Faster PCs on the team will simply run quicker.

## Tech stack

- **Python 3 + PySide6 (Qt)** — desktop UI, audio playback (QMediaPlayer)
- **sounddevice + soundfile** — microphone recording to WAV
- **soxr** — high-quality resampling of imported audio to 16 kHz mono
- **whisper.cpp `whisper-cli.exe`** — run as a separate process (JSON output, progress reporting); easy to upgrade independently
- **ffmpeg.exe** (optional, bundled) — import of M4A/AAC/video files
- **python-docx** — Word export
- **PyInstaller + Inno Setup** — single Windows installer for the team

## Data layout (one folder per interview)

```
Documents\Interviews\2026-11-15_Jane-Doe-interview\
  meta.json         title, interviewee, interviewer, date, location, notes, model, status
  audio.wav         16 kHz mono PCM (what whisper.cpp needs)
  original.<ext>    imported file, kept untouched
  transcript.json   [{start_ms, end_ms, speaker, text}, ...]  ← user edits saved here
  whisper_raw.json  raw whisper.cpp output (for reprocessing/debugging)
```

The interviews root folder is configurable, so a team can point it at a shared drive if they want.

## Phases

### Phase 1 — MVP (building now)
- [x] Create / list / open interviews with details (title, interviewee, interviewer, date, location, notes)
- [x] Record from microphone: device picker, Record / Pause / Stop, timer, level meter
- [x] Import existing audio (WAV, MP3, FLAC, OGG; M4A etc. when ffmpeg is present)
- [x] Transcribe with whisper.cpp in the background: model picker, progress bar, cancel
- [x] Transcript table with timestamps; edit text; double-click a line to play that moment
- [x] Custom vocabulary prompt (names, organisation terms) to improve spelling
- [x] Export: TXT, DOCX (with interview header), SRT
- [x] Settings dialog (folders, whisper-cli path, threads, vocabulary, organisation name)
- [x] Setup script that downloads whisper.cpp + a model

### Phase 2 — Editor & quality (done)
- [x] Speaker workflow: buttons + Ctrl+1…9 (auto-advance to next line), drop-down per line, add, rename everywhere
- [x] Split / merge / delete lines, undo (100 steps), find & replace
- [x] Playback: Ctrl+Space, ±5 s, play selected line, speed 0.5×–1.5×, highlight of the line being played
- [x] One line per sentence (uses whisper word timings; joins sentences cut across whisper blocks)
- [x] Silero VAD “skip silence” (word timings re-mapped to the original audio)
- [x] Paragraph-layout export (by speaker, or by pauses when no speakers)
- [x] Crash-safe recording (raw PCM flushed every 3 s, recovery prompt)
- [x] Unit tests (`python -m unittest`)

### Phase 3 — Team readiness
- Automatic speaker diarization offline (sherpa-onnx speaker models)
- Optional password encryption of interview folders
- PDF export, organisation logo/template on exports
- Installer (PyInstaller + Inno Setup) bundling Python, whisper.cpp, ffmpeg and the default model
- Model manager inside the app (download / remove models)

### Phase 4 — Optional
- Local LLM summary, key quotes, and question/answer extraction (still offline)

## Risks / notes
- CPU-only transcription is slow for long interviews → default to `base.en`, run in background, allow cancel.
- Microphone quality matters more than model size; recommend a cheap USB or lapel mic for the team.
- Whisper can occasionally “hallucinate” text during long silences → VAD in Phase 2.
- Interviews are sensitive: everything stays on the machine; nothing is uploaded.
