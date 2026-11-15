"""Export transcripts to TXT, DOCX and SRT."""
from __future__ import annotations

from pathlib import Path

from .storage import Interview, Segment


def fmt_time(ms: int, srt: bool = False) -> str:
    ms = max(0, int(ms))
    h, rem = divmod(ms, 3_600_000)
    m, rem = divmod(rem, 60_000)
    s, ms_ = divmod(rem, 1000)
    if srt:
        return f"{h:02d}:{m:02d}:{s:02d},{ms_:03d}"
    return f"{h:02d}:{m:02d}:{s:02d}"


def _header_rows(iv: Interview, duration_s: float, organisation: str) -> list[tuple[str, str]]:
    m = iv.meta
    rows = [
        ("Organisation", organisation),
        ("Interviewee", m.get("interviewee", "")),
        ("Interviewer", m.get("interviewer", "")),
        ("Date", m.get("date", "")),
        ("Location", m.get("location", "")),
        ("Duration", fmt_time(int(duration_s * 1000)) if duration_s else ""),
        ("Notes", m.get("notes", "")),
    ]
    return [(k, v) for k, v in rows if v]


def _line(seg: Segment, timestamps: bool) -> str:
    parts = []
    if timestamps:
        parts.append(f"[{fmt_time(seg.start_ms)}]")
    if seg.speaker:
        parts.append(f"{seg.speaker}:")
    parts.append(seg.text)
    return " ".join(parts)


def export_txt(iv: Interview, segments: list[Segment], dst: Path, *, timestamps: bool = True,
               duration_s: float = 0.0, organisation: str = "") -> None:
    lines = [iv.title, "=" * len(iv.title)]
    lines += [f"{k}: {v}" for k, v in _header_rows(iv, duration_s, organisation)]
    lines.append("")
    lines += [_line(s, timestamps) for s in segments]
    Path(dst).write_text("\n".join(lines) + "\n", encoding="utf-8")


def export_srt(segments: list[Segment], dst: Path) -> None:
    out = []
    for i, s in enumerate(segments, 1):
        text = f"{s.speaker}: {s.text}" if s.speaker else s.text
        out.append(f"{i}\n{fmt_time(s.start_ms, True)} --> {fmt_time(s.end_ms, True)}\n{text}\n")
    Path(dst).write_text("\n".join(out), encoding="utf-8")


def export_docx(iv: Interview, segments: list[Segment], dst: Path, *, timestamps: bool = True,
                duration_s: float = 0.0, organisation: str = "") -> None:
    from docx import Document
    from docx.shared import Pt, RGBColor

    doc = Document()
    style = doc.styles["Normal"]
    style.font.name = "Calibri"
    style.font.size = Pt(11)

    doc.add_heading(iv.title, level=1)
    rows = _header_rows(iv, duration_s, organisation)
    if rows:
        table = doc.add_table(rows=0, cols=2)
        table.style = "Light Grid Accent 1"
        for k, v in rows:
            cells = table.add_row().cells
            cells[0].text = k
            cells[1].text = v
            cells[0].paragraphs[0].runs[0].bold = True
    doc.add_heading("Transcript", level=2)

    for s in segments:
        p = doc.add_paragraph()
        if timestamps:
            r = p.add_run(f"[{fmt_time(s.start_ms)}]  ")
            r.font.color.rgb = RGBColor(0x80, 0x80, 0x80)
            r.font.size = Pt(9)
        if s.speaker:
            p.add_run(f"{s.speaker}: ").bold = True
        p.add_run(s.text)

    doc.save(str(dst))
