"""
All Files Duplicate Checker — Advanced Suite.
Optimized for large file collections.
"""
from __future__ import annotations

import os
import sys

# Ensure UTF-8 console output on Windows to prevent UnicodeEncodeError with emojis/Hindi text
if sys.platform == "win32":
    try:
        sys.stdout.reconfigure(encoding="utf-8", errors="replace")
        sys.stderr.reconfigure(encoding="utf-8", errors="replace")
    except Exception:
        pass

# Suppress harmless internal Qt and FFmpeg diagnostic logs from cluttering the console
os.environ["QT_LOGGING_RULES"] = (
    "qt.multimedia*=false;"
    "qt.multimedia.ffmpeg*=false;"
    "qt.gui.icc*=false;"
    "qt.gui.imageio*=false;"
    "qt.qpa.*=false;"
    "*.debug=false"
)

from PySide6.QtCore import Qt, QPoint, qInstallMessageHandler, QtMsgType
from PySide6.QtGui import QColor, QFont, QIcon, QPainter, QPen, QPixmap, QPolygon
from PySide6.QtWidgets import QApplication


def _qt_message_filter(msg_type, context, message):
    # Filter out harmless benign diagnostic logs (ICC profile format warnings, FFmpeg probe notices)
    if any(k in message for k in ["fromIccProfile", "qt.gui.icc", "qt.multimedia", "FFmpeg", "ICC profile"]):
        return
    # Only print critical/fatal errors to stderr
    if msg_type in (QtMsgType.QtCriticalMsg, QtMsgType.QtFatalMsg):
        sys.stderr.write(f"[Qt] {message}\n")


qInstallMessageHandler(_qt_message_filter)

from main_window import MainWindow


def create_app_icon() -> QIcon:
    """Draw a clean, modern file duplicate checker icon at runtime."""
    pm = QPixmap(64, 64)
    pm.fill(Qt.transparent)
    p = QPainter(pm)
    p.setRenderHint(QPainter.Antialiasing)

    # Back document (duplicate copy)
    p.setPen(QPen(QColor("#f97316"), 2))
    p.setBrush(QColor("#ffedd5"))
    p.drawRoundedRect(8, 14, 34, 44, 4, 4)

    # Front document
    p.setPen(QPen(QColor("#2563eb"), 2))
    p.setBrush(QColor("#ffffff"))
    p.drawRoundedRect(22, 6, 34, 44, 4, 4)

    # Document lines
    p.setPen(QPen(QColor("#94a3b8"), 2))
    for y in (16, 24, 32, 40):
        p.drawLine(28, y, 48, y)

    # Flash / Lightning badge
    p.setPen(Qt.NoPen)
    p.setBrush(QColor("#eab308"))
    points = [
        (46, 34), (40, 46), (44, 46),
        (38, 58), (52, 44), (46, 44)
    ]
    poly = QPolygon([QPoint(x, y) for x, y in points])
    p.drawPolygon(poly)

    p.end()
    return QIcon(pm)


def main():
    app = QApplication.instance() or QApplication(sys.argv)
    app.setStyle("Fusion")
    app.setApplicationName("All Files Duplicate Checker")
    app.setOrganizationName("File_Tools")
    app.setWindowIcon(create_app_icon())

    font = QFont("Segoe UI", 9)
    app.setFont(font)

    window = MainWindow()

    # Ingest incoming folder or drive paths from Windows Explorer context menu or CLI
    if len(sys.argv) > 1:
        for arg in sys.argv[1:]:
            clean_arg = os.path.abspath(arg.strip('"\''))
            if os.path.isdir(clean_arg):
                window._add_location(clean_arg)

    window.show()
    sys.exit(app.exec())


if __name__ == "__main__":
    from multiprocessing import freeze_support
    freeze_support()
    main()
