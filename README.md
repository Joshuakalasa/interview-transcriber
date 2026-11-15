# Interview Transcriber

Record interviews and transcribe them **offline** on Windows using [whisper.cpp](https://github.com/ggml-org/whisper.cpp). Nothing is uploaded.

## Setup (developer / first run)

```powershell
python -m venv .venv
.venv\Scripts\pip install -r requirements.txt
powershell -ExecutionPolicy Bypass -File scripts\setup_whisper.ps1                    # whisper.cpp + base.en model
powershell -ExecutionPolicy Bypass -File scripts\setup_whisper.ps1 -Model small.en-q5_1   # optional, more accurate
.venv\Scripts\python main.py
```

Optional: put `ffmpeg.exe` in `tools\ffmpeg\` to import M4A/AAC/video files (WAV, MP3, FLAC, OGG work without it).

## Using it

1. **+ New interview** → type a title, then fill in interviewee, location, etc.
2. Choose the microphone → **● Record** → **Pause/Resume** → **■ Stop**. (Or **Import audio…** for an existing recording.)
3. Pick a model → **Transcribe**. A progress bar shows how far along it is. You can cancel at any time.
4. Double-click a **Time** to listen to that moment; double-click **Text** or **Speaker** to correct it (saved automatically).
5. **Export Word / TXT / SRT**.

Tip: in **File → Settings → Vocabulary hint**, list names and terms that often come up (people, places, organisation words). Whisper will spell them correctly more often.

## Where data lives

- Interviews: `Documents\Interviews\<date>_<title>\` (changeable in Settings, e.g. to a shared drive)
- Per-user settings: `%APPDATA%\InterviewTranscriber\settings.json`

## Project layout

```
main.py               entry point
app/ui.py             main window
app/recorder.py       microphone capture
app/audio_utils.py    conversion to 16 kHz mono WAV
app/transcriber.py    runs whisper-cli.exe, parses JSON
app/exporter.py       TXT / DOCX / SRT
app/storage.py        interview folders, metadata, transcript
scripts/setup_whisper.ps1
tools/whisper/        whisper-cli.exe + DLLs (downloaded)
models/               ggml-*.bin models (downloaded)
```

See `PLAN.md` for the roadmap.
