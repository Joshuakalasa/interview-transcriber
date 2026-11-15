"""Audio conversion: anything -> 16 kHz mono 16-bit WAV (what whisper.cpp expects)."""
from __future__ import annotations

import subprocess
from pathlib import Path

import numpy as np
import soundfile as sf

TARGET_SR = 16000
BLOCK = 65536  # frames per block: keeps memory low for long recordings


class ConversionError(RuntimeError):
    pass


def duration_seconds(path: Path) -> float:
    try:
        return float(sf.info(str(path)).duration)
    except Exception:
        return 0.0


def _convert_with_ffmpeg(ffmpeg: Path, src: Path, dst: Path) -> None:
    cmd = [str(ffmpeg), "-y", "-hide_banner", "-loglevel", "error", "-i", str(src),
           "-ar", str(TARGET_SR), "-ac", "1", "-c:a", "pcm_s16le", str(dst)]
    flags = getattr(subprocess, "CREATE_NO_WINDOW", 0)
    res = subprocess.run(cmd, capture_output=True, text=True, creationflags=flags)
    if res.returncode != 0:
        raise ConversionError(res.stderr.strip() or "ffmpeg failed")


def _convert_with_soundfile(src: Path, dst: Path, raw_samplerate: int | None = None) -> None:
    """Stream-convert src to dst. raw_samplerate: src is headerless 16-bit mono PCM."""
    open_kw = {}
    if raw_samplerate:
        open_kw = dict(samplerate=raw_samplerate, channels=1, format="RAW", subtype="PCM_16")
        src_rate = raw_samplerate
    else:
        try:
            src_rate = sf.info(str(src)).samplerate
        except Exception as e:
            raise ConversionError(
                f"Cannot read '{src.name}'. Supported without ffmpeg: WAV, MP3, FLAC, OGG. "
                f"Install ffmpeg (tools/ffmpeg/ffmpeg.exe) for M4A/AAC/video files.\n{e}"
            ) from e

    resampler = None
    if src_rate != TARGET_SR:
        import soxr
        resampler = soxr.ResampleStream(src_rate, TARGET_SR, 1, dtype="float32",
                                        quality="HQ")

    with sf.SoundFile(str(dst), "w", samplerate=TARGET_SR, channels=1,
                      subtype="PCM_16") as out:
        for block in sf.blocks(str(src), blocksize=BLOCK, dtype="float32",
                               always_2d=True, **open_kw):
            mono = block.mean(axis=1).astype(np.float32)
            if resampler is not None:
                mono = resampler.resample_chunk(mono)
            if mono.size:
                out.write(mono)
        if resampler is not None:
            tail = resampler.resample_chunk(np.zeros(0, dtype=np.float32), last=True)
            if tail.size:
                out.write(tail)


def to_whisper_wav(src: Path, dst: Path, ffmpeg: Path | None = None) -> None:
    src, dst = Path(src), Path(dst)
    tmp = dst.with_name(dst.stem + "_converting.wav")
    if ffmpeg and Path(ffmpeg).exists():
        _convert_with_ffmpeg(Path(ffmpeg), src, tmp)
    else:
        _convert_with_soundfile(src, tmp)
    tmp.replace(dst)


def raw_pcm_to_whisper_wav(pcm: Path, samplerate: int, dst: Path) -> None:
    """Convert a crash-safe raw recording (16-bit mono PCM, no header) to audio.wav."""
    dst = Path(dst)
    tmp = dst.with_name(dst.stem + "_converting.wav")
    _convert_with_soundfile(Path(pcm), tmp, raw_samplerate=samplerate)
    tmp.replace(dst)


def is_whisper_ready(path: Path) -> bool:
    try:
        info = sf.info(str(path))
    except Exception:
        return False
    return info.samplerate == TARGET_SR and info.channels == 1 and info.subtype == "PCM_16"
