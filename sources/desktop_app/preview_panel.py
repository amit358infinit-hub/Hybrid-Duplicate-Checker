"""
Universal File Preview and Multi-File Comparison Widget (Studio-Grade).
Supports:
- Document rendering (PDF, EPUB, XPS) in isolated cancellable worker processes
- Image rendering (JPG, PNG, WEBP, GIF, BMP, SVG, TIFF) with mouse-wheel zoom & drag-pan
- Text & Code inspection (TXT, CSV, JSON, XML, MD, PY, LOG, INI, etc.) with truncation indicators
- Archive inspection (ZIP, TAR table of contents)
- Audio/Video media preview & playback via QtMultimedia with full OS file-handle release
- Hex dump fallback for unknown or binary files
- Multi-file layouts: Dual (2-Up), Visual Curtain Slider, Difference Heatmap, Triple (3-Up), Quad (2x2 Grid), and Filmstrip Carousel
- Synchronized proportional scrolling, page flipping, zooming, and dynamic multi-column metadata comparison
- Live [KEEP Original] vs [DUPLICATE] role badges
- Fully integrated with Fluent Dark and Light themes without conflicting inline styles
"""
from __future__ import annotations

import concurrent.futures
import os
import subprocess
import threading
import time
import zipfile
import tarfile
import xml.etree.ElementTree as ET
from typing import Dict, List, Optional, Tuple

import weakref
from pdf_worker import PdfWorkerPool, PdfWorkerError
from PIL import Image, ImageChops, ImageEnhance

from PySide6.QtCore import Qt, QTimer, Signal, QUrl, QSize, QRect, QEvent, QPoint, Slot
from PySide6.QtGui import QColor, QFont, QImage, QPixmap, QImageReader, QPainter, QPen, QBrush
from PySide6.QtWidgets import (
    QCheckBox,
    QComboBox,
    QFrame,
    QGridLayout,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QPlainTextEdit,
    QPushButton,
    QScrollArea,
    QSlider,
    QSpinBox,
    QStackedWidget,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
    QWidget,
)

try:
    from PySide6.QtMultimedia import QMediaPlayer, QAudioOutput
    from PySide6.QtMultimediaWidgets import QVideoWidget
    QT_MULTIMEDIA_AVAILABLE = True
except Exception:
    QT_MULTIMEDIA_AVAILABLE = False

from safe_trash import win_long_path
from shell_utils import open_in_file_explorer
from scanner import FileInfo, is_heuristic_match

# Extension categorizations
DOC_EXTS = {'.pdf', '.xps', '.epub', '.mobi', '.cbz', '.fb2'}
IMG_EXTS = {'.jpg', '.jpeg', '.png', '.webp', '.bmp', '.gif', '.ico', '.tiff', '.tif', '.svg'}
OFFICE_EXTS = {
    '.docx', '.doc', '.docm', '.dotx', '.dotm',
    '.xlsx', '.xls', '.xlsm', '.xltx', '.xltm',
    '.pptx', '.ppt', '.pptm', '.potx', '.potm',
    '.odt', '.ods', '.odp', '.rtf'
}
TEXT_EXTS = {
    '.txt', '.csv', '.tsv', '.json', '.xml', '.html', '.htm', '.css', '.js', '.ts',
    '.py', '.java', '.c', '.cpp', '.h', '.hpp', '.cs', '.php', '.rb', '.go', '.rs',
    '.sql', '.sh', '.bat', '.ps1', '.cmd', '.md', '.yaml', '.yml', '.ini', '.cfg',
    '.conf', '.log', '.properties', '.env'
}
ARCHIVE_EXTS = {'.zip', '.tar', '.gz', '.tgz', '.bz2', '.xz', '.7z', '.rar'}
AUDIO_EXTS = {'.mp3', '.wav', '.flac', '.aac', '.ogg', '.m4a', '.wma'}
VIDEO_EXTS = {'.mp4', '.mkv', '.avi', '.mov', '.wmv', '.webm', '.flv', '.m4v'}

# Background thread pool executor for non-blocking document rendering
# Document work is owned by process pools, never a rendering thread pool.


def _get_pos(event) -> QPoint:
    """Helper for cross-version Qt mouse position without deprecation warnings."""
    if hasattr(event, "position"):
        return event.position().toPoint()
    return event.pos()


def extract_office_metadata(file_path: str) -> dict:
    """
    Extracts structured metadata from Office documents (DOCX, XLSX, PPTX, ODT, ODS, ODP)
    without requiring Microsoft Office or heavy COM dependencies.
    Parses OpenXML (core.xml, app.xml, workbook.xml) and OpenDocument (meta.xml) in < 2ms.
    """
    meta = {
        "title": "",
        "author": "",
        "modified_by": "",
        "created": "",
        "modified": "",
        "description": "",
        "sheets": [],
        "slide_count": 0,
        "page_count": 0,
        "word_count": 0,
        "app_name": "",
        "doc_type": "Office Document"
    }
    if not file_path or not os.path.isfile(file_path):
        return meta

    ext = os.path.splitext(file_path)[1].lower()
    if ext in ('.docx', '.docm', '.dotx', '.dotm'):
        meta["doc_type"] = "Microsoft Word Document"
    elif ext in ('.xlsx', '.xlsm', '.xltx', '.xltm'):
        meta["doc_type"] = "Microsoft Excel Spreadsheet"
    elif ext in ('.pptx', '.pptm', '.potx', '.potm'):
        meta["doc_type"] = "Microsoft PowerPoint Presentation"
    elif ext == '.odt':
        meta["doc_type"] = "OpenDocument Text"
    elif ext == '.ods':
        meta["doc_type"] = "OpenDocument Spreadsheet"
    elif ext == '.odp':
        meta["doc_type"] = "OpenDocument Presentation"
    elif ext in ('.doc', '.xls', '.ppt'):
        meta["doc_type"] = f"Legacy Office Binary ({ext.upper()})"
    elif ext == '.rtf':
        meta["doc_type"] = "Rich Text Document"

    # Legacy binary formats cannot be opened via zipfile
    if ext in ('.doc', '.xls', '.ppt', '.rtf'):
        return meta

    try:
        with zipfile.ZipFile(file_path, 'r') as z:
            names = set(z.namelist())

            # 1. docProps/core.xml (Standard OOXML: docx, xlsx, pptx)
            if 'docProps/core.xml' in names:
                try:
                    tree = ET.fromstring(z.read('docProps/core.xml'))
                    for el in tree.iter():
                        tag = el.tag.split('}')[-1].lower()
                        text = (el.text or '').strip()
                        if not text:
                            continue
                        if tag == 'title':
                            meta['title'] = text
                        elif tag == 'creator':
                            meta['author'] = text
                        elif tag == 'lastmodifiedby':
                            meta['modified_by'] = text
                        elif tag == 'created':
                            meta['created'] = text.replace('T', ' ').replace('Z', '')[:19]
                        elif tag == 'modified':
                            meta['modified'] = text.replace('T', ' ').replace('Z', '')[:19]
                        elif tag in ('subject', 'description'):
                            meta['description'] = text
                except Exception:
                    pass

            # 2. docProps/app.xml (Extended properties: Slides, Pages, Words, Application)
            if 'docProps/app.xml' in names:
                try:
                    tree = ET.fromstring(z.read('docProps/app.xml'))
                    for el in tree.iter():
                        tag = el.tag.split('}')[-1].lower()
                        text = (el.text or '').strip()
                        if not text:
                            continue
                        if tag == 'slides' and text.isdigit():
                            meta['slide_count'] = int(text)
                        elif tag == 'pages' and text.isdigit():
                            meta['page_count'] = int(text)
                        elif tag == 'words' and text.isdigit():
                            meta['word_count'] = int(text)
                        elif tag == 'application':
                            meta['app_name'] = text
                except Exception:
                    pass

            # 3. Excel Sheets (xl/workbook.xml)
            if 'xl/workbook.xml' in names:
                try:
                    tree = ET.fromstring(z.read('xl/workbook.xml'))
                    for el in tree.iter():
                        if el.tag.endswith('sheet'):
                            s_name = el.attrib.get('name')
                            if s_name:
                                meta['sheets'].append(s_name)
                except Exception:
                    pass

            # 4. PowerPoint Slide Count fallback (count slide files if app.xml missing)
            if meta['slide_count'] == 0 and ext.startswith('.ppt'):
                slides = [n for n in names if n.startswith('ppt/slides/slide') and n.endswith('.xml')]
                if slides:
                    meta['slide_count'] = len(slides)

            # 5. OpenDocument Format (meta.xml)
            if 'meta.xml' in names:
                try:
                    tree = ET.fromstring(z.read('meta.xml'))
                    for el in tree.iter():
                        tag = el.tag.split('}')[-1].lower()
                        text = (el.text or '').strip()
                        if not text:
                            continue
                        if tag == 'title':
                            meta['title'] = text
                        elif tag == 'creator':
                            meta['author'] = text
                        elif tag == 'creation-date':
                            meta['created'] = text.replace('T', ' ').replace('Z', '')[:19]
                        elif tag == 'date':
                            meta['modified'] = text.replace('T', ' ').replace('Z', '')[:19]
                        elif tag == 'description':
                            meta['description'] = text
                except Exception:
                    pass

    except Exception as e:
        meta['error'] = str(e)

    return meta


class UniversalFileViewer(QWidget):
    """
    Universal File Viewer supporting Documents (PDF/EPUB), Office Documents (DOCX/XLSX/PPTX/ODT),
    Images, Text/Code, Archives, Media (Audio/Video), and Binary/Hex fallback.
    Fully backwards-compatible with SinglePdfViewer interface.
    """
    page_changed = Signal(int)
    zoom_changed = Signal(float)
    pixmap_rendered = Signal()
    _bg_render_done = Signal(object)

    PAGE_DOC_IMG = 0
    PAGE_TEXT = 1
    PAGE_ARCHIVE = 2
    PAGE_MEDIA = 3
    PAGE_HEX = 4
    PAGE_OFFICE = 5

    def __init__(self, title_prefix: str = "", parent=None, pool_provider=None):
        super().__init__(parent)
        self.title_prefix = title_prefix
        self.current_path: Optional[str] = None
        self.doc = None  # Process-returned metadata only; no native document handle.
        self._pool_provider = pool_provider
        self._own_pdf_pool = None
        self._metadata_future = None
        self.current_page: int = 0
        self.total_pages: int = 0
        self.zoom_level: float = 1.0
        self.fit_mode: str = "fit_width"  # "fit_width", "fit_page"
        self._pixmap_cache: Dict[tuple, QPixmap] = {}
        self._current_ext: str = ""
        self._media_player: Optional[QMediaPlayer] = None
        self._audio_output: Optional[QAudioOutput] = None
        self._raw_pixmap: Optional[QPixmap] = None
        self._render_token: int = 0
        self.comparison_viewport = None
        self._display_key = None
        self._office_meta: Optional[dict] = None

        # Thread cancellation & active future tracking
        self._active_future: Optional[concurrent.futures.Future] = None
        self._cancel_event = threading.Event()

        # Drag-Pan state
        self._is_panning = False
        self._pan_start = QPoint()
        self._pan_start_h = 0
        self._pan_start_v = 0

        self._bg_render_done.connect(self._apply_bg_render_result, Qt.QueuedConnection)

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(4, 4, 4, 4)
        layout.setSpacing(4)

        # 1. Header Bar
        header = QHBoxLayout()
        header.setSpacing(4)

        self.title_label = QLabel(self.title_prefix)
        self.title_label.setObjectName("preview_title_label")
        header.addWidget(self.title_label)

        self.role_badge = QLabel("")
        self.role_badge.setObjectName("preview_role_badge")
        self.role_badge.setVisible(False)
        header.addWidget(self.role_badge)
        header.addStretch(1)

        self.btn_zoom_out = QPushButton("−")
        self.btn_zoom_out.setObjectName("preview_tool_btn")
        self.btn_zoom_out.setToolTip("Zoom Out (−)")
        self.btn_zoom_out.clicked.connect(self.zoom_out)

        self.btn_zoom_in = QPushButton("+")
        self.btn_zoom_in.setObjectName("preview_tool_btn")
        self.btn_zoom_in.setToolTip("Zoom In (+)")
        self.btn_zoom_in.clicked.connect(self.zoom_in)

        self.btn_fit_w = QPushButton("W")
        self.btn_fit_w.setObjectName("preview_tool_btn")
        self.btn_fit_w.setToolTip("Fit Width (W)")
        self.btn_fit_w.clicked.connect(self.fit_width)

        self.btn_fit_p = QPushButton("P")
        self.btn_fit_p.setObjectName("preview_tool_btn")
        self.btn_fit_p.setToolTip("Fit Page / Window (P)")
        self.btn_fit_p.clicked.connect(self.fit_page)

        self.btn_open_external = QPushButton("↗")
        self.btn_open_external.setObjectName("preview_tool_btn")
        self.btn_open_external.setToolTip("Open in External Application")
        self.btn_open_external.clicked.connect(self.open_external)

        self.btn_reveal = QPushButton("📁")
        self.btn_reveal.setObjectName("preview_tool_btn")
        self.btn_reveal.setToolTip("Show in Windows File Explorer")
        self.btn_reveal.clicked.connect(self.reveal_in_explorer)

        header.addWidget(self.btn_zoom_out)
        header.addWidget(self.btn_zoom_in)
        header.addWidget(self.btn_fit_w)
        header.addWidget(self.btn_fit_p)
        header.addWidget(self.btn_open_external)
        header.addWidget(self.btn_reveal)
        layout.addLayout(header)

        # 2. File Information Subtitle
        self.info_label = QLabel("No file loaded")
        self.info_label.setObjectName("preview_info_label")
        self.info_label.setWordWrap(True)
        layout.addWidget(self.info_label)

        # 3. Stacked Viewer Widget
        self.stack = QStackedWidget()

        # Page 0: Image & Document Scroll Area
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("preview_scroll_area")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.viewport().installEventFilter(self)

        self.image_label = QLabel()
        self.image_label.setObjectName("preview_image_label")
        self.image_label.setAlignment(Qt.AlignCenter)
        self.image_label.setText("Select a file to preview")
        self.scroll_area.setWidget(self.image_label)
        self.stack.addWidget(self.scroll_area)

        # Page 1: Text & Code Viewer
        self.text_editor = QPlainTextEdit()
        self.text_editor.setObjectName("preview_text_editor")
        self.text_editor.setReadOnly(True)
        self.text_editor.setFont(QFont("Consolas", 10))
        self.stack.addWidget(self.text_editor)

        # Page 2: Archive Table Viewer
        self.archive_table = QTableWidget()
        self.archive_table.setObjectName("preview_archive_table")
        self.archive_table.setColumnCount(3)
        self.archive_table.setHorizontalHeaderLabels(["Name", "Size", "Compressed"])
        self.archive_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.Stretch)
        self.archive_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.archive_table.horizontalHeader().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.archive_table.setAlternatingRowColors(True)
        self.stack.addWidget(self.archive_table)

        # Page 3: Media Player Widget
        self.media_widget = QWidget()
        m_lay = QVBoxLayout(self.media_widget)
        m_lay.setContentsMargins(6, 6, 6, 6)
        m_lay.setSpacing(6)

        if QT_MULTIMEDIA_AVAILABLE:
            self.video_widget = QVideoWidget()
            self.video_widget.setStyleSheet("background-color: #0f172a; border-radius: 4px;")
            m_lay.addWidget(self.video_widget, stretch=1)
        else:
            self.video_widget = None
            lbl_no_mm = QLabel("Multimedia Player Not Available")
            lbl_no_mm.setAlignment(Qt.AlignCenter)
            m_lay.addWidget(lbl_no_mm, stretch=1)

        # Media Control bar
        med_ctrl = QHBoxLayout()
        self.btn_play_pause = QPushButton("▶ Play")
        self.btn_play_pause.setFixedHeight(26)
        self.btn_play_pause.clicked.connect(self._toggle_playback)

        self.media_slider = QSlider(Qt.Horizontal)
        self.media_slider.setRange(0, 0)
        self.media_slider.sliderMoved.connect(self._set_media_position)

        self.lbl_media_time = QLabel("00:00 / 00:00")
        self.lbl_media_time.setObjectName("preview_info_label")

        med_ctrl.addWidget(self.btn_play_pause)
        med_ctrl.addWidget(self.media_slider, stretch=1)
        med_ctrl.addWidget(self.lbl_media_time)
        m_lay.addLayout(med_ctrl)

        self.stack.addWidget(self.media_widget)

        # Page 4: Hex Viewer
        self.hex_editor = QPlainTextEdit()
        self.hex_editor.setObjectName("preview_hex_editor")
        self.hex_editor.setReadOnly(True)
        self.hex_editor.setFont(QFont("Consolas", 10))
        self.stack.addWidget(self.hex_editor)

        # Page 5: Office Document Inspector Card
        self.office_widget = QWidget()
        office_outer = QVBoxLayout(self.office_widget)
        office_outer.setContentsMargins(8, 8, 8, 8)
        office_outer.setSpacing(6)

        self.office_card = QFrame()
        self.office_card.setObjectName("preview_office_card")
        card_lay = QVBoxLayout(self.office_card)
        card_lay.setContentsMargins(12, 10, 12, 10)
        card_lay.setSpacing(8)

        # Header Badge
        top_h = QHBoxLayout()
        self.office_badge = QLabel("📘 Office Document")
        self.office_badge.setObjectName("preview_office_badge")
        top_h.addWidget(self.office_badge)
        top_h.addStretch(1)
        card_lay.addLayout(top_h)

        # Document Title
        self.office_title_lbl = QLabel("Document Title")
        self.office_title_lbl.setObjectName("preview_office_title")
        self.office_title_lbl.setWordWrap(True)
        self.office_title_lbl.setTextInteractionFlags(Qt.TextSelectableByMouse)
        card_lay.addWidget(self.office_title_lbl)

        # Thumbnail preview for Office Documents
        self.office_thumb_lbl = QLabel()
        self.office_thumb_lbl.setAlignment(Qt.AlignCenter)
        self.office_thumb_lbl.setVisible(False)
        self.office_thumb_lbl.setMaximumHeight(160)
        card_lay.addWidget(self.office_thumb_lbl)

        # Metadata Table
        self.office_table = QTableWidget()
        self.office_table.setObjectName("preview_office_table")
        self.office_table.setColumnCount(2)
        self.office_table.setHorizontalHeaderLabels(["Property", "Value"])
        self.office_table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.office_table.horizontalHeader().setSectionResizeMode(1, QHeaderView.Stretch)
        self.office_table.verticalHeader().setVisible(False)
        self.office_table.setShowGrid(False)
        self.office_table.setAlternatingRowColors(True)
        card_lay.addWidget(self.office_table, stretch=1)

        # Quick Actions
        btn_h = QHBoxLayout()
        btn_h.setSpacing(8)
        self.btn_office_open = QPushButton("↗ Open in Default App")
        self.btn_office_open.setFixedHeight(28)
        self.btn_office_open.clicked.connect(self.open_external)
        self.btn_office_reveal = QPushButton("📁 Show in Explorer")
        self.btn_office_reveal.setFixedHeight(28)
        self.btn_office_reveal.clicked.connect(self.reveal_in_explorer)
        btn_h.addWidget(self.btn_office_open)
        btn_h.addWidget(self.btn_office_reveal)
        btn_h.addStretch(1)
        card_lay.addLayout(btn_h)

        office_outer.addWidget(self.office_card)
        self.stack.addWidget(self.office_widget)

        layout.addWidget(self.stack, stretch=1)

        # 4. Bottom Navigation & Status Bar
        self.nav_bar = QHBoxLayout()
        self.nav_bar.setSpacing(6)

        self.btn_prev = QPushButton("◀ Prev")
        self.btn_prev.setFixedHeight(26)
        self.btn_prev.clicked.connect(self.prev_page)

        self.page_spin = QSpinBox()
        self.page_spin.setFixedHeight(26)
        self.page_spin.setMinimum(1)
        self.page_spin.setMaximum(1)
        self.page_spin.valueChanged.connect(self._on_spin_changed)

        self.page_count_label = QLabel("/ 0")
        self.page_count_label.setObjectName("preview_info_label")

        self.btn_next = QPushButton("Next ▶")
        self.btn_next.setFixedHeight(26)
        self.btn_next.clicked.connect(self.next_page)

        self.nav_bar.addWidget(self.btn_prev)
        self.nav_bar.addWidget(self.page_spin)
        self.nav_bar.addWidget(self.page_count_label)
        self.nav_bar.addWidget(self.btn_next)

        self.lbl_extra_info = QLabel("")
        self.lbl_extra_info.setObjectName("preview_extra_info")
        self.nav_bar.addWidget(self.lbl_extra_info, stretch=1)

        layout.addLayout(self.nav_bar)

    def set_role_badge(self, role: str):
        """Sets the visual role badge: 'keep', 'duplicate', or 'none'/'clear'."""
        if role == "potential":
            self.role_badge.setText(" POTENTIAL MATCH (Manual Review Required) ")
            self.role_badge.setStyleSheet("color: #f59e0b; font-weight: bold; padding: 2px;")
            self.role_badge.setVisible(True)
        elif role == "keep":
            self.role_badge.setText(" ⭐ [KEEP Original] ")
            self.role_badge.setStyleSheet(
                "color: #10b981; background: rgba(16, 185, 129, 0.15); "
                "border: 1px solid #10b981; border-radius: 4px; font-weight: bold; "
                "font-size: 11px; padding: 1px 6px;"
            )
            self.role_badge.setVisible(True)
        elif role == "duplicate":
            self.role_badge.setText(" 🗑️ [DUPLICATE] ")
            self.role_badge.setStyleSheet(
                "color: #ef4444; background: rgba(239, 68, 68, 0.15); "
                "border: 1px solid #ef4444; border-radius: 4px; font-weight: bold; "
                "font-size: 11px; padding: 1px 6px;"
            )
            self.role_badge.setVisible(True)
        else:
            self.role_badge.setText("")
            self.role_badge.setVisible(False)

    def eventFilter(self, watched, event):
        if watched == self.scroll_area.viewport():
            # 1. Ctrl + Mouse Wheel Zoom
            if event.type() == QEvent.Wheel:
                if event.modifiers() & Qt.ControlModifier:
                    delta = event.angleDelta().y()
                    if delta > 0:
                        self.zoom_in()
                    elif delta < 0:
                        self.zoom_out()
                    return True

            # 2. Left Mouse Press -> Start Drag-Pan
            elif event.type() == QEvent.MouseButtonPress:
                if event.button() == Qt.LeftButton:
                    h_bar = self.scroll_area.horizontalScrollBar()
                    v_bar = self.scroll_area.verticalScrollBar()
                    if h_bar.maximum() > 0 or v_bar.maximum() > 0:
                        self._is_panning = True
                        self._pan_start = _get_pos(event)
                        self._pan_start_h = h_bar.value()
                        self._pan_start_v = v_bar.value()
                        self.scroll_area.viewport().setCursor(Qt.ClosedHandCursor)
                        return True

            # 3. Mouse Move -> Drag-Pan or update cursor
            elif event.type() == QEvent.MouseMove:
                if self._is_panning:
                    delta = _get_pos(event) - self._pan_start
                    self.scroll_area.horizontalScrollBar().setValue(self._pan_start_h - delta.x())
                    self.scroll_area.verticalScrollBar().setValue(self._pan_start_v - delta.y())
                    return True
                else:
                    h_bar = self.scroll_area.horizontalScrollBar()
                    v_bar = self.scroll_area.verticalScrollBar()
                    if h_bar.maximum() > 0 or v_bar.maximum() > 0:
                        self.scroll_area.viewport().setCursor(Qt.OpenHandCursor)
                    else:
                        self.scroll_area.viewport().setCursor(Qt.ArrowCursor)

            # 4. Mouse Button Release -> End Drag-Pan
            elif event.type() == QEvent.MouseButtonRelease:
                if event.button() == Qt.LeftButton and self._is_panning:
                    self._is_panning = False
                    h_bar = self.scroll_area.horizontalScrollBar()
                    v_bar = self.scroll_area.verticalScrollBar()
                    if h_bar.maximum() > 0 or v_bar.maximum() > 0:
                        self.scroll_area.viewport().setCursor(Qt.OpenHandCursor)
                    else:
                        self.scroll_area.viewport().setCursor(Qt.ArrowCursor)
                    return True

        return super().eventFilter(watched, event)

    def load_pdf(self, file_path: str):
        """Compatibility alias for load_file()."""
        self.load_file(file_path)

    def close_doc(self):
        """Explicitly closes open document handles and releases OS locks immediately."""
        self._cleanup_resources()
        self.current_path = None
        self._raw_pixmap = None
        self.image_label.clear()
        self.info_label.setText("No file loaded")
        self.image_label.setText("Select a file to preview")
        self.total_pages = 0
        self.page_spin.setMaximum(1)
        self.page_count_label.setText("/ 0")
        self.lbl_extra_info.setText("")
        self._set_nav_visible(False)

    def load_file(self, file_path: str):
        """Universal loader dispatching to appropriate sub-renderer based on file type."""
        self._cleanup_resources()
        self.current_path = file_path
        self.current_page = 0
        self._raw_pixmap = None

        if not file_path or not os.path.isfile(file_path):
            self.info_label.setText("No file loaded")
            self.image_label.setText("Select a file to preview")
            self.total_pages = 0
            self.page_spin.setMaximum(1)
            self.page_count_label.setText("/ 0")
            self.lbl_extra_info.setText("")
            self.stack.setCurrentIndex(self.PAGE_DOC_IMG)
            self._set_nav_visible(False)
            return

        ext = os.path.splitext(file_path)[1].lower()
        self._current_ext = ext
        fname = os.path.basename(file_path)
        fsize_bytes = os.path.getsize(file_path)
        fsize_str = self._format_size(fsize_bytes)
        self.info_label.setText(f"{fname}  •  {fsize_str}")

        try:
            if ext in DOC_EXTS:
                self._load_document(file_path)
            elif ext in OFFICE_EXTS:
                self._load_office_doc(file_path)
            elif ext in IMG_EXTS:
                self._load_image(file_path)
            elif ext in TEXT_EXTS:
                self._load_text(file_path)
            elif ext in ARCHIVE_EXTS:
                self._load_archive(file_path)
            elif ext in AUDIO_EXTS or ext in VIDEO_EXTS:
                self._load_media(file_path, is_video=(ext in VIDEO_EXTS))
            else:
                self._load_hex(file_path)
        except Exception as e:
            self._load_hex(file_path, error_msg=str(e))

    def _cleanup_resources(self):
        """Stops playback, closes document handles, drains background rendering futures, and clears caches."""
        self._render_token += 1
        self._cancel_event.set()
        for future in (self._active_future, self._metadata_future):
            if future and not future.done():
                pool = self._pool_provider() if self._pool_provider else self._own_pdf_pool
                if pool:
                    pool.cancel(future)
        self._active_future = None
        self._metadata_future = None
        self._office_meta = None

        self.doc = None

        self._pixmap_cache.clear()
        self._display_key = None

        if self._media_player:
            try:
                self._media_player.stop()
                self._media_player.setSource(QUrl())  # Release DirectShow/MF file lock immediately
            except Exception:
                pass

        # Reset stacked editor contents to prevent stale state display
        if hasattr(self, 'text_editor'):
            self.text_editor.clear()
        if hasattr(self, 'hex_editor'):
            self.hex_editor.clear()
        if hasattr(self, 'archive_table'):
            self.archive_table.setRowCount(0)
        if hasattr(self, 'office_table'):
            self.office_table.setRowCount(0)
        if hasattr(self, 'office_thumb_lbl'):
            self.office_thumb_lbl.clear()
            self.office_thumb_lbl.setVisible(False)

    def _set_nav_visible(self, visible: bool):
        self.btn_prev.setVisible(visible)
        self.page_spin.setVisible(visible)
        self.page_count_label.setVisible(visible)
        self.btn_next.setVisible(visible)

    def _format_size(self, size_bytes: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if size_bytes < 1024.0:
                return f"{size_bytes:.2f} {unit}"
            size_bytes /= 1024.0
        return f"{size_bytes:.2f} PB"

    # ---------------- OFFICE DOCUMENTS (DOCX/XLSX/PPTX/ODT) ----------------
    def _load_office_doc(self, file_path: str):
        self.stack.setCurrentIndex(self.PAGE_OFFICE)
        self._set_nav_visible(False)
        self.total_pages = 1

        ext = os.path.splitext(file_path)[1].lower()
        meta = extract_office_metadata(file_path)
        self._office_meta = meta

        # Dynamic badge styling per document family
        if ext in ('.docx', '.doc', '.docm', '.dotx', '.dotm'):
            badge_text = "📘 Microsoft Word Document"
            badge_style = "font-size: 12px; font-weight: bold; color: #3b82f6; padding: 4px 8px; background: rgba(59, 130, 246, 0.15); border-radius: 4px;"
        elif ext in ('.xlsx', '.xls', '.xlsm', '.xltx', '.xltm'):
            badge_text = "📗 Microsoft Excel Spreadsheet"
            badge_style = "font-size: 12px; font-weight: bold; color: #10b981; padding: 4px 8px; background: rgba(16, 185, 129, 0.15); border-radius: 4px;"
        elif ext in ('.pptx', '.ppt', '.pptm', '.potx', '.potm'):
            badge_text = "📙 Microsoft PowerPoint Presentation"
            badge_style = "font-size: 12px; font-weight: bold; color: #f59e0b; padding: 4px 8px; background: rgba(245, 158, 11, 0.15); border-radius: 4px;"
        elif ext in ('.odt', '.ods', '.odp'):
            badge_text = f"📄 {meta.get('doc_type', 'OpenDocument')}"
            badge_style = "font-size: 12px; font-weight: bold; color: #8b5cf6; padding: 4px 8px; background: rgba(139, 92, 246, 0.15); border-radius: 4px;"
        else:
            badge_text = f"📄 {meta.get('doc_type', 'Office Document')}"
            badge_style = "font-size: 12px; font-weight: bold; color: #94a3b8; padding: 4px 8px; background: rgba(148, 163, 184, 0.15); border-radius: 4px;"

        self.office_badge.setText(badge_text)
        self.office_badge.setStyleSheet(badge_style)

        title = meta.get("title") or os.path.basename(file_path)
        self.office_title_lbl.setText(title)

        # Check for embedded thumbnail in OOXML (DOCX, PPTX)
        self.office_thumb_lbl.clear()
        self.office_thumb_lbl.setVisible(False)
        if ext in ('.docx', '.pptx'):
            try:
                with zipfile.ZipFile(file_path, 'r') as z:
                    for t_name in ['docProps/thumbnail.jpeg', 'docProps/thumbnail.wmf', 'docProps/thumbnail.png']:
                        if t_name in z.namelist():
                            t_bytes = z.read(t_name)
                            qimg = QImage()
                            if qimg.loadFromData(t_bytes):
                                thumb_pix = QPixmap.fromImage(qimg).scaled(
                                    200, 160, Qt.KeepAspectRatio, Qt.SmoothTransformation
                                )
                                self.office_thumb_lbl.setPixmap(thumb_pix)
                                self.office_thumb_lbl.setVisible(True)
                            break
            except Exception:
                pass

        fsize = self._format_size(os.path.getsize(file_path))
        props = [
            ("File Name", os.path.basename(file_path)),
            ("Folder", os.path.basename(os.path.dirname(file_path))),
            ("File Size", fsize),
            ("Format", ext.upper().lstrip('.')),
            ("Author / Creator", meta.get("author") or "—"),
            ("Last Modified By", meta.get("modified_by") or "—"),
            ("Created Date", meta.get("created") or "—"),
            ("Modified Date", meta.get("modified") or "—"),
        ]

        if meta.get("sheets"):
            sheets = meta["sheets"]
            sheets_str = f"{len(sheets)} sheets: {', '.join(sheets[:5])}"
            if len(sheets) > 5:
                sheets_str += f" (+{len(sheets) - 5} more)"
            props.append(("Sheets", sheets_str))

        if meta.get("slide_count"):
            props.append(("Slide Count", f"{meta['slide_count']} slides"))

        if meta.get("page_count"):
            props.append(("Page Count", f"{meta['page_count']} pages"))

        if meta.get("word_count"):
            props.append(("Word Count", f"{meta['word_count']} words"))

        if meta.get("app_name"):
            props.append(("Created With", meta.get("app_name")))

        props.append(("Full Path", file_path))

        self.office_table.setRowCount(len(props))
        for r, (k, v) in enumerate(props):
            k_item = QTableWidgetItem(k)
            k_item.setFont(QFont("Segoe UI", 9, QFont.Bold))
            v_item = QTableWidgetItem(str(v))
            v_item.setFont(QFont("Segoe UI", 9))
            v_item.setToolTip(str(v))
            self.office_table.setItem(r, 0, k_item)
            self.office_table.setItem(r, 1, v_item)

        self.lbl_extra_info.setText(f"{badge_text}  •  {fsize}")

    # ---------------- DOCS (PDF/EPUB/XPS) ----------------
    def _get_pdf_pool(self):
        if self._pool_provider:
            return self._pool_provider()
        if self._own_pdf_pool is None:
            self._own_pdf_pool = PdfWorkerPool()
        return self._own_pdf_pool

    def _watch_document_future(self, future, kind, token, path, cache_key=None):
        weak = weakref.ref(self)
        def completed(done):
            try:
                value = done.result()
            except Exception as error:
                value = error
            viewer = weak()
            if viewer is not None:
                try:
                    viewer._bg_render_done.emit((token, path, kind, cache_key, value))
                except RuntimeError:
                    pass  # Qt may already have destroyed the closed viewer.
        future.add_done_callback(completed)

    def _load_document(self, file_path: str):
        self.stack.setCurrentIndex(self.PAGE_DOC_IMG)
        self._set_nav_visible(False)
        self.total_pages = 0
        self.image_label.setText("Loading document metadata...")
        self.lbl_extra_info.setText("Loading...")
        try:
            self._metadata_future = self._get_pdf_pool().extract_pdf_metadata(file_path)
            self._watch_document_future(self._metadata_future, "metadata", self._render_token, file_path)
        except Exception as error:
            self._document_error(error)

    def _document_error(self, error):
        reason = getattr(error, "reason", "document_error")
        if reason == "password_required":
            badge = "Password-protected document"
        elif reason in ("timeout", "resource_limit"):
            badge = "Preview unavailable"
        else:
            badge = "Damaged / Unreadable PDF"
        self.lbl_extra_info.setText(badge)
        self.image_label.clear()
        self.image_label.setText(f"{badge}\n{error}")
        self._set_nav_visible(False)
        self.pixmap_rendered.emit()

    def render_current_page(self, sync: bool = False):
        # The legacy sync argument never permits native work or waits on the GUI thread.
        if self._current_ext in IMG_EXTS:
            self._render_image_scaled()
            return
        if not self.doc or not self.total_pages:
            return
        self._render_token += 1
        if self._active_future and not self._active_future.done():
            self._get_pdf_pool().cancel(self._active_future)
        page = max(0, min(self.current_page, self.total_pages - 1))
        width = max(100, self.scroll_area.viewport().width() - 24)
        height = max(100, self.scroll_area.viewport().height() - 24)
        zoom, fit_mode = self.zoom_level, self.fit_mode
        if self.comparison_viewport:
            width, height = self.comparison_viewport
            zoom, fit_mode = 1.0, "fit_page"
        key = (page, round(zoom, 3), width, height, fit_mode)
        if key in self._pixmap_cache:
            pixmap = self._pixmap_cache[key]
            self._display_key = key
            self.image_label.setPixmap(pixmap)
            self.image_label.resize(pixmap.size())
            self.pixmap_rendered.emit()
            return
        try:
            self._active_future = self._get_pdf_pool().render_pdf_page(
                self.current_path, page, zoom, viewport=(width, height), fit_mode=fit_mode
            )
            self._watch_document_future(self._active_future, "render", self._render_token, self.current_path, key)
        except Exception as error:
            self._document_error(error)

    @Slot(object)
    def _apply_bg_render_result(self, result):
        token, path, kind, cache_key, value = result
        if token != self._render_token or path != self.current_path:
            return
        if isinstance(value, Exception):
            if getattr(value, "reason", "") != "cancelled":
                self._document_error(value)
            return
        if kind == "metadata":
            self._metadata_future = None
            if value.is_damaged or value.is_encrypted or value.page_count < 1:
                reason = "password_required" if value.is_encrypted else "document_error"
                self._document_error(PdfWorkerError(value.error or "Document cannot be previewed", reason=reason))
                return
            self.doc = value
            self.total_pages = value.page_count
            self.page_spin.blockSignals(True)
            self.page_spin.setRange(1, self.total_pages)
            self.page_spin.setValue(self.current_page + 1)
            self.page_spin.blockSignals(False)
            self.page_count_label.setText(f"/ {self.total_pages}")
            self.lbl_extra_info.setText(f"Format: {value.format}")
            self._set_nav_visible(True)
            self.render_current_page()
            return
        pixmap = QPixmap()
        if not pixmap.loadFromData(value, "PNG"):
            self._document_error(ValueError("Worker returned invalid PNG data"))
            return
        if len(self._pixmap_cache) >= 8:
            self._pixmap_cache.clear()
        self._pixmap_cache[cache_key] = pixmap
        self._display_key = cache_key
        self.image_label.setPixmap(pixmap)
        self.image_label.resize(pixmap.size())
        self.pixmap_rendered.emit()

    def _load_image(self, file_path: str):
        self.stack.setCurrentIndex(self.PAGE_DOC_IMG)
        self._set_nav_visible(False)
        self.total_pages = 1
        self.current_page = 0

        pix = None
        try:
            reader = QImageReader(file_path)
            reader.setAutoTransform(True)
            sz = reader.size()
            if sz.isValid():
                orig_w, orig_h = sz.width(), sz.height()
                if max(orig_w, orig_h) > 2800:
                    scale_f = 2500.0 / max(orig_w, orig_h)
                    reader.setScaledSize(QSize(int(orig_w * scale_f), int(orig_h * scale_f)))
                qimg = reader.read()
                if not qimg.isNull():
                    pix = QPixmap.fromImage(qimg)
        except Exception:
            pix = None

        if pix is None or pix.isNull():
            pix = QPixmap(file_path)

        if pix.isNull():
            try:
                pil_img = Image.open(file_path)
                pil_img.draft("RGB", (2500, 2500))
                pil_img = pil_img.convert("RGBA")
                data = pil_img.tobytes("raw", "RGBA")
                qimg = QImage(data, pil_img.size[0], pil_img.size[1], QImage.Format.Format_RGBA8888).copy()
                pix = QPixmap.fromImage(qimg)
            except Exception:
                pass

        if pix and not pix.isNull():
            self._raw_pixmap = pix
            w, h = pix.width(), pix.height()
            self.lbl_extra_info.setText(f"Dimensions: {w} × {h} px  •  Zoom: {int(self.zoom_level * 100)}%")
            self._render_image_scaled()
        else:
            self.image_label.setText("Failed to load image preview")

    def _render_image_scaled(self):
        if not self._raw_pixmap or self._raw_pixmap.isNull():
            return

        vp_w = max(50, self.scroll_area.viewport().width() - 24)
        vp_h = max(50, self.scroll_area.viewport().height() - 24)
        orig_w = self._raw_pixmap.width()
        orig_h = self._raw_pixmap.height()

        if self.fit_mode == "fit_page" and vp_h > 50:
            scale = min(vp_w / orig_w, vp_h / orig_h)
        else:  # fit_width
            scale = (vp_w / orig_w) if vp_w > 50 else 1.0

        target_w = max(20, min(4096, int(orig_w * scale * self.zoom_level)))
        target_h = max(20, min(4096, int(orig_h * scale * self.zoom_level)))

        scaled = self._raw_pixmap.scaled(
            target_w, target_h, Qt.KeepAspectRatio, Qt.SmoothTransformation
        )
        self.image_label.setPixmap(scaled)
        self.image_label.resize(scaled.size())
        self.pixmap_rendered.emit()
        self.lbl_extra_info.setText(f"Dimensions: {orig_w} × {orig_h} px  •  Zoom: {int(self.zoom_level * 100)}%")

    # ---------------- TEXT / CODE ----------------
    def _load_text(self, file_path: str):
        self.stack.setCurrentIndex(self.PAGE_TEXT)
        self._set_nav_visible(False)
        self.total_pages = 1

        fsize = os.path.getsize(file_path) if os.path.isfile(file_path) else 0
        is_truncated = fsize > 128 * 1024
        content = ""
        encoding_used = "utf-8"
        for enc in ['utf-8', 'utf-8-sig', 'latin-1', 'cp1252']:
            try:
                with open(file_path, 'r', encoding=enc, errors='replace') as f:
                    content = f.read(128 * 1024)
                encoding_used = enc
                break
            except Exception:
                continue

        if is_truncated:
            content += f"\n\n--- ⚠️ Truncated: Showing first 128 KB of {self._format_size(fsize)} file ---"

        line_count = content.count('\n') + 1
        self.text_editor.setPlainText(content)
        info_txt = f"Lines: {line_count}  •  Encoding: {encoding_used}"
        if is_truncated:
            info_txt += "  •  ⚠️ Truncated to 128 KB"
        self.lbl_extra_info.setText(info_txt)

    # ---------------- ARCHIVES (ZIP / TAR) ----------------
    def _load_archive(self, file_path: str):
        self.stack.setCurrentIndex(self.PAGE_ARCHIVE)
        self._set_nav_visible(False)
        self.total_pages = 1
        self.archive_table.setRowCount(0)

        ext = os.path.splitext(file_path)[1].lower()
        items = []
        try:
            if ext == '.zip':
                with zipfile.ZipFile(file_path, 'r') as zf:
                    for info in zf.infolist()[:500]:
                        items.append((info.filename, self._format_size(info.file_size), self._format_size(info.compress_size)))
            elif ext in ('.tar', '.gz', '.tgz', '.bz2', '.xz'):
                with tarfile.open(file_path, 'r:*') as tf:
                    for member in tf.getmembers()[:500]:
                        items.append((member.name, self._format_size(member.size), "—"))
        except Exception as e:
            items.append((f"Error reading archive: {e}", "", ""))

        self.archive_table.setRowCount(len(items))
        for r, (name, sz, csz) in enumerate(items):
            self.archive_table.setItem(r, 0, QTableWidgetItem(name))
            self.archive_table.setItem(r, 1, QTableWidgetItem(sz))
            self.archive_table.setItem(r, 2, QTableWidgetItem(csz))

        self.lbl_extra_info.setText(f"Archive Entries: {len(items)}")

    # ---------------- MEDIA (AUDIO / VIDEO) ----------------
    def _load_media(self, file_path: str, is_video: bool = False):
        self.stack.setCurrentIndex(self.PAGE_MEDIA)
        self._set_nav_visible(False)
        self.total_pages = 1

        if not QT_MULTIMEDIA_AVAILABLE:
            self._load_hex(file_path, error_msg="QtMultimedia is not available on this platform.")
            return

        if not self._media_player:
            self._media_player = QMediaPlayer(self)
            self._audio_output = QAudioOutput(self)
            self._media_player.setAudioOutput(self._audio_output)
            if self.video_widget:
                self._media_player.setVideoOutput(self.video_widget)

            self._media_player.positionChanged.connect(self._on_media_position_changed)
            self._media_player.durationChanged.connect(self._on_media_duration_changed)

        if self.video_widget:
            self.video_widget.setVisible(is_video)

        self._media_player.setSource(QUrl.fromLocalFile(file_path))
        self.btn_play_pause.setText("▶ Play")
        self.lbl_extra_info.setText("Video" if is_video else "Audio")

    def _toggle_playback(self):
        if not self._media_player:
            return
        if self._media_player.playbackState() == QMediaPlayer.PlaybackState.PlayingState:
            self._media_player.pause()
            self.btn_play_pause.setText("▶ Play")
        else:
            self._media_player.play()
            self.btn_play_pause.setText("⏸ Pause")

    def _on_media_position_changed(self, pos: int):
        self.media_slider.setValue(pos)
        dur = self._media_player.duration() if self._media_player else 0
        self.lbl_media_time.setText(f"{self._format_ms(pos)} / {self._format_ms(dur)}")

    def _on_media_duration_changed(self, dur: int):
        self.media_slider.setRange(0, dur)
        pos = self._media_player.position() if self._media_player else 0
        self.lbl_media_time.setText(f"{self._format_ms(pos)} / {self._format_ms(dur)}")

    def _set_media_position(self, pos: int):
        if self._media_player:
            self._media_player.setPosition(pos)

    def _format_ms(self, ms: int) -> str:
        s = ms // 1000
        m = s // 60
        s = s % 60
        return f"{m:02d}:{s:02d}"

    # ---------------- HEX / BINARY FALLBACK ----------------
    def _load_hex(self, file_path: str, error_msg: Optional[str] = None):
        self.stack.setCurrentIndex(self.PAGE_HEX)
        self._set_nav_visible(False)
        self.total_pages = 1

        lines = []
        if error_msg:
            lines.append(f"Notice: {error_msg}\nShowing Hex Inspection:\n" + "=" * 50)

        try:
            with open(file_path, 'rb') as f:
                data = f.read(1024)
            for i in range(0, len(data), 16):
                chunk = data[i:i + 16]
                hex_str = " ".join(f"{b:02X}" for b in chunk)
                ascii_str = "".join(chr(b) if 32 <= b < 127 else "." for b in chunk)
                lines.append(f"{i:08X}  {hex_str:<48}  |{ascii_str}|")
        except Exception as e:
            lines.append(f"Failed to read file bytes: {e}")

        self.hex_editor.setPlainText("\n".join(lines))
        self.lbl_extra_info.setText("Binary / Hex Inspection")

    # ---------------- METADATA EXTRACTION ----------------
    def get_metadata_dict(self) -> dict:
        """Extracts key metadata from current document, office doc, image or file for comparison."""
        if not self.current_path or not os.path.isfile(self.current_path):
            return {}

        fname = os.path.basename(self.current_path)
        folder = os.path.basename(os.path.dirname(self.current_path))
        fsize = self._format_size(os.path.getsize(self.current_path))
        ext = os.path.splitext(self.current_path)[1].upper()

        try:
            mtime = time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(os.path.getmtime(self.current_path)))
        except Exception:
            mtime = "—"

        meta_dict = {
            "File Name": fname,
            "Folder": folder,
            "Size": fsize,
            "Date Modified": mtime,
            "Format": ext or "Unknown",
            "Pages / Dim": "—",
            "Title": "—",
            "Author": "—",
            "Last Modified By": "—",
            "Sheets / Slides": "—",
            "Dimensions": "—",
            "CreationDate": "—",
        }

        if self.doc:
            meta_dict.update({
                "Pages / Dim": f"{self.doc.page_count} pages", "Pages": str(self.doc.page_count),
                "Title": self.doc.title or "—", "Author": self.doc.author or "—",
                "CreationDate": self.doc.creation_date or "—",
                "Dimensions": f"{int(self.doc.page_width)} × {int(self.doc.page_height)} pt" if self.doc.page_width else "—"
            })
        elif self._office_meta:
            om = self._office_meta
            title = om.get("title") or "—"
            author = om.get("author") or "—"
            mod_by = om.get("modified_by") or "—"
            sheets = om.get("sheets", [])
            slides = om.get("slide_count", 0)
            pages = om.get("page_count", 0)
            words = om.get("word_count", 0)
            created = om.get("created") or "—"

            sheets_slides_str = "—"
            if sheets:
                sheets_slides_str = f"{len(sheets)} sheets: {', '.join(sheets[:3])}{'...' if len(sheets) > 3 else ''}"
            elif slides > 0:
                sheets_slides_str = f"{slides} slides"

            pages_dim_str = "—"
            if pages > 0:
                pages_dim_str = f"{pages} pages"
            elif words > 0:
                pages_dim_str = f"{words} words"

            meta_dict.update({
                "Title": title,
                "Author": author,
                "Last Modified By": mod_by,
                "Sheets / Slides": sheets_slides_str,
                "Pages / Dim": pages_dim_str,
                "CreationDate": created
            })
        elif self._raw_pixmap and not self._raw_pixmap.isNull():
            w = self._raw_pixmap.width()
            h = self._raw_pixmap.height()
            meta_dict["Pages / Dim"] = f"{w} × {h} px"
            meta_dict["Dimensions"] = f"{w} × {h} px"

        return meta_dict

    # ---------------- ZOOM & NAVIGATION ----------------
    def resizeEvent(self, event):
        super().resizeEvent(event)
        if (self.doc or self._raw_pixmap) and self.zoom_level == 1.0:
            QTimer.singleShot(150, self._check_resize_render)

    def _check_resize_render(self):
        if (self.doc or self._raw_pixmap) and self.zoom_level == 1.0:
            self._pixmap_cache.clear()
            self.render_current_page()

    def prev_page(self):
        if self.current_page > 0:
            self.go_to_page(self.current_page - 1)

    def next_page(self):
        if self.current_page < self.total_pages - 1:
            self.go_to_page(self.current_page + 1)

    def go_to_page(self, page_idx: int):
        if 0 <= page_idx < self.total_pages and page_idx != self.current_page:
            self.current_page = page_idx
            self.page_spin.blockSignals(True)
            self.page_spin.setValue(self.current_page + 1)
            self.page_spin.blockSignals(False)
            self.render_current_page()
            self.page_changed.emit(self.current_page)

    def _on_spin_changed(self, val: int):
        page_idx = val - 1
        if 0 <= page_idx < self.total_pages and page_idx != self.current_page:
            self.current_page = page_idx
            self.render_current_page()
            self.page_changed.emit(self.current_page)

    def zoom_in(self):
        if self.zoom_level < 4.0:
            self.zoom_level = round(self.zoom_level * 1.25, 2)
            self._pixmap_cache.clear()
            self.render_current_page()
            self.zoom_changed.emit(self.zoom_level)

    def zoom_out(self):
        if self.zoom_level > 0.25:
            self.zoom_level = round(self.zoom_level / 1.25, 2)
            self._pixmap_cache.clear()
            self.render_current_page()
            self.zoom_changed.emit(self.zoom_level)

    def fit_width(self):
        self.fit_mode = "fit_width"
        self.zoom_level = 1.0
        self._pixmap_cache.clear()
        self.render_current_page()
        self.zoom_changed.emit(self.zoom_level)

    def fit_page(self):
        self.fit_mode = "fit_page"
        self.zoom_level = 1.0
        self._pixmap_cache.clear()
        self.render_current_page()
        self.zoom_changed.emit(self.zoom_level)

    def open_external(self):
        if self.current_path and os.path.isfile(self.current_path):
            try:
                os.startfile(win_long_path(self.current_path))
            except Exception as e:
                self.info_label.setText(f"Cannot open externally: {e}")

    def reveal_in_explorer(self):
        if self.current_path:
            try:
                open_in_file_explorer(self.current_path)
            except Exception as e:
                self.info_label.setText(f"Cannot reveal in File Explorer: {e}")

    def get_current_pixmap(self) -> Optional[QPixmap]:
        """Returns the currently rendered page/image pixmap."""
        pix = self.image_label.pixmap()
        return pix if (pix and not pix.isNull()) else None

    def closeEvent(self, event):
        self._cleanup_resources()
        if self._own_pdf_pool:
            self._own_pdf_pool.close(wait=False)
        super().closeEvent(event)


# Backwards compatibility alias for existing tests
SinglePdfViewer = UniversalFileViewer


class FilmstripCard(QFrame):
    """A thumbnail / metadata card for the duplicate file filmstrip carousel."""
    clicked = Signal(int)

    def __init__(self, index: int, file_info: FileInfo, parent=None):
        super().__init__(parent)
        self.index = index
        self.file_info = file_info
        self.is_selected = False
        self.setObjectName("filmstrip_card")
        self._init_ui()

    def _init_ui(self):
        self.setFixedSize(145, 75)
        self.setCursor(Qt.PointingHandCursor)

        layout = QVBoxLayout(self)
        layout.setContentsMargins(8, 6, 8, 6)
        layout.setSpacing(2)

        # Header: Index Badge + Format
        hdr = QHBoxLayout()
        ext = os.path.splitext(self.file_info.filename)[1].upper() or "FILE"
        badge = QLabel(f"#{self.index + 1} {ext}")
        badge.setStyleSheet("font-size: 10px; font-weight: bold; color: #3b82f6;")
        hdr.addWidget(badge)
        hdr.addStretch(1)

        sz_lbl = QLabel(self.file_info.formatted_size)
        sz_lbl.setStyleSheet("font-size: 10px; color: #94a3b8;")
        hdr.addWidget(sz_lbl)
        layout.addLayout(hdr)

        # File Name
        name_lbl = QLabel(self.file_info.filename)
        name_lbl.setStyleSheet("font-size: 11px; font-weight: 600;")
        name_lbl.setWordWrap(False)
        name_lbl.setToolTip(self.file_info.path)
        layout.addWidget(name_lbl)

        # Folder Name
        dir_name = os.path.basename(os.path.dirname(self.file_info.path))
        dir_lbl = QLabel(f"📁 {dir_name}")
        dir_lbl.setStyleSheet("font-size: 9px; color: #94a3b8;")
        dir_lbl.setToolTip(self.file_info.path)
        layout.addWidget(dir_lbl)

        self.update_style()

    def set_selected(self, selected: bool):
        self.is_selected = selected
        self.update_style()

    def update_style(self):
        if self.is_selected:
            self.setStyleSheet(
                "QFrame#filmstrip_card { border: 2px solid #3b82f6; border-radius: 6px; }"
            )
        else:
            self.setStyleSheet("")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.index)
        super().mousePressEvent(event)


class CurtainCanvas(QWidget):
    """
    High-performance drawing canvas for the visual curtain divider comparison.
    Features:
    - Pre-scaled caching of left and right pixmaps to avoid rescaling on every paint
    - Independent aspect-ratio centering: eliminates alignment drift between cropped/uncropped images
    """
    def __init__(self, owner: 'CurtainCompareWidget'):
        super().__init__(owner)
        self.owner = owner
        self.setMouseTracking(True)
        self._dragging = False
        self._cached_scaled_a: Optional[QPixmap] = None
        self._cached_scaled_b: Optional[QPixmap] = None
        self._ox_a: int = 0
        self._oy_a: int = 0
        self._ox_b: int = 0
        self._oy_b: int = 0

    def resizeEvent(self, event):
        super().resizeEvent(event)
        self.update_cache()

    def update_cache(self):
        """Pre-scales pixmaps to canvas dimensions so paintEvent does ZERO scaling on each frame."""
        w = self.width()
        h = self.height()
        if w < 10 or h < 10:
            return

        pix_a = self.owner.pixmap_a
        pix_b = self.owner.pixmap_b

        if pix_a and not pix_a.isNull():
            scaled_a = pix_a.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._cached_scaled_a = scaled_a
            self._ox_a = (w - scaled_a.width()) // 2
            self._oy_a = (h - scaled_a.height()) // 2
        else:
            self._cached_scaled_a = None

        if pix_b and not pix_b.isNull():
            scaled_b = pix_b.scaled(w, h, Qt.KeepAspectRatio, Qt.SmoothTransformation)
            self._cached_scaled_b = scaled_b
            self._ox_b = (w - scaled_b.width()) // 2
            self._oy_b = (h - scaled_b.height()) // 2
        else:
            self._cached_scaled_b = None

        self.update()

    def paintEvent(self, event):
        painter = QPainter(self)
        painter.setRenderHint(QPainter.Antialiasing)

        w = self.width()
        h = self.height()
        painter.fillRect(0, 0, w, h, QColor("#0b0f19"))

        if not self._cached_scaled_a and not self._cached_scaled_b:
            painter.setPen(QColor("#64748b"))
            painter.setFont(QFont("Segoe UI", 11))
            painter.drawText(self.rect(), Qt.AlignCenter, "Select a duplicate group to compare via Visual Curtain Slider.")
            return

        split_x = max(0, min(w, int(w * self.owner.split_ratio)))

        # 1. Draw Image A (Left side) clipped to split_x
        if self._cached_scaled_a:
            painter.save()
            painter.setClipRect(0, 0, split_x, h)
            painter.drawPixmap(self._ox_a, self._oy_a, self._cached_scaled_a)
            painter.restore()

        # 2. Draw Image B (Right side) clipped from split_x to right edge
        if self._cached_scaled_b:
            painter.save()
            painter.setClipRect(split_x, 0, w - split_x, h)
            painter.drawPixmap(self._ox_b, self._oy_b, self._cached_scaled_b)
            painter.restore()

        # 3. Draw Divider Line
        painter.setPen(QPen(QColor("#38bdf8"), 2))
        painter.drawLine(split_x, 0, split_x, h)

        # 4. Draw Center Circular Drag Handle
        cy = h // 2
        handle_r = 14
        painter.setBrush(QBrush(QColor("#1e293b")))
        painter.setPen(QPen(QColor("#38bdf8"), 2))
        painter.drawEllipse(split_x - handle_r, cy - handle_r, handle_r * 2, handle_r * 2)

        painter.setPen(QColor("#ffffff"))
        painter.setFont(QFont("Segoe UI", 8, QFont.Bold))
        painter.drawText(QRect(split_x - handle_r, cy - handle_r, handle_r * 2, handle_r * 2), Qt.AlignCenter, "⮂ ⮃")

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = True
            self._update_split(_get_pos(event).x())

    def mouseMoveEvent(self, event):
        if self._dragging:
            self._update_split(_get_pos(event).x())
        else:
            split_x = int(self.width() * self.owner.split_ratio)
            if abs(_get_pos(event).x() - split_x) <= 15:
                self.setCursor(Qt.SplitHCursor)
            else:
                self.setCursor(Qt.ArrowCursor)

    def mouseReleaseEvent(self, event):
        if event.button() == Qt.LeftButton:
            self._dragging = False

    def _update_split(self, x: int):
        ratio = max(0.0, min(1.0, x / max(1, self.width())))
        self.owner.slider.blockSignals(True)
        self.owner.slider.setValue(int(ratio * 100))
        self.owner.slider.blockSignals(False)
        self.owner.split_ratio = ratio
        self.update()


class CurtainCompareWidget(QWidget):
    """
    Interactive Visual Curtain / Split Slider widget.
    Allows sliding a vertical divider across two overlapping images or documents
    to instantly detect pixel-level differences, text shifts, or compression artifacts.
    Also includes a 1-click Blink Flip feature for instant A/B flickering.
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap_a: Optional[QPixmap] = None
        self.pixmap_b: Optional[QPixmap] = None
        self.split_ratio: float = 0.5
        self._blink_state: int = 0

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        bar = QFrame()
        bar.setStyleSheet("background-color: #1e293b; border-radius: 6px; padding: 4px;")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(8, 4, 8, 4)
        bar_lay.setSpacing(10)

        self.lbl_p1 = QLabel("🔵 Left: Pane 1")
        self.lbl_p1.setStyleSheet("color: #60a5fa; font-weight: bold; font-size: 11px;")
        bar_lay.addWidget(self.lbl_p1)

        self.slider = QSlider(Qt.Horizontal)
        self.slider.setRange(0, 100)
        self.slider.setValue(50)
        self.slider.setToolTip("Drag slider or drag divider on the image canvas")
        self.slider.valueChanged.connect(self._on_slider_changed)
        bar_lay.addWidget(self.slider, stretch=1)

        self.lbl_p2 = QLabel("🔴 Right: Pane 2")
        self.lbl_p2.setStyleSheet("color: #f87171; font-weight: bold; font-size: 11px;")
        bar_lay.addWidget(self.lbl_p2)

        self.btn_flip = QPushButton("⚡ Blink Flip")
        self.btn_flip.setFixedHeight(22)
        self.btn_flip.setToolTip("Instant A/B flip comparator (click repeatedly to spot subtle differences)")
        self.btn_flip.setStyleSheet("padding: 2px 8px; font-size: 10px; font-weight: bold; color: #fbbf24;")
        self.btn_flip.clicked.connect(self._flip_blink)
        bar_lay.addWidget(self.btn_flip)

        btn_reset = QPushButton("Reset 50%")
        btn_reset.setFixedHeight(22)
        btn_reset.setStyleSheet("padding: 2px 8px; font-size: 10px;")
        btn_reset.clicked.connect(lambda: self.slider.setValue(50))
        bar_lay.addWidget(btn_reset)

        layout.addWidget(bar)

        self.canvas = CurtainCanvas(self)
        layout.addWidget(self.canvas, stretch=1)

    def _flip_blink(self):
        if self._blink_state == 0:
            self.slider.setValue(100)
            self._blink_state = 1
        else:
            self.slider.setValue(0)
            self._blink_state = 0

    def _on_slider_changed(self, val: int):
        self.split_ratio = val / 100.0
        self.canvas.update()

    def set_pixmaps(self, pix_a: Optional[QPixmap], pix_b: Optional[QPixmap], name_a: str = "", name_b: str = ""):
        self.pixmap_a = pix_a
        self.pixmap_b = pix_b
        if name_a:
            self.lbl_p1.setText(f"🔵 Left: {name_a[:28]}")
        if name_b:
            self.lbl_p2.setText(f"🔴 Right: {name_b[:28]}")
        self.canvas.update_cache()


class HeatmapCompareWidget(QWidget):
    """
    Studio-Grade Visual Difference Heatmap Comparator.
    Uses Pillow (ImageChops.difference) to calculate mathematical pixel differences between two documents/images.
    Features:
    - Mathematical diff % calculation: (sum of pixel variances) / (total_pixels * 255) * 100
    - Neon Magenta/Amber difference amplification overlay
    - Real-time Sensitivity adjustment (1x to 20x multiplier)
    - 3 Blend Modes: Heatmap Only, Amplified Diff Overlay, Inverted Diff
    - Interactive ScrollArea with Ctrl+Wheel zoom & Hand Drag-Pan
    """
    def __init__(self, parent=None):
        super().__init__(parent)
        self.pixmap_a: Optional[QPixmap] = None
        self.pixmap_b: Optional[QPixmap] = None
        self.name_a: str = ""
        self.name_b: str = ""
        self._diff_pct: float = 0.0
        self._raw_diff_img: Optional[Image.Image] = None
        self._ref_size: Tuple[int, int] = (0, 0)
        self.zoom_level: float = 1.0

        # Panning state
        self._is_panning = False
        self._pan_start = QPoint()
        self._pan_start_h = 0
        self._pan_start_v = 0

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setContentsMargins(0, 0, 0, 0)
        layout.setSpacing(4)

        # Control Bar
        bar = QFrame()
        bar.setStyleSheet("background-color: #1e293b; border-radius: 6px; padding: 4px;")
        bar_lay = QHBoxLayout(bar)
        bar_lay.setContentsMargins(8, 4, 8, 4)
        bar_lay.setSpacing(10)

        self.lbl_stats = QLabel("Diff: —")
        self.lbl_stats.setStyleSheet("font-weight: bold; font-size: 11px; color: #10b981;")
        bar_lay.addWidget(self.lbl_stats)

        bar_lay.addWidget(QLabel("Mode:"))
        self.combo_mode = QComboBox()
        self.combo_mode.addItems(["🔥 Heatmap Only", "⚡ Amplified Overlay", "🔄 Inverted Diff"])
        self.combo_mode.currentIndexChanged.connect(self._render_diff_pixmap)
        bar_lay.addWidget(self.combo_mode)

        bar_lay.addWidget(QLabel("Boost:"))
        self.slider_boost = QSlider(Qt.Horizontal)
        self.slider_boost.setRange(1, 20)
        self.slider_boost.setValue(5)
        self.slider_boost.setToolTip("Adjust difference sensitivity/amplification factor (1x to 20x)")
        self.slider_boost.valueChanged.connect(self._render_diff_pixmap)
        bar_lay.addWidget(self.slider_boost)

        self.lbl_boost_val = QLabel("5x")
        self.lbl_boost_val.setStyleSheet("font-size: 10px; color: #94a3b8; min-width: 20px;")
        bar_lay.addWidget(self.lbl_boost_val)

        btn_zoom_out = QPushButton("−")
        btn_zoom_out.setFixedSize(22, 22)
        btn_zoom_out.clicked.connect(self.zoom_out)
        btn_zoom_in = QPushButton("+")
        btn_zoom_in.setFixedSize(22, 22)
        btn_zoom_in.clicked.connect(self.zoom_in)
        btn_zoom_reset = QPushButton("Fit")
        btn_zoom_reset.setFixedHeight(22)
        btn_zoom_reset.clicked.connect(self.reset_zoom)

        bar_lay.addWidget(btn_zoom_out)
        bar_lay.addWidget(btn_zoom_in)
        bar_lay.addWidget(btn_zoom_reset)

        layout.addWidget(bar)

        # Scroll area with heatmap label
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("preview_scroll_area")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setAlignment(Qt.AlignCenter)
        self.scroll_area.viewport().installEventFilter(self)

        self.lbl_display = QLabel("Select duplicate files to generate Visual Difference Heatmap.")
        self.lbl_display.setAlignment(Qt.AlignCenter)
        self.lbl_display.setObjectName("preview_image_label")
        self.scroll_area.setWidget(self.lbl_display)

        layout.addWidget(self.scroll_area, stretch=1)

    def eventFilter(self, watched, event):
        if watched == self.scroll_area.viewport():
            if event.type() == QEvent.Wheel and (event.modifiers() & Qt.ControlModifier):
                delta = event.angleDelta().y()
                if delta > 0:
                    self.zoom_in()
                elif delta < 0:
                    self.zoom_out()
                return True
            elif event.type() == QEvent.MouseButtonPress and event.button() == Qt.LeftButton:
                h_bar = self.scroll_area.horizontalScrollBar()
                v_bar = self.scroll_area.verticalScrollBar()
                if h_bar.maximum() > 0 or v_bar.maximum() > 0:
                    self._is_panning = True
                    self._pan_start = _get_pos(event)
                    self._pan_start_h = h_bar.value()
                    self._pan_start_v = v_bar.value()
                    self.scroll_area.viewport().setCursor(Qt.ClosedHandCursor)
                    return True
            elif event.type() == QEvent.MouseMove:
                if self._is_panning:
                    delta = _get_pos(event) - self._pan_start
                    self.scroll_area.horizontalScrollBar().setValue(self._pan_start_h - delta.x())
                    self.scroll_area.verticalScrollBar().setValue(self._pan_start_v - delta.y())
                    return True
                else:
                    h_bar = self.scroll_area.horizontalScrollBar()
                    v_bar = self.scroll_area.verticalScrollBar()
                    if h_bar.maximum() > 0 or v_bar.maximum() > 0:
                        self.scroll_area.viewport().setCursor(Qt.OpenHandCursor)
                    else:
                        self.scroll_area.viewport().setCursor(Qt.ArrowCursor)
            elif event.type() == QEvent.MouseButtonRelease and event.button() == Qt.LeftButton and self._is_panning:
                self._is_panning = False
                h_bar = self.scroll_area.horizontalScrollBar()
                v_bar = self.scroll_area.verticalScrollBar()
                if h_bar.maximum() > 0 or v_bar.maximum() > 0:
                    self.scroll_area.viewport().setCursor(Qt.OpenHandCursor)
                else:
                    self.scroll_area.viewport().setCursor(Qt.ArrowCursor)
                return True

        return super().eventFilter(watched, event)

    def zoom_in(self):
        if self.zoom_level < 4.0:
            self.zoom_level = round(self.zoom_level * 1.25, 2)
            self._render_diff_pixmap()

    def zoom_out(self):
        if self.zoom_level > 0.25:
            self.zoom_level = round(self.zoom_level / 1.25, 2)
            self._render_diff_pixmap()

    def reset_zoom(self):
        self.zoom_level = 1.0
        self._render_diff_pixmap()

    def set_pixmaps(self, pix_a: Optional[QPixmap], pix_b: Optional[QPixmap], name_a: str = "", name_b: str = ""):
        self.pixmap_a = pix_a
        self.pixmap_b = pix_b
        self.name_a = name_a
        self.name_b = name_b
        self._compute_difference()

    def _qpixmap_to_pil(self, pix: QPixmap) -> Optional[Image.Image]:
        if not pix or pix.isNull():
            return None
        qimg = pix.toImage().convertToFormat(QImage.Format.Format_RGBA8888)
        w, h = qimg.width(), qimg.height()
        ptr = qimg.constBits()
        try:
            return Image.frombuffer("RGBA", (w, h), bytes(ptr), "raw", "RGBA", 0, 1).convert("RGB")
        except Exception:
            return None

    def _compute_difference(self):
        if not self.pixmap_a or self.pixmap_a.isNull() or not self.pixmap_b or self.pixmap_b.isNull():
            self._raw_diff_img = None
            self.lbl_stats.setText("Diff: — (Need 2 files)")
            self.lbl_stats.setStyleSheet("font-weight: bold; font-size: 11px; color: #94a3b8;")
            self.lbl_display.setText("Both Pane 1 and Pane 2 must have active previews for heatmap comparison.")
            return

        img_a = self._qpixmap_to_pil(self.pixmap_a)
        img_b = self._qpixmap_to_pil(self.pixmap_b)

        if not img_a or not img_b:
            self.lbl_display.setText("Failed to convert pixmaps for difference calculation.")
            return

        # Resize b to match a if dimensions differ
        if img_a.size != img_b.size:
            try:
                resample_filter = Image.Resampling.BILINEAR
            except AttributeError:
                resample_filter = Image.BILINEAR
            img_b = img_b.resize(img_a.size, resample=resample_filter)

        self._ref_size = img_a.size
        diff = ImageChops.difference(img_a, img_b)
        self._raw_diff_img = diff

        # Calculate exact mathematical difference percentage
        gray_diff = diff.convert("L")
        stat = gray_diff.getextrema()
        if stat == (0, 0):
            self._diff_pct = 0.0
            self.lbl_stats.setText("✓ 100% Identical Pixels (0.00% Diff)")
            self.lbl_stats.setStyleSheet("font-weight: bold; font-size: 11px; color: #10b981;")
        else:
            hist = gray_diff.histogram()
            total_sum = sum(i * count for i, count in enumerate(hist))
            total_pixels = img_a.size[0] * img_a.size[1]
            self._diff_pct = (total_sum / (total_pixels * 255.0)) * 100.0
            if self._diff_pct < 0.05:
                color = "#f59e0b"
            else:
                color = "#ef4444"
            self.lbl_stats.setText(f"⚠️ Diff: {self._diff_pct:.2f}% Variance")
            self.lbl_stats.setStyleSheet(f"font-weight: bold; font-size: 11px; color: {color};")

        self._render_diff_pixmap()

    def _render_diff_pixmap(self):
        if not self._raw_diff_img:
            return

        boost = self.slider_boost.value()
        self.lbl_boost_val.setText(f"{boost}x")
        mode = self.combo_mode.currentIndex()

        diff_rgb = self._raw_diff_img
        if boost > 1:
            enhancer = ImageEnhance.Brightness(diff_rgb)
            amplified = enhancer.enhance(float(boost))
        else:
            amplified = diff_rgb

        if mode == 0:
            # 🔥 Heatmap Only: neon magenta (#ec4899) and intense amber (#f59e0b)
            gray = amplified.convert("L")
            lut_r = [min(255, int(v * 1.5)) for v in range(256)]
            lut_g = [int(v * 0.4) for v in range(256)]
            lut_b = [min(255, int(v * 1.2)) for v in range(256)]
            r = gray.point(lut_r)
            g = gray.point(lut_g)
            b = gray.point(lut_b)
            out_img = Image.merge("RGB", (r, g, b))
        elif mode == 1:
            # ⚡ Amplified Overlay
            out_img = amplified
        else:
            # 🔄 Inverted Diff
            out_img = ImageChops.invert(amplified)

        vp_w = max(100, self.scroll_area.viewport().width() - 20)
        vp_h = max(100, self.scroll_area.viewport().height() - 20)
        orig_w, orig_h = out_img.size

        base_scale = min(vp_w / max(1, orig_w), vp_h / max(1, orig_h))
        target_w = max(20, min(4096, int(orig_w * base_scale * self.zoom_level)))
        target_h = max(20, min(4096, int(orig_h * base_scale * self.zoom_level)))

        try:
            resample_filter = Image.Resampling.BILINEAR
        except AttributeError:
            resample_filter = Image.BILINEAR

        scaled_pil = out_img.resize((target_w, target_h), resample=resample_filter)
        rgba = scaled_pil.convert("RGBA")
        raw_bytes = rgba.tobytes("raw", "RGBA")

        qimg = QImage(raw_bytes, target_w, target_h, QImage.Format.Format_RGBA8888)
        pix = QPixmap.fromImage(qimg)
        self.lbl_display.setPixmap(pix)
        self.lbl_display.resize(pix.size())


class ComparePreviewPanel(QWidget):
    """
    Simultaneous multi-file preview and comparison panel (Studio-Grade).
    Supports Dual (2-Up), Visual Curtain Slider, Difference Heatmap, Triple (3-Up),
    Quad (2x2 Grid), and Filmstrip Carousel modes.
    Includes synchronized proportional scrolling, page flipping, zooming, and dynamic multi-column metadata comparison.
    """
    LAYOUT_DUAL = "👥 Dual (2-Up)"
    LAYOUT_CURTAIN = "🪟 Visual Curtain (Slider)"
    LAYOUT_HEATMAP = "🔥 Difference Heatmap"
    LAYOUT_TRIPLE = "📐 Triple (3-Up)"
    LAYOUT_QUAD = "🔲 Quad (2x2)"
    LAYOUT_CAROUSEL = "🎞️ Carousel Filmstrip"

    def __init__(self, parent=None):
        super().__init__(parent)
        self._pdf_pool = None
        self.current_files: List[FileInfo] = []
        self._sync_paging = True
        self._is_syncing_scroll = False
        self._current_layout_mode = self.LAYOUT_DUAL

        # Pool of 4 viewers
        self.viewers: List[UniversalFileViewer] = [
            UniversalFileViewer(title_prefix="Pane 1", pool_provider=self._get_pdf_pool),
            UniversalFileViewer(title_prefix="Pane 2", pool_provider=self._get_pdf_pool),
            UniversalFileViewer(title_prefix="Pane 3", pool_provider=self._get_pdf_pool),
            UniversalFileViewer(title_prefix="Pane 4", pool_provider=self._get_pdf_pool)
        ]

        # For backwards compatibility with tests and callers
        self.viewer_left = self.viewers[0]
        self.viewer_right = self.viewers[1]

        self.curtain_widget = CurtainCompareWidget(self)
        self.heatmap_widget = HeatmapCompareWidget(self)

        self.filmstrip_cards: List[FilmstripCard] = []
        self.combo_items: List[Tuple[QLabel, QComboBox]] = []

        self._init_ui()

    def _get_pdf_pool(self):
        if self._pdf_pool is None:
            self._pdf_pool = PdfWorkerPool()
        return self._pdf_pool

    def closeEvent(self, event):
        self.unload_all()
        if self._pdf_pool:
            self._pdf_pool.close(wait=False)
        super().closeEvent(event)

    def _init_ui(self):
        main_layout = QVBoxLayout(self)
        main_layout.setContentsMargins(6, 6, 6, 6)
        main_layout.setSpacing(6)

        # ---------------- Control Box ----------------
        control_box = QFrame()
        control_box.setObjectName("control_box")
        box_lay = QVBoxLayout(control_box)
        box_lay.setContentsMargins(8, 6, 8, 6)
        box_lay.setSpacing(6)

        # Row 1: Header + Match Badge + Layout Selector + Sync
        row1 = QHBoxLayout()
        row1.setSpacing(8)

        top_label = QLabel("<b>Multi-File Comparison & Verification</b>")
        top_label.setStyleSheet("font-size: 12px; font-weight: 600;")
        row1.addWidget(top_label)

        self.lbl_match_badge = QLabel("Select group to compare")
        self.lbl_match_badge.setObjectName("match_badge")
        row1.addWidget(self.lbl_match_badge)
        row1.addStretch(1)

        # Layout selector
        lbl_mode = QLabel("Layout:")
        lbl_mode.setStyleSheet("font-size: 11px; font-weight: 600;")
        row1.addWidget(lbl_mode)

        self.combo_layout = QComboBox()
        self.combo_layout.addItems([
            self.LAYOUT_DUAL,
            self.LAYOUT_CURTAIN,
            self.LAYOUT_HEATMAP,
            self.LAYOUT_TRIPLE,
            self.LAYOUT_QUAD,
            self.LAYOUT_CAROUSEL
        ])
        self.combo_layout.currentTextChanged.connect(self._on_layout_mode_changed)
        row1.addWidget(self.combo_layout)

        self.btn_meta_toggle = QPushButton("ℹ️ Metadata")
        self.btn_meta_toggle.setFixedHeight(24)
        self.btn_meta_toggle.setCheckable(True)
        self.btn_meta_toggle.toggled.connect(self._toggle_meta_panel)
        row1.addWidget(self.btn_meta_toggle)

        self.chk_sync = QCheckBox("🔗 Sync Navigation")
        self.chk_sync.setChecked(True)
        self.chk_sync.toggled.connect(self._on_sync_toggled)
        row1.addWidget(self.chk_sync)

        box_lay.addLayout(row1)

        # Row 2: File Selector Combos (Pane 1..4)
        self.selectors_container = QWidget()
        row2 = QHBoxLayout(self.selectors_container)
        row2.setContentsMargins(0, 0, 0, 0)
        row2.setSpacing(8)

        self.combos: List[QComboBox] = []
        self.combo_labels: List[QLabel] = []

        pane_colors = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b"]
        for i in range(4):
            lbl = QLabel(f"<b>P{i+1}:</b>")
            lbl.setStyleSheet(f"color: {pane_colors[i]}; font-size: 12px; font-weight: bold;")
            combo = QComboBox()
            combo.currentIndexChanged.connect(lambda idx, p=i: self._on_pane_file_selected(p, idx))

            row2.addWidget(lbl)
            row2.addWidget(combo, stretch=1)

            self.combos.append(combo)
            self.combo_labels.append(lbl)
            self.combo_items.append((lbl, combo))

        # For backwards compatibility
        self.combo_left = self.combos[0]
        self.combo_right = self.combos[1]

        box_lay.addWidget(self.selectors_container)

        # Row 3: Filmstrip Carousel Bar
        self.filmstrip_scroll = QScrollArea()
        self.filmstrip_scroll.setObjectName("filmstrip_scroll")
        self.filmstrip_scroll.setFixedHeight(90)
        self.filmstrip_scroll.setWidgetResizable(True)
        self.filmstrip_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)

        self.filmstrip_container = QWidget()
        self.filmstrip_layout = QHBoxLayout(self.filmstrip_container)
        self.filmstrip_layout.setContentsMargins(4, 4, 4, 4)
        self.filmstrip_layout.setSpacing(8)
        self.filmstrip_layout.addStretch(1)
        self.filmstrip_scroll.setWidget(self.filmstrip_container)

        box_lay.addWidget(self.filmstrip_scroll)
        self.filmstrip_scroll.setVisible(False)

        # Row 4: Dynamic Metadata Grid
        self.meta_frame = QFrame()
        self.meta_frame.setObjectName("meta_frame")
        self.meta_frame.setVisible(False)
        self.meta_grid = QGridLayout(self.meta_frame)
        self.meta_grid.setContentsMargins(8, 6, 8, 6)
        self.meta_grid.setSpacing(6)
        box_lay.addWidget(self.meta_frame)

        main_layout.addWidget(control_box)

        # ---------------- Splitter Container for Viewers ----------------
        self.splitter_container = QWidget()
        self.grid_layout = QGridLayout(self.splitter_container)
        self.grid_layout.setContentsMargins(0, 0, 0, 0)
        self.grid_layout.setSpacing(4)

        for idx, viewer in enumerate(self.viewers):
            viewer.page_changed.connect(lambda p, origin=idx: self._on_viewer_page_changed(origin, p))
            viewer.zoom_changed.connect(lambda z, origin=idx: self._on_viewer_zoom_changed(origin, z))
            v_bar = viewer.scroll_area.verticalScrollBar()
            h_bar = viewer.scroll_area.horizontalScrollBar()
            v_bar.valueChanged.connect(lambda val, origin=idx: self._on_viewer_vscroll(origin, val))
            h_bar.valueChanged.connect(lambda val, origin=idx: self._on_viewer_hscroll(origin, val))

        self.viewers[0].pixmap_rendered.connect(self._refresh_curtain_and_heatmap)
        self.viewers[1].pixmap_rendered.connect(self._refresh_curtain_and_heatmap)

        self._arrange_viewers_layout(self.LAYOUT_DUAL)
        main_layout.addWidget(self.splitter_container, stretch=1)

    def _on_viewer_vscroll(self, origin_index: int, val: int):
        if not self._sync_paging or self._is_syncing_scroll:
            return
        origin_bar = self.viewers[origin_index].scroll_area.verticalScrollBar()
        max_val = origin_bar.maximum()
        if max_val <= 0:
            return
        ratio = val / float(max_val)
        self._is_syncing_scroll = True
        try:
            for i, v in enumerate(self.viewers):
                if i != origin_index and not v.isHidden():
                    t_bar = v.scroll_area.verticalScrollBar()
                    if t_bar.maximum() > 0:
                        t_bar.setValue(int(ratio * t_bar.maximum()))
        finally:
            self._is_syncing_scroll = False

    def _on_viewer_hscroll(self, origin_index: int, val: int):
        if not self._sync_paging or self._is_syncing_scroll:
            return
        origin_bar = self.viewers[origin_index].scroll_area.horizontalScrollBar()
        max_val = origin_bar.maximum()
        if max_val <= 0:
            return
        ratio = val / float(max_val)
        self._is_syncing_scroll = True
        try:
            for i, v in enumerate(self.viewers):
                if i != origin_index and not v.isHidden():
                    t_bar = v.scroll_area.horizontalScrollBar()
                    if t_bar.maximum() > 0:
                        t_bar.setValue(int(ratio * t_bar.maximum()))
        finally:
            self._is_syncing_scroll = False

    def _on_layout_mode_changed(self, mode: str):
        self._current_layout_mode = mode
        self._arrange_viewers_layout(mode)
        self._refresh_visible_viewers()
        if not self.meta_frame.isHidden():
            self._update_metadata_comparison()

    def _set_pane_visible(self, index: int, visible: bool):
        self.combos[index].setVisible(visible)
        self.combo_labels[index].setVisible(visible)

    def _arrange_viewers_layout(self, mode: str):
        """Rearranges the 4 viewers in the grid based on selected layout mode."""
        for viewer in self.viewers:
            viewer.comparison_viewport = (640, 900) if mode in (self.LAYOUT_CURTAIN, self.LAYOUT_HEATMAP) else None
        for i in reversed(range(self.grid_layout.count())):
            w = self.grid_layout.itemAt(i).widget()
            if w:
                self.grid_layout.removeWidget(w)

        if mode == self.LAYOUT_CURTAIN:
            self.filmstrip_scroll.setVisible(False)
            self.heatmap_widget.hide()
            self.grid_layout.addWidget(self.curtain_widget, 0, 0, 1, 2)
            self.curtain_widget.show()
            for v in self.viewers:
                v.hide()
            self._set_pane_visible(0, True)
            self._set_pane_visible(1, True)
            self._set_pane_visible(2, False)
            self._set_pane_visible(3, False)
            self._refresh_curtain()

        elif mode == self.LAYOUT_HEATMAP:
            self.filmstrip_scroll.setVisible(False)
            self.curtain_widget.hide()
            self.grid_layout.addWidget(self.heatmap_widget, 0, 0, 1, 2)
            self.heatmap_widget.show()
            for v in self.viewers:
                v.hide()
            self._set_pane_visible(0, True)
            self._set_pane_visible(1, True)
            self._set_pane_visible(2, False)
            self._set_pane_visible(3, False)
            self._refresh_heatmap()

        elif mode == self.LAYOUT_DUAL:
            self.curtain_widget.hide()
            self.heatmap_widget.hide()
            self.filmstrip_scroll.setVisible(False)
            self.grid_layout.addWidget(self.viewers[0], 0, 0)
            self.grid_layout.addWidget(self.viewers[1], 0, 1)
            self.viewers[0].show()
            self.viewers[1].show()
            self.viewers[2].hide()
            self.viewers[3].hide()
            self._set_pane_visible(0, True)
            self._set_pane_visible(1, True)
            self._set_pane_visible(2, False)
            self._set_pane_visible(3, False)

        elif mode == self.LAYOUT_TRIPLE:
            self.curtain_widget.hide()
            self.heatmap_widget.hide()
            self.filmstrip_scroll.setVisible(False)
            self.grid_layout.addWidget(self.viewers[0], 0, 0)
            self.grid_layout.addWidget(self.viewers[1], 0, 1)
            self.grid_layout.addWidget(self.viewers[2], 0, 2)
            self.viewers[0].show()
            self.viewers[1].show()
            self.viewers[2].show()
            self.viewers[3].hide()
            self._set_pane_visible(0, True)
            self._set_pane_visible(1, True)
            self._set_pane_visible(2, True)
            self._set_pane_visible(3, False)

        elif mode == self.LAYOUT_QUAD:
            self.curtain_widget.hide()
            self.heatmap_widget.hide()
            self.filmstrip_scroll.setVisible(False)
            self.grid_layout.addWidget(self.viewers[0], 0, 0)
            self.grid_layout.addWidget(self.viewers[1], 0, 1)
            self.grid_layout.addWidget(self.viewers[2], 1, 0)
            self.grid_layout.addWidget(self.viewers[3], 1, 1)
            for v in self.viewers:
                v.show()
            for i in range(4):
                self._set_pane_visible(i, True)

        elif mode == self.LAYOUT_CAROUSEL:
            self.curtain_widget.hide()
            self.heatmap_widget.hide()
            self.filmstrip_scroll.setVisible(True)
            self.grid_layout.addWidget(self.viewers[0], 0, 0)
            self.grid_layout.addWidget(self.viewers[1], 0, 1)
            self.viewers[0].show()
            self.viewers[1].show()
            self.viewers[2].hide()
            self.viewers[3].hide()
            self._set_pane_visible(0, True)
            self._set_pane_visible(1, True)
            self._set_pane_visible(2, False)
            self._set_pane_visible(3, False)

    def _refresh_visible_viewers(self):
        """Loads files into visible viewers based on their combo selections."""
        for i, viewer in enumerate(self.viewers):
            if not viewer.isHidden() or (self._current_layout_mode in (self.LAYOUT_CURTAIN, self.LAYOUT_HEATMAP) and i < 2):
                idx = self.combos[i].currentIndex()
                if 0 <= idx < len(self.current_files):
                    viewer.load_file(self.current_files[idx].path)
                else:
                    viewer.load_file("")
        if self._current_layout_mode == self.LAYOUT_CURTAIN:
            self._refresh_curtain()
        elif self._current_layout_mode == self.LAYOUT_HEATMAP:
            self._refresh_heatmap()

    def _refresh_curtain_and_heatmap(self):
        if not self.meta_frame.isHidden():
            self._update_metadata_comparison()
        if self._current_layout_mode == self.LAYOUT_CURTAIN:
            self._refresh_curtain()
        elif self._current_layout_mode == self.LAYOUT_HEATMAP:
            self._refresh_heatmap()

    def _refresh_curtain(self):
        """Refreshes pixmaps and file titles on the curtain compare widget."""
        if self._current_layout_mode != self.LAYOUT_CURTAIN:
            return
        pix_a = self._comparison_pixmap(self.viewers[0])
        pix_b = self._comparison_pixmap(self.viewers[1])
        name_a = self.combos[0].currentText() if self.combos and self.combos[0].count() > 0 else "Pane 1"
        name_b = self.combos[1].currentText() if len(self.combos) > 1 and self.combos[1].count() > 0 else "Pane 2"
        self.curtain_widget.set_pixmaps(pix_a, pix_b, name_a, name_b)

    def _refresh_heatmap(self):
        """Refreshes pixmaps and file titles on the visual difference heatmap widget."""
        if self._current_layout_mode != self.LAYOUT_HEATMAP:
            return
        pix_a = self._comparison_pixmap(self.viewers[0])
        pix_b = self._comparison_pixmap(self.viewers[1])
        name_a = self.combos[0].currentText() if self.combos and self.combos[0].count() > 0 else "Pane 1"
        name_b = self.combos[1].currentText() if len(self.combos) > 1 and self.combos[1].count() > 0 else "Pane 2"
        self.heatmap_widget.set_pixmaps(pix_a, pix_b, name_a, name_b)

    @staticmethod
    def _comparison_pixmap(viewer):
        if viewer.doc and viewer.comparison_viewport:
            expected = (viewer.current_page, 1.0, 640, 900, "fit_page")
            if viewer._display_key != expected:
                return None
        return viewer.get_current_pixmap()

    def _rebuild_filmstrip(self):
        for card in self.filmstrip_cards:
            card.setParent(None)
        self.filmstrip_cards.clear()

        stretch_item = self.filmstrip_layout.takeAt(self.filmstrip_layout.count() - 1)

        for i, finfo in enumerate(self.current_files):
            card = FilmstripCard(i, finfo, self.filmstrip_container)
            card.clicked.connect(self._on_filmstrip_card_clicked)
            self.filmstrip_cards.append(card)
            self.filmstrip_layout.addWidget(card)

        self.filmstrip_layout.addItem(stretch_item)
        self._highlight_active_cards()

    def _highlight_active_cards(self):
        active_indices = {self.combos[i].currentIndex() for i, v in enumerate(self.viewers) if not v.isHidden()}
        for card in self.filmstrip_cards:
            card.set_selected(card.index in active_indices)

    def _on_filmstrip_card_clicked(self, file_index: int):
        if self.combos[0].currentIndex() != file_index:
            self.combos[0].setCurrentIndex(file_index)
        elif len(self.current_files) > 1 and not self.combos[1].isHidden():
            self.combos[1].setCurrentIndex(file_index)
        self._highlight_active_cards()

    def _toggle_meta_panel(self, checked: bool):
        self.meta_frame.setVisible(checked)
        if checked:
            self._update_metadata_comparison()

    def _update_metadata_comparison(self):
        for i in reversed(range(self.meta_grid.count())):
            w = self.meta_grid.itemAt(i).widget()
            if w:
                w.setParent(None)

        visible_viewers = [(i, v) for i, v in enumerate(self.viewers) if not v.isHidden()]
        if not visible_viewers:
            return

        hdr_prop = QLabel("<b>Property</b>")
        hdr_prop.setStyleSheet("font-size: 11px; font-weight: 700;")
        self.meta_grid.addWidget(hdr_prop, 0, 0)

        pane_colors = ["#3b82f6", "#ef4444", "#10b981", "#f59e0b"]
        for col_idx, (p_idx, _) in enumerate(visible_viewers, start=1):
            lbl_title = QLabel(f"<b>Pane {p_idx + 1}</b>")
            lbl_title.setStyleSheet(f"color: {pane_colors[p_idx % len(pane_colors)]}; font-weight: bold; font-size: 11px;")
            self.meta_grid.addWidget(lbl_title, 0, col_idx)

        properties = [
            "File Name", "Folder", "Size", "Date Modified", "Format", "Pages / Dim", "Title",
            "Author", "Last Modified By", "Sheets / Slides", "Dimensions", "CreationDate"
        ]

        current_row = 1
        for prop in properties:
            vals = [viewer.get_metadata_dict().get(prop, "—") for _, viewer in visible_viewers]

            if prop not in ("File Name", "Folder", "Size", "Date Modified", "Format") and all(v == "—" or not v for v in vals):
                continue

            has_multiple = len(visible_viewers) > 1
            distinct_vals = set(vals)
            is_diff = has_multiple and len(distinct_vals) > 1
            is_match = has_multiple and len(distinct_vals) == 1 and any(v != "—" and v for v in vals)

            lbl_p = QLabel()
            if is_diff:
                lbl_p.setText(f"{prop} <span style='color: #f59e0b; font-size: 9px; font-weight: 700;'>⚠️ DIFF</span>:")
            elif is_match:
                lbl_p.setText(f"{prop} <span style='color: #10b981; font-size: 9px; font-weight: 600;'>✓ MATCH</span>:")
            else:
                lbl_p.setText(f"{prop}:")
            lbl_p.setStyleSheet("color: #94a3b8; font-weight: 600; font-size: 11px;")
            self.meta_grid.addWidget(lbl_p, current_row, 0)

            for col_idx, (_, viewer) in enumerate(visible_viewers, start=1):
                val = vals[col_idx - 1]
                val_lbl = QLabel(str(val))
                val_lbl.setWordWrap(True)
                if is_diff:
                    val_lbl.setStyleSheet(
                        "font-size: 11px; padding: 2px 4px; "
                        "background-color: rgba(245, 158, 11, 0.12); "
                        "border-left: 2px solid #f59e0b; border-radius: 2px;"
                    )
                elif is_match:
                    val_lbl.setStyleSheet(
                        "font-size: 11px; padding: 2px 4px; "
                        "background-color: rgba(16, 185, 129, 0.08); "
                        "border-left: 2px solid #10b981; border-radius: 2px;"
                    )
                else:
                    val_lbl.setStyleSheet("font-size: 11px; padding: 2px 4px;")
                self.meta_grid.addWidget(val_lbl, current_row, col_idx)

            current_row += 1

    def select_comparison_file(self, file_idx: int):
        """Updates Pane 2 to the specified file index without reloading Pane 1 or resetting page/zoom."""
        if 0 <= file_idx < len(self.current_files):
            if len(self.combos) > 1:
                self.combos[1].setCurrentIndex(file_idx)

    def load_duplicate_group(
        self,
        files: List[FileInfo],
        match_type: str = "Exact Match",
        selected_idx_a: int = 0,
        selected_idx_b: int = 1
    ):
        """Loads a duplicate group and configures views, combos, role badges, and filmstrip."""
        self.current_files = files
        potential = is_heuristic_match(match_type)
        self.lbl_match_badge.setText("POTENTIAL MATCH (Manual Review Required)" if potential else f"Match: {match_type}")

        for combo in self.combos:
            combo.blockSignals(True)
            combo.clear()

        if not files:
            for combo in self.combos:
                combo.blockSignals(False)
            for viewer in self.viewers:
                viewer.load_file("")
                viewer.set_role_badge("none")
            self._rebuild_filmstrip()
            self.filmstrip_scroll.setVisible(False)
            if not self.meta_frame.isHidden():
                self._update_metadata_comparison()
            if self._current_layout_mode == self.LAYOUT_CURTAIN:
                self.curtain_widget.set_pixmaps(None, None)
            elif self._current_layout_mode == self.LAYOUT_HEATMAP:
                self.heatmap_widget.set_pixmaps(None, None)
            return

        for idx, f in enumerate(files):
            label = f"[{idx + 1}] {f.filename} ({f.formatted_size})"
            for combo in self.combos:
                combo.addItem(label, f.path)

        # Auto-switch layout mode if more files exist and user is in default Dual mode
        self.combo_layout.blockSignals(True)
        if len(files) == 3 and self._current_layout_mode == self.LAYOUT_DUAL:
            self.combo_layout.setCurrentText(self.LAYOUT_TRIPLE)
            self._current_layout_mode = self.LAYOUT_TRIPLE
            self._arrange_viewers_layout(self.LAYOUT_TRIPLE)
        elif len(files) >= 4 and self._current_layout_mode in (self.LAYOUT_DUAL, self.LAYOUT_TRIPLE):
            self.combo_layout.setCurrentText(self.LAYOUT_CAROUSEL)
            self._current_layout_mode = self.LAYOUT_CAROUSEL
            self._arrange_viewers_layout(self.LAYOUT_CAROUSEL)
        self.combo_layout.blockSignals(False)

        idx_a = max(0, min(selected_idx_a, len(files) - 1))
        idx_b = max(0, min(selected_idx_b, len(files) - 1))
        if len(files) > 1 and idx_a == idx_b:
            idx_b = (idx_a + 1) % len(files)

        self.combos[0].setCurrentIndex(idx_a)
        self.combos[1].setCurrentIndex(idx_b)

        if len(files) > 2:
            idx_c = (max(idx_a, idx_b) + 1) % len(files)
            self.combos[2].setCurrentIndex(idx_c)
        else:
            self.combos[2].setCurrentIndex(idx_a)

        if len(files) > 3:
            idx_d = (max(idx_a, idx_b) + 2) % len(files)
            self.combos[3].setCurrentIndex(idx_d)
        else:
            self.combos[3].setCurrentIndex(idx_b)

        for combo in self.combos:
            combo.blockSignals(False)

        # Role badges: Viewer 0 is original (keep), others are duplicates
        self.viewers[0].set_role_badge("potential" if potential else "keep")
        for v in self.viewers[1:]:
            v.set_role_badge("potential" if potential else "duplicate")

        self._rebuild_filmstrip()
        if len(files) > 2 or self._current_layout_mode == self.LAYOUT_CAROUSEL:
            self.filmstrip_scroll.setVisible(True)
        else:
            self.filmstrip_scroll.setVisible(False)

        self._refresh_visible_viewers()

        if not self.meta_frame.isHidden():
            self._update_metadata_comparison()

    def unload_all(self):
        """Explicitly closes and releases all file locks across all viewers, filmstrip, curtain, and heatmap."""
        import gc
        for viewer in self.viewers:
            viewer.close_doc()
            viewer.set_role_badge("none")
        for combo in self.combos:
            combo.blockSignals(True)
            combo.clear()
            combo.blockSignals(False)
        self.current_files = []
        self._rebuild_filmstrip()
        self.filmstrip_scroll.setVisible(False)
        if hasattr(self, 'curtain_widget'):
            self.curtain_widget.set_pixmaps(None, None)
        if hasattr(self, 'heatmap_widget'):
            self.heatmap_widget.set_pixmaps(None, None)
        if not self.meta_frame.isHidden():
            self._update_metadata_comparison()
        gc.collect()

    def preview_single_file(self, file_info: FileInfo):
        """Previews a single file in the primary viewer."""
        self.viewers[0].load_file(file_info.path)
        self.viewers[0].set_role_badge("none")
        if not self.meta_frame.isHidden():
            self._update_metadata_comparison()

    def _on_pane_file_selected(self, pane_index: int, file_index: int):
        if 0 <= file_index < len(self.current_files):
            finfo = self.current_files[file_index]
            self.viewers[pane_index].load_file(finfo.path)
            self._highlight_active_cards()
            if not self.meta_frame.isHidden():
                self._update_metadata_comparison()

    def _on_left_selected(self, index: int):
        self._on_pane_file_selected(0, index)

    def _on_right_selected(self, index: int):
        self._on_pane_file_selected(1, index)

    def _on_sync_toggled(self, checked: bool):
        self._sync_paging = checked

    def _on_viewer_page_changed(self, origin_index: int, page_num: int):
        if not self._sync_paging:
            return
        for i, viewer in enumerate(self.viewers):
            if i != origin_index and not viewer.isHidden() and viewer.doc:
                if viewer.current_page != page_num:
                    viewer.go_to_page(page_num)

    def _on_viewer_zoom_changed(self, origin_index: int, zoom_val: float):
        if not self._sync_paging:
            return
        for i, viewer in enumerate(self.viewers):
            if i != origin_index and not viewer.isHidden():
                if abs(viewer.zoom_level - zoom_val) > 0.01:
                    viewer.zoom_level = zoom_val
                    viewer._pixmap_cache.clear()
                    viewer.render_current_page()

    def prev_page(self):
        """Flips back 1 page on visible viewers (propagates synchronously)."""
        for v in self.viewers:
            if not v.isHidden() and v.doc:
                v.prev_page()
                if self._sync_paging:
                    break

    def next_page(self):
        """Flips forward 1 page on visible viewers (propagates synchronously)."""
        for v in self.viewers:
            if not v.isHidden() and v.doc:
                v.next_page()
                if self._sync_paging:
                    break

    def zoom_in(self):
        """Zooms in on all visible viewers."""
        if self._current_layout_mode == self.LAYOUT_HEATMAP:
            self.heatmap_widget.zoom_in()
            return
        for v in self.viewers:
            if not v.isHidden():
                v.zoom_in()

    def zoom_out(self):
        """Zooms out on all visible viewers."""
        if self._current_layout_mode == self.LAYOUT_HEATMAP:
            self.heatmap_widget.zoom_out()
            return
        for v in self.viewers:
            if not v.isHidden():
                v.zoom_out()

    def reset_zoom(self):
        """Resets zoom to fit width/page on all visible viewers."""
        if self._current_layout_mode == self.LAYOUT_HEATMAP:
            self.heatmap_widget.reset_zoom()
            return
        for v in self.viewers:
            if not v.isHidden():
                v.fit_width()
