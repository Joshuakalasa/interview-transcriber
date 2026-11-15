"""Runs whisper.cpp (whisper-cli.exe) as a background process and parses its JSON."""
from __future__ import annotations

import json
import re
from pathlib import Path

from PySide6.QtCore import QObject, QProcess, Signal

from .storage import Segment

PROGRESS_RE = re.compile(r"progress\s*=\s*(\d+)\s*%")


def parse_whisper_json(path: Path) -> list[Segment]:
    raw = path.read_bytes().decode("utf-8", errors="replace")
    data = json.loads(raw)
    segments: list[Segment] = []
    for item in data.get("transcription", []):
        text = (item.get("text") or "").strip()
        if not text:
            continue
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
              language: str = "en", threads: int = 4, prompt: str = "") -> None:
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
                "-oj", "-of", str(self.out_base), "-pp"]
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
            segments = parse_whisper_json(json_path)
        except Exception as e:
            self.failed.emit(f"Could not read whisper output: {e}")
            return
        self.progress.emit(100)
        self.finished.emit(segments)
