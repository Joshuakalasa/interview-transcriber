"""Runs whisper.cpp (whisper-cli.exe) as a background process and parses its JSON."""
from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from .storage import Segment

PROGRESS_RE = re.compile(r"progress\s*=\s*(\d+)\s*%")
SENTENCE_END = re.compile(r"[.!?][\"')\]]*$")
ABBREVIATION = re.compile(r"\b(Mr|Mrs|Ms|Dr|Prof|St|Mt|vs|etc|e\.g|i\.e|No)\.$", re.IGNORECASE)
MIN_SENTENCE_CHARS = 25   # avoid tiny lines like "Sure." on their own


def clean_text(text: str) -> str:
    """Tidy whisper output: trim and remove stray quote marks wrapping a passage."""
    text = text.strip()
    if text.count('"') == 2 and text.startswith('"') and text.endswith('"'):
        text = text[1:-1].strip()
    elif text.count('"') == 1 and (text.startswith('"') or text.endswith('"')):
        text = text.strip('"').strip()
    return text


MAX_LINE_CHARS = 300      # if nobody uses punctuation, still break at a whisper segment end


def _timed_tokens(items: list[dict], vad: bool) -> list[tuple[str, int, int, bool]]:
    """Flatten all tokens to (text, from_ms, to_ms, ends_whisper_segment).

    With VAD, whisper.cpp maps *segment* times back to the original audio but leaves
    *token* times on the silence-removed timeline, so token times are stretched
    linearly into their segment's corrected time span.
    """
    out = []
    for item in items:
        toks = [t for t in item.get("tokens", []) if not t.get("text", "").startswith("[_")]
        if not toks:
            continue
        t_from = [int(t.get("offsets", {}).get("from", 0)) for t in toks]
        t_to = [int(t.get("offsets", {}).get("to", 0)) for t in toks]
        if vad:
            s0 = int(item.get("offsets", {}).get("from", 0))
            s1 = int(item.get("offsets", {}).get("to", 0))
            a, b = t_from[0], max(t_to)
            scale = (s1 - s0) / (b - a) if b > a else 0.0
            t_from = [int(s0 + (x - a) * scale) for x in t_from]
            t_to = [int(s0 + (x - a) * scale) for x in t_to]
        for i, t in enumerate(toks):
            out.append((t.get("text", ""), t_from[i], t_to[i], i == len(toks) - 1))
    return out


def _split_by_sentences(tokens: list[tuple[str, int, int, bool]]) -> list[Segment]:
    """Cut the token stream at sentence ends (works across whisper segment boundaries)."""
    out: list[Segment] = []
    text, start, end = "", None, 0

    def emit():
        cleaned = clean_text(text)
        if cleaned:
            out.append(Segment(start or 0, max(end, start or 0), cleaned))

    for i, (t, t0, t1, seg_end) in enumerate(tokens):
        if start is None and any(c.isalnum() for c in t):
            start = t0
        text += t
        end = max(end, t1)
        nxt = tokens[i + 1][0] if i + 1 < len(tokens) else " "
        stripped = text.strip()
        sentence_done = (SENTENCE_END.search(t.strip()) and nxt.startswith(" ")   # not "3.5"
                         and len(stripped) >= MIN_SENTENCE_CHARS
                         and not ABBREVIATION.search(stripped))
        too_long = seg_end and len(stripped) >= MAX_LINE_CHARS
        if sentence_done or too_long:
            emit()
            text, start, end = "", None, 0
    if text.strip():
        emit()
    return out


def parse_whisper_json(path: Path, split_sentences: bool = True, vad: bool = False) -> list[Segment]:
    raw = path.read_bytes().decode("utf-8", errors="replace")
    items = json.loads(raw).get("transcription", [])
    if split_sentences and any(i.get("tokens") for i in items):
        return _split_by_sentences(_timed_tokens(items, vad))
    segments: list[Segment] = []
    for item in items:
        text = clean_text(item.get("text") or "")
        if text:
            off = item.get("offsets", {})
            segments.append(Segment(int(off.get("from", 0)), int(off.get("to", 0)), text))
    return segments


class TranscribeJob(QObject):
    progress = Signal(int)          # 0..100
    log = Signal(str)
    finished = Signal(list)         # list[Segment]
    failed = Signal(str)

    def __init__(self, parent=None) -> None:
        super().__init__(parent)
        self.proc: QProcess | None = None
        self.out_base: Path | None = None
        self._cancelled = False
        self._tail: list[str] = []

    @property
    def running(self) -> bool:
        return self.proc is not None and self.proc.state() != QProcess.NotRunning

    def start(self, cli: Path, model: Path, wav: Path, out_base: Path, *,
              language: str = "en", threads: int = 4, prompt: str = "",
              vad_model: Path | None = None, split_sentences: bool = True) -> None:
        self.split_sentences = split_sentences
        self.vad_used = bool(vad_model and Path(vad_model).exists())
        cli, model = Path(cli), Path(model)
        if not cli.exists():
            self.failed.emit(f"whisper-cli not found:\n{cli}\n\nRun setup or fix the path in Settings.")
            return
        if not model.exists():
            self.failed.emit(f"Model not found:\n{model}")
            return

        self.out_base = Path(out_base)
        json_path = self.out_base.with_suffix(".json")
        if json_path.exists():
            json_path.unlink()

        args = ["-m", str(model), "-f", str(wav), "-l", language, "-t", str(threads),
                "-ojf", "-of", str(self.out_base), "-pp"]   # -ojf: includes word timings
        if vad_model and Path(vad_model).exists():
            # Skip silence: faster on long interviews and fewer made-up phrases in pauses.
            args += ["--vad", "-vm", str(vad_model)]
        if prompt.strip():
            args += ["--prompt", prompt.strip()]

        self._cancelled = False
        self._tail = []
        self.proc = QProcess(self)
        self.proc.setProcessChannelMode(QProcess.MergedChannels)
        self.proc.setWorkingDirectory(str(cli.parent))  # so its DLLs are found
        self.proc.readyReadStandardOutput.connect(self._on_output)
        self.proc.finished.connect(self._on_finished)
        self.proc.errorOccurred.connect(self._on_error)
        self.progress.emit(0)
        self.proc.start(str(cli), args)

    def cancel(self) -> None:
        if self.running:
            self._cancelled = True
            self.proc.kill()

    def _on_output(self) -> None:
        text = bytes(self.proc.readAllStandardOutput()).decode("utf-8", errors="replace")
        for line in text.splitlines():
            m = PROGRESS_RE.search(line)
            if m:
                self.progress.emit(int(m.group(1)))
            elif line.strip():
                self._tail = (self._tail + [line])[-30:]
                self.log.emit(line)

    def _on_error(self, err) -> None:
        if err == QProcess.FailedToStart:
            self.failed.emit("Could not start whisper-cli.exe (missing DLLs or blocked by antivirus?).")

    def _on_finished(self, code: int, status) -> None:
        if self._cancelled:
            self.failed.emit("Transcription cancelled.")
            return
        json_path = self.out_base.with_suffix(".json")
        if code != 0 or not json_path.exists():
            detail = "\n".join(self._tail[-10:])
            self.failed.emit(f"whisper-cli exited with code {code}.\n\n{detail}")
            return
        try:
            segments = parse_whisper_json(json_path, self.split_sentences, self.vad_used)
        except Exception as e:
            self.failed.emit(f"Could not read whisper output: {e}")
            return
        self.progress.emit(100)
        self.finished.emit(segments)
