"""Crash-safe microphone recording with pause and a live level meter.

Audio is written as raw 16-bit PCM (recording.pcm) and flushed to disk every few
seconds, with the sample rate stored next to it (recording.json). Raw PCM has no
header that needs finalising, so if the app or the PC crashes, everything up to the
last flush can be recovered. On Stop it is converted to audio.wav.
"""
from __future__ import annotations

import json
import os
import queue
import threading
import time
from pathlib import Path

import numpy as np
import sounddevice as sd

from . import audio_utils
from .audio_utils import TARGET_SR

PCM_NAME = "recording.pcm"
PCM_INFO_NAME = "recording.json"
FLUSH_EVERY_S = 3.0


def list_input_devices() -> list[tuple[int, str]]:
    devices = []
    try:
        hostapis = sd.query_hostapis()
        for idx, d in enumerate(sd.query_devices()):
            if d["max_input_channels"] > 0:
                api = hostapis[d["hostapi"]]["name"]
                # MME/WASAPI duplicates are common on Windows; show the API to tell apart.
                devices.append((idx, f"{d['name']}  [{api}]"))
    except Exception:
        pass
    return devices


def pending_recording(folder: Path) -> tuple[Path, int] | None:
    """An unfinished recording left in folder (after a crash), as (pcm_path, samplerate)."""
    pcm, info = Path(folder) / PCM_NAME, Path(folder) / PCM_INFO_NAME
    if not pcm.exists() or pcm.stat().st_size == 0:
        return None
    try:
        sr = int(json.loads(info.read_text(encoding="utf-8"))["samplerate"])
    except Exception:
        sr = TARGET_SR
    return pcm, sr


def finalize_recording(folder: Path, dst: Path) -> None:
    """Convert recording.pcm -> dst (audio.wav) and remove the raw files."""
    found = pending_recording(folder)
    if not found:
        raise FileNotFoundError("No recording data found.")
    pcm, sr = found
    audio_utils.raw_pcm_to_whisper_wav(pcm, sr, dst)
    discard_recording(folder)


def discard_recording(folder: Path) -> None:
    for name in (PCM_NAME, PCM_INFO_NAME):
        (Path(folder) / name).unlink(missing_ok=True)


class Recorder:
    """Records mono 16-bit audio. Tries 16 kHz directly; falls back to the device's rate.

    The audio callback only puts data on a queue; a writer thread does the disk I/O,
    so a slow disk never causes dropped audio.
    """

    def __init__(self) -> None:
        self.stream: sd.InputStream | None = None
        self.file = None
        self.queue: queue.Queue = queue.Queue()
        self.writer: threading.Thread | None = None
        self.paused = False
        self.level = 0.0               # 0..1 for the meter
        self.samplerate = TARGET_SR
        self.frames_written = 0
        self.folder: Path | None = None
        self.error: str | None = None

    @property
    def is_recording(self) -> bool:
        return self.stream is not None

    @property
    def elapsed_seconds(self) -> float:
        return self.frames_written / float(self.samplerate) if self.samplerate else 0.0

    def _callback(self, indata, frames, time_info, status) -> None:
        mono = indata[:, 0].copy()
        rms = float(np.sqrt(np.mean((mono.astype(np.float32) / 32768.0) ** 2)))
        self.level = min(1.0, rms * 4)
        if not self.paused:
            self.queue.put(mono)

    def _write_loop(self) -> None:
        last_flush = time.monotonic()
        while True:
            chunk = self.queue.get()
            if chunk is None:
                break
            try:
                self.file.write(chunk.astype("<i2").tobytes())
                self.frames_written += len(chunk)
                if time.monotonic() - last_flush > FLUSH_EVERY_S:
                    self.file.flush()
                    os.fsync(self.file.fileno())   # really on disk, survives power loss
                    last_flush = time.monotonic()
            except Exception as e:  # disk full etc.
                self.error = str(e)

    def start(self, folder: Path, device: int | None = None) -> None:
        self.folder = Path(folder)
        self.paused = False
        self.frames_written = 0
        self.error = None
        try:
            sd.check_input_settings(device=device, samplerate=TARGET_SR, channels=1,
                                    dtype="int16")
            self.samplerate = TARGET_SR
        except Exception:
            info = sd.query_devices(device, "input")
            self.samplerate = int(info["default_samplerate"])

        discard_recording(self.folder)
        (self.folder / PCM_INFO_NAME).write_text(
            json.dumps({"samplerate": self.samplerate, "started": time.time()}), encoding="utf-8")
        self.file = open(self.folder / PCM_NAME, "wb")
        self.writer = threading.Thread(target=self._write_loop, daemon=True)
        self.writer.start()
        try:
            self.stream = sd.InputStream(device=device, samplerate=self.samplerate, channels=1,
                                         dtype="int16", callback=self._callback, blocksize=1024)
            self.stream.start()
        except Exception:
            self.stream = None
            self._close_writer()
            discard_recording(self.folder)
            raise

    def pause(self, value: bool) -> None:
        self.paused = value

    def _close_writer(self) -> None:
        if self.writer is not None:
            self.queue.put(None)
            self.writer.join()
            self.writer = None
        if self.file is not None:
            self.file.flush()
            os.fsync(self.file.fileno())
            self.file.close()
            self.file = None

    def stop(self) -> Path | None:
        """Stop capturing. The raw file stays until finalize_recording() converts it."""
        if self.stream is not None:
            self.stream.stop()
            self.stream.close()
            self.stream = None
        self._close_writer()
        self.level = 0.0
        return self.folder
