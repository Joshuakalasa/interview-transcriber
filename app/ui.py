"""Main window: interview list, details, recorder, transcription, transcript editor, export."""
from __future__ import annotations

import os
import shutil
from pathlib import Path

from PySide6.QtCore import QThread, QTimer, Qt, QUrl, Signal
from PySide6.QtGui import QAction, QColor
from PySide6.QtMultimedia import QAudioOutput, QMediaPlayer
from PySide6.QtWidgets import (
    QAbstractItemView, QApplication, QCheckBox, QComboBox, QDialog, QDialogButtonBox,
    QFileDialog, QFormLayout, QGroupBox, QHBoxLayout, QHeaderView, QInputDialog, QLabel,
    QLineEdit, QListWidget, QListWidgetItem, QMainWindow, QMessageBox, QPlainTextEdit,
    QProgressBar, QPushButton, QSpinBox, QSplitter, QTableWidget, QTableWidgetItem,
    QVBoxLayout, QWidget,
)

from . import audio_utils, exporter
from .config import Settings
from .recorder import Recorder, list_input_devices
from .storage import Interview, Segment, create_interview, list_interviews
from .transcriber import TranscribeJob

AUDIO_FILTER = "Audio (*.wav *.mp3 *.m4a *.flac *.ogg *.aac *.wma *.mp4);;All files (*.*)"


class Worker(QThread):
    """Runs a blocking function off the UI thread."""
    done = Signal(object)
    error = Signal(str)

    def __init__(self, fn, *args):
        super().__init__()
        self.fn, self.args = fn, args

    def run(self):
        try:
            self.done.emit(self.fn(*self.args))
        except Exception as e:  # report to UI
            self.error.emit(str(e))


# --------------------------------------------------------------------------- settings
class SettingsDialog(QDialog):
    def __init__(self, settings: Settings, parent=None):
        super().__init__(parent)
        self.setWindowTitle("Settings")
        self.s = settings
        form = QFormLayout(self)

        self.interviews = self._path_row(form, "Interviews folder", settings["interviews_dir"], folder=True)
        self.models = self._path_row(form, "Models folder", settings["models_dir"], folder=True)
        self.cli = self._path_row(form, "whisper-cli.exe", settings["whisper_cli"])
        self.ffmpeg = self._path_row(form, "ffmpeg.exe (optional)", settings["ffmpeg"])

        self.threads = QSpinBox()
        self.threads.setRange(1, 64)
        self.threads.setValue(int(settings["threads"]))
        form.addRow("CPU threads", self.threads)

        self.org = QLineEdit(settings["organisation"])
        form.addRow("Organisation (on exports)", self.org)
        self.interviewer = QLineEdit(settings["default_interviewer"])
        form.addRow("Default interviewer", self.interviewer)

        self.vocab = QPlainTextEdit(settings["vocabulary"])
        self.vocab.setPlaceholderText("Names and terms that often appear, e.g. Lusaka, MTN, Mwansa Chileshe")
        self.vocab.setFixedHeight(70)
        form.addRow("Vocabulary hint", self.vocab)

        buttons = QDialogButtonBox(QDialogButtonBox.Save | QDialogButtonBox.Cancel)
        buttons.accepted.connect(self.accept)
        buttons.rejected.connect(self.reject)
        form.addRow(buttons)
        self.resize(620, 0)

    def _path_row(self, form, label, value, folder=False) -> QLineEdit:
        edit = QLineEdit(value)
        btn = QPushButton("Browse…")

        def browse():
            if folder:
                p = QFileDialog.getExistingDirectory(self, label, edit.text())
            else:
                p, _ = QFileDialog.getOpenFileName(self, label, edit.text(), "Programs (*.exe)")
            if p:
                edit.setText(os.path.normpath(p))

        btn.clicked.connect(browse)
        row = QHBoxLayout()
        row.addWidget(edit)
        row.addWidget(btn)
        form.addRow(label, row)
        return edit

    def accept(self):
        self.s["interviews_dir"] = self.interviews.text().strip()
        self.s["models_dir"] = self.models.text().strip()
        self.s["whisper_cli"] = self.cli.text().strip()
        self.s["ffmpeg"] = self.ffmpeg.text().strip()
        self.s["threads"] = self.threads.value()
        self.s["organisation"] = self.org.text().strip()
        self.s["default_interviewer"] = self.interviewer.text().strip()
        self.s["vocabulary"] = self.vocab.toPlainText().strip()
        self.s.save()
        super().accept()


# --------------------------------------------------------------------------- main window
class MainWindow(QMainWindow):
    def __init__(self):
        super().__init__()
        self.settings = Settings()
        self.current: Interview | None = None
        self.segments: list[Segment] = []
        self.recorder = Recorder()
        self.job = TranscribeJob(self)
        self.workers: list[Worker] = []
        self._loading_table = False

        self.setWindowTitle("Interview Transcriber")
        self.resize(1200, 780)
        self._build_menu()
        self._build_ui()

        self.player = QMediaPlayer(self)
        self.audio_out = QAudioOutput(self)
        self.player.setAudioOutput(self.audio_out)
        self.player.positionChanged.connect(self._on_player_position)
        self.player.playbackStateChanged.connect(self._on_playback_state)

        self.job.progress.connect(self.progress.setValue)
        self.job.finished.connect(self._on_transcribed)
        self.job.failed.connect(self._on_transcribe_failed)

        self.meter_timer = QTimer(self, interval=100, timeout=self._update_meter)
        self.autosave_timer = QTimer(self, singleShot=True, interval=800, timeout=self._save_segments)

        self.refresh_list()
        self._set_interview(None)
        QTimer.singleShot(300, self._check_setup)

    # ---------------- UI construction
    def _build_menu(self):
        m = self.menuBar().addMenu("&File")
        for text, slot in [("New interview", self.new_interview), ("Import audio…", self.import_audio),
                           ("Settings…", self.open_settings), ("Open interviews folder", self.open_root)]:
            a = QAction(text, self)
            a.triggered.connect(slot)
            m.addAction(a)
        m.addSeparator()
        quit_ = QAction("Exit", self)
        quit_.triggered.connect(self.close)
        m.addAction(quit_)

    def _build_ui(self):
        splitter = QSplitter()
        self.setCentralWidget(splitter)

        # left: list
        left = QWidget()
        lv = QVBoxLayout(left)
        self.btn_new = QPushButton("+ New interview")
        self.btn_new.clicked.connect(self.new_interview)
        self.btn_import = QPushButton("Import audio…")
        self.btn_import.clicked.connect(self.import_audio)
        self.list = QListWidget()
        self.list.currentItemChanged.connect(self._on_select)
        lv.addWidget(self.btn_new)
        lv.addWidget(self.btn_import)
        lv.addWidget(self.list)
        splitter.addWidget(left)

        # right: workspace
        right = QWidget()
        rv = QVBoxLayout(right)

        # details
        details = QGroupBox("Interview details")
        df = QFormLayout(details)
        self.f_title = QLineEdit()
        self.f_interviewee = QLineEdit()
        self.f_interviewer = QLineEdit()
        self.f_date = QLineEdit()
        self.f_location = QLineEdit()
        self.f_notes = QLineEdit()
        self.meta_edits = {"title": self.f_title, "interviewee": self.f_interviewee,
                           "interviewer": self.f_interviewer, "date": self.f_date,
                           "location": self.f_location, "notes": self.f_notes}
        r1 = QHBoxLayout()
        r1.addWidget(self.f_title, 2)
        r1.addWidget(QLabel("Date"))
        r1.addWidget(self.f_date, 1)
        df.addRow("Title", r1)
        r2 = QHBoxLayout()
        r2.addWidget(self.f_interviewee)
        r2.addWidget(QLabel("Interviewer"))
        r2.addWidget(self.f_interviewer)
        df.addRow("Interviewee", r2)
        r3 = QHBoxLayout()
        r3.addWidget(self.f_location)
        r3.addWidget(QLabel("Notes"))
        r3.addWidget(self.f_notes, 2)
        df.addRow("Location", r3)
        for e in self.meta_edits.values():
            e.editingFinished.connect(self._save_meta)
        rv.addWidget(details)

        # recorder + transcribe row
        row = QHBoxLayout()
        rec = QGroupBox("Record")
        rl = QVBoxLayout(rec)
        self.device_combo = QComboBox()
        self._fill_devices()
        rl.addWidget(self.device_combo)
        rb = QHBoxLayout()
        self.btn_rec = QPushButton("● Record")
        self.btn_rec.setStyleSheet("QPushButton{color:#c62828;font-weight:bold}")
        self.btn_rec.clicked.connect(self.start_recording)
        self.btn_pause = QPushButton("Pause")
        self.btn_pause.setCheckable(True)
        self.btn_pause.toggled.connect(self._toggle_pause)
        self.btn_stop = QPushButton("■ Stop")
        self.btn_stop.clicked.connect(self.stop_recording)
        self.rec_time = QLabel("00:00:00")
        self.rec_time.setStyleSheet("font: 16px 'Consolas'")
        for w in (self.btn_rec, self.btn_pause, self.btn_stop, self.rec_time):
            rb.addWidget(w)
        rl.addLayout(rb)
        self.level = QProgressBar()
        self.level.setRange(0, 100)
        self.level.setTextVisible(False)
        self.level.setFixedHeight(10)
        rl.addWidget(self.level)
        row.addWidget(rec, 1)

        tr = QGroupBox("Transcribe")
        tl = QVBoxLayout(tr)
        tm = QHBoxLayout()
        tm.addWidget(QLabel("Model"))
        self.model_combo = QComboBox()
        tm.addWidget(self.model_combo, 1)
        tl.addLayout(tm)
        tb = QHBoxLayout()
        self.btn_transcribe = QPushButton("Transcribe")
        self.btn_transcribe.setStyleSheet("QPushButton{font-weight:bold}")
        self.btn_transcribe.clicked.connect(self.start_transcription)
        self.btn_cancel = QPushButton("Cancel")
        self.btn_cancel.clicked.connect(self.job.cancel)
        tb.addWidget(self.btn_transcribe)
        tb.addWidget(self.btn_cancel)
        tl.addLayout(tb)
        self.progress = QProgressBar()
        self.progress.setRange(0, 100)
        tl.addWidget(self.progress)
        row.addWidget(tr, 1)
        rv.addLayout(row)
        self._fill_models()

        # transcript table
        self.table = QTableWidget(0, 3)
        self.table.setHorizontalHeaderLabels(["Time", "Speaker", "Text"])
        hh = self.table.horizontalHeader()
        hh.setSectionResizeMode(0, QHeaderView.ResizeToContents)
        hh.setSectionResizeMode(1, QHeaderView.Interactive)
        hh.setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.setColumnWidth(1, 110)
        self.table.verticalHeader().setVisible(False)
        self.table.setWordWrap(True)
        self.table.setSelectionBehavior(QAbstractItemView.SelectRows)
        self.table.setEditTriggers(QAbstractItemView.DoubleClicked | QAbstractItemView.EditKeyPressed
                                   | QAbstractItemView.AnyKeyPressed)
        self.table.itemChanged.connect(self._on_cell_changed)
        self.table.cellDoubleClicked.connect(self._on_cell_double_clicked)
        rv.addWidget(self.table, 1)

        # bottom bar: playback + export
        bottom = QHBoxLayout()
        self.btn_play = QPushButton("▶ Play")
        self.btn_play.clicked.connect(self.toggle_play)
        self.play_pos = QLabel("00:00:00")
        bottom.addWidget(self.btn_play)
        bottom.addWidget(self.play_pos)
        bottom.addWidget(QLabel("  Tip: double-click a time to jump there."))
        bottom.addStretch(1)
        self.chk_ts = QCheckBox("Include timestamps")
        self.chk_ts.setChecked(True)
        bottom.addWidget(self.chk_ts)
        for label, fmt in (("Export Word", "docx"), ("Export TXT", "txt"), ("Export SRT", "srt")):
            b = QPushButton(label)
            b.clicked.connect(lambda _=False, f=fmt: self.export(f))
            bottom.addWidget(b)
        rv.addLayout(bottom)

        splitter.addWidget(right)
        splitter.setSizes([260, 940])
        self.statusBar().showMessage("Ready")

    def _fill_devices(self):
        self.device_combo.clear()
        self.device_combo.addItem("Default microphone", None)
        for idx, name in list_input_devices():
            self.device_combo.addItem(name, idx)
        saved = self.settings["input_device"]
        i = self.device_combo.findData(saved)
        if i >= 0:
            self.device_combo.setCurrentIndex(i)

    def _fill_models(self):
        self.model_combo.clear()
        models = self.settings.available_models()
        for m in models:
            self.model_combo.addItem(m)
        if not models:
            self.model_combo.addItem("(no models found – see README)")
        i = self.model_combo.findText(self.settings["model"])
        if i >= 0:
            self.model_combo.setCurrentIndex(i)

    def _check_setup(self):
        problems = []
        if not Path(self.settings["whisper_cli"]).exists():
            problems.append("• whisper-cli.exe was not found.")
        if not self.settings.available_models():
            problems.append("• No Whisper model found in the models folder.")
        if problems:
            QMessageBox.information(
                self, "Setup needed",
                "\n".join(problems) + "\n\nRun scripts\\setup_whisper.ps1 (see README), "
                "or set the paths in File → Settings. Recording still works without it.")

    # ---------------- interview list
    def refresh_list(self, select: Path | None = None):
        self.list.blockSignals(True)
        self.list.clear()
        for iv in list_interviews(Path(self.settings["interviews_dir"])):
            item = QListWidgetItem(self._list_label(iv))
            item.setData(Qt.UserRole, str(iv.folder))
            self.list.addItem(item)
            if select and Path(select) == iv.folder:
                self.list.setCurrentItem(item)
        self.list.blockSignals(False)
        if select:
            cur = self.list.currentItem()
            self._set_interview(Interview(Path(cur.data(Qt.UserRole))) if cur else None)

    @staticmethod
    def _list_label(iv: Interview) -> str:
        status = {"new": "○", "recorded": "◐", "transcribed": "●"}.get(iv.meta.get("status"), "○")
        return f"{status}  {iv.title}\n     {iv.meta.get('date', '')}  {iv.meta.get('interviewee', '')}"

    def _on_select(self, cur, _prev):
        if self.recorder.is_recording or self.job.running:
            return
        self._set_interview(Interview(Path(cur.data(Qt.UserRole))) if cur else None)

    def _set_interview(self, iv: Interview | None):
        self._save_segments()
        self.player.stop()
        self.player.setSource(QUrl())
        self.current = iv
        enabled = iv is not None
        for w in list(self.meta_edits.values()) + [self.btn_rec, self.btn_transcribe, self.table]:
            w.setEnabled(enabled)
        for k, e in self.meta_edits.items():
            e.setText(iv.meta.get(k, "") if iv else "")
        self.segments = iv.load_segments() if iv else []
        self._load_table()
        self.progress.setValue(100 if self.segments else 0)
        if iv and iv.has_audio:
            self.player.setSource(QUrl.fromLocalFile(str(iv.audio_path)))
            dur = audio_utils.duration_seconds(iv.audio_path)
            self.rec_time.setText(exporter.fmt_time(int(dur * 1000)))
        else:
            self.rec_time.setText("00:00:00")
        self._update_buttons()

    def _update_buttons(self):
        rec = self.recorder.is_recording
        busy = self.job.running
        has = self.current is not None
        self.btn_rec.setEnabled(has and not rec and not busy)
        self.btn_pause.setEnabled(rec)
        self.btn_stop.setEnabled(rec)
        self.btn_transcribe.setEnabled(has and not rec and not busy and self.current.has_audio)
        self.btn_cancel.setEnabled(busy)
        self.list.setEnabled(not rec and not busy)
        self.btn_new.setEnabled(not rec and not busy)
        self.btn_import.setEnabled(not rec and not busy)
        self.device_combo.setEnabled(not rec)

    def _save_meta(self):
        if not self.current:
            return
        for k, e in self.meta_edits.items():
            self.current.meta[k] = e.text().strip()
        self.current.save_meta()
        item = self.list.currentItem()
        if item:
            item.setText(self._list_label(self.current))

    def new_interview(self):
        title, ok = QInputDialog.getText(self, "New interview", "Title (e.g. 'Interview with Jane Doe'):")
        if not ok or not title.strip():
            return
        iv = create_interview(Path(self.settings["interviews_dir"]), title.strip(),
                              self.settings["default_interviewer"])
        self.refresh_list(select=iv.folder)

    def import_audio(self):
        src, _ = QFileDialog.getOpenFileName(self, "Import audio", str(Path.home()), AUDIO_FILTER)
        if not src:
            return
        src = Path(src)
        iv = create_interview(Path(self.settings["interviews_dir"]), src.stem,
                              self.settings["default_interviewer"])
        original = iv.folder / f"original{src.suffix.lower()}"

        def work():
            shutil.copy2(src, original)
            audio_utils.to_whisper_wav(original, iv.audio_path, Path(self.settings["ffmpeg"]))
            return iv

        def done(_):
            iv.meta["status"] = "recorded"
            iv.save_meta()
            QApplication.restoreOverrideCursor()
            self.statusBar().showMessage(f"Imported {src.name}", 5000)
            self.refresh_list(select=iv.folder)

        def fail(msg):
            QApplication.restoreOverrideCursor()
            QMessageBox.warning(self, "Import failed", msg)
            self.refresh_list(select=iv.folder)

        QApplication.setOverrideCursor(Qt.WaitCursor)
        self.statusBar().showMessage(f"Converting {src.name}…")
        self._run(work, done, fail)

    def _run(self, fn, done, fail):
        w = Worker(fn)
        w.done.connect(done)
        w.error.connect(fail)
        w.finished.connect(lambda: self.workers.remove(w))
        self.workers.append(w)
        w.start()

    # ---------------- recording
    def start_recording(self):
        iv = self.current
        if not iv:
            return
        if iv.has_audio and QMessageBox.question(
                self, "Replace recording?",
                "This interview already has audio. Record again and replace it?") != QMessageBox.Yes:
            return
        self.player.stop()
        self.player.setSource(QUrl())
        device = self.device_combo.currentData()
        self.settings["input_device"] = device
        self.settings.save()
        try:
            self.recorder.start(iv.folder / "recording.wav", device)
        except Exception as e:
            QMessageBox.critical(self, "Microphone error", f"Could not start recording:\n{e}")
            return
        self.btn_pause.setChecked(False)
        self.meter_timer.start()
        self.statusBar().showMessage("Recording…")
        self._update_buttons()

    def _toggle_pause(self, paused: bool):
        self.recorder.pause(paused)
        self.btn_pause.setText("Resume" if paused else "Pause")
        self.statusBar().showMessage("Paused" if paused else "Recording…")

    def stop_recording(self):
        if not self.recorder.is_recording:
            return
        path = self.recorder.stop()
        self.meter_timer.stop()
        self.level.setValue(0)
        iv = self.current
        if self.recorder.error:
            QMessageBox.warning(self, "Recording problem", self.recorder.error)

        def finalize():
            if audio_utils.is_whisper_ready(path):
                path.replace(iv.audio_path)
            else:
                audio_utils.to_whisper_wav(path, iv.audio_path)
                path.unlink(missing_ok=True)
            return iv

        def done(_):
            iv.meta["status"] = "recorded"
            iv.meta["duration_s"] = round(audio_utils.duration_seconds(iv.audio_path), 1)
            iv.save_meta()
            self.statusBar().showMessage("Recording saved", 5000)
            self.refresh_list(select=iv.folder)

        def fail(msg):
            QMessageBox.warning(self, "Saving failed", msg)
            self._update_buttons()

        self._run(finalize, done, fail)
        self._update_buttons()

    def _update_meter(self):
        self.level.setValue(int(self.recorder.level * 100))
        self.rec_time.setText(exporter.fmt_time(int(self.recorder.elapsed_seconds * 1000)))

    # ---------------- transcription
    def start_transcription(self):
        iv = self.current
        if not iv or not iv.has_audio:
            return
        if self.segments and QMessageBox.question(
                self, "Transcribe again?",
                "This will replace the current transcript, including your edits. Continue?") != QMessageBox.Yes:
            return
        model = self.model_combo.currentText()
        self.settings["model"] = model
        self.settings.save()
        iv.meta["model"] = model
        iv.save_meta()
        self.job.start(
            Path(self.settings["whisper_cli"]), Path(self.settings["models_dir"]) / model,
            iv.audio_path, iv.whisper_out_base,
            language=self.settings["language"], threads=int(self.settings["threads"]),
            prompt=self.settings["vocabulary"],
        )
        if self.job.running:
            self.statusBar().showMessage(f"Transcribing with {model}… (you can keep editing details)")
        self._update_buttons()

    def _on_transcribed(self, segments: list):
        iv = self.current
        self.segments = segments
        iv.save_segments(segments)
        iv.meta["status"] = "transcribed"
        iv.save_meta()
        self._load_table()
        self.statusBar().showMessage(f"Done: {len(segments)} segments", 8000)
        item = self.list.currentItem()
        if item:
            item.setText(self._list_label(iv))
        self._update_buttons()

    def _on_transcribe_failed(self, msg: str):
        self.progress.setValue(0)
        self.statusBar().showMessage("Transcription stopped", 5000)
        if "cancelled" not in msg:
            QMessageBox.warning(self, "Transcription failed", msg)
        QTimer.singleShot(0, self._update_buttons)

    # ---------------- transcript table
    def _load_table(self):
        self._loading_table = True
        self.table.setRowCount(len(self.segments))
        for r, s in enumerate(self.segments):
            t = QTableWidgetItem(exporter.fmt_time(s.start_ms))
            t.setFlags(t.flags() & ~Qt.ItemIsEditable)
            t.setForeground(QColor("#1565c0"))
            t.setToolTip("Double-click to play from here")
            self.table.setItem(r, 0, t)
            self.table.setItem(r, 1, QTableWidgetItem(s.speaker))
            self.table.setItem(r, 2, QTableWidgetItem(s.text))
        self.table.resizeRowsToContents()
        self._loading_table = False

    def _on_cell_changed(self, item: QTableWidgetItem):
        if self._loading_table or item.row() >= len(self.segments):
            return
        seg = self.segments[item.row()]
        if item.column() == 1:
            seg.speaker = item.text().strip()
        elif item.column() == 2:
            seg.text = item.text().strip()
        self.autosave_timer.start()

    def _save_segments(self):
        if self.current and self.segments and not self._loading_table:
            self.current.save_segments(self.segments)

    def _on_cell_double_clicked(self, row: int, col: int):
        if col == 0 and row < len(self.segments):
            self.player.setPosition(self.segments[row].start_ms)
            self.player.play()

    # ---------------- playback
    def toggle_play(self):
        if self.player.playbackState() == QMediaPlayer.PlayingState:
            self.player.pause()
        else:
            self.player.play()

    def _on_playback_state(self, state):
        self.btn_play.setText("❚❚ Pause" if state == QMediaPlayer.PlayingState else "▶ Play")

    def _on_player_position(self, ms: int):
        self.play_pos.setText(exporter.fmt_time(ms))
        if self.player.playbackState() != QMediaPlayer.PlayingState:
            return
        for r, s in enumerate(self.segments):  # highlight the line being spoken
            if s.start_ms <= ms < s.end_ms:
                if self.table.currentRow() != r:
                    self.table.selectRow(r)
                    self.table.scrollToItem(self.table.item(r, 0))
                break

    # ---------------- export
    def export(self, fmt: str):
        iv = self.current
        if not iv or not self.segments:
            QMessageBox.information(self, "Nothing to export", "Transcribe the interview first.")
            return
        self._save_segments()
        default = str(iv.folder / f"{iv.folder.name}.{fmt}")
        dst, _ = QFileDialog.getSaveFileName(self, "Export transcript", default, f"*.{fmt}")
        if not dst:
            return
        kw = dict(timestamps=self.chk_ts.isChecked(),
                  duration_s=audio_utils.duration_seconds(iv.audio_path),
                  organisation=self.settings["organisation"])
        try:
            if fmt == "docx":
                exporter.export_docx(iv, self.segments, Path(dst), **kw)
            elif fmt == "txt":
                exporter.export_txt(iv, self.segments, Path(dst), **kw)
            else:
                exporter.export_srt(self.segments, Path(dst))
        except PermissionError:
            QMessageBox.warning(self, "Export failed", "The file is open in another program. Close it and try again.")
            return
        self.statusBar().showMessage(f"Exported {dst}", 8000)

    # ---------------- misc
    def open_settings(self):
        if SettingsDialog(self.settings, self).exec():
            self._fill_models()
            self.refresh_list()
            self._set_interview(None)

    def open_root(self):
        root = Path(self.settings["interviews_dir"])
        root.mkdir(parents=True, exist_ok=True)
        os.startfile(root)  # Windows

    def closeEvent(self, event):
        if self.recorder.is_recording:
            if QMessageBox.question(self, "Recording in progress",
                                    "Stop and save the recording before closing?") != QMessageBox.Yes:
                event.ignore()
                return
            path = self.recorder.stop()
            if self.current and audio_utils.is_whisper_ready(path):
                path.replace(self.current.audio_path)
        if self.job.running:
            if QMessageBox.question(self, "Transcription running",
                                    "Cancel the transcription and close?") != QMessageBox.Yes:
                event.ignore()
                return
            self.job.cancel()
        self._save_segments()
        for w in self.workers:
            w.wait(10000)
        event.accept()
