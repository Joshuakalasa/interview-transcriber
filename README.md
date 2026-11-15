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
5. Label speakers: click a line, press **Ctrl+1** (Interviewer) or **Ctrl+2** (Respondent) — it jumps to the next line, so you can label a whole interview from the keyboard. **Rename…** changes a name everywhere (e.g. Respondent → Mrs Banda).
6. **Export Word / TXT / SRT** — choose *Line by line* or *Paragraphs* layout.

### Keyboard shortcuts (also under Help → Keyboard shortcuts)

| Keys | Action |
|---|---|
| Ctrl+Space | Play / pause |
| Alt+Left / Alt+Right | Back / forward 5 seconds |
| Ctrl+J | Play the selected line |
| Ctrl+1 … Ctrl+9, Ctrl+0 | Set / clear speaker of selected lines |
| Ctrl+M | Merge selected lines |
| Ctrl+K | Split line (at the text cursor while editing) |
| Ctrl+Delete | Delete selected lines |
| Ctrl+Z | Undo |
| Ctrl+F, F3 | Find & replace, find next |

### Settings worth knowing
- **Skip silence** (on by default): faster on long interviews and avoids made-up text in pauses.
- **One line per sentence** (on by default): splits whisper's ~30-second blocks into sentences.
- **Recording is crash-safe**: audio is flushed to disk every few seconds. If the app or PC dies mid-interview, opening that interview offers to recover the recording.

## Tests

```powershell
.venv\Scripts\python -m unittest -v
```

Tip: in **File → Settings → Vocabulary hint**, list names and terms that often come up (people, places, organisation words). Whisper will spell them correctly more often.

## Where data lives

- Interviews: `Documents\Interviews\<date>_<title>\` (changeable in Settings, e.g. to a shared drive)
- Per-user settings: `%APPDATA%\InterviewTranscriber\settings.json`

## Project layout

```
main.py               entry point
app/ui.py             main window
app/transcript_editor.py  transcript table, speakers, split/merge, find & replace, undo
app/editing.py        pure editing operations (tested)
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
