"""Interview Transcriber — offline interview recording and transcription (whisper.cpp)."""
import sys

from PySide6.QtWidgets import QApplication

from app.ui import MainWindow


def main() -> int:
    app = QApplication(sys.argv)
    app.setApplicationName("Interview Transcriber")
    app.setStyle("Fusion")
    win = MainWindow()
    win.show()
    return app.exec()


if __name__ == "__main__":
    sys.exit(main())
