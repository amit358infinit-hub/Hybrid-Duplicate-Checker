"""
Main Window for Fast PDF Duplicate Finder.
All-file duplicate management suite.
Features:
- Czkawka exact hashing and explicit review for heuristic matches.
- Dark & Light Fluent UI Theme switcher.
- Live Statistics & Space Recovery Metrics.
- Smart Auto-Select Rules (Cleanest Name, Newest, Oldest, Shortest Path).
- Multiple Safe Actions: Move to '_deleted_pdfs', Windows Recycle Bin, Custom Folder.
- Export to Excel (.xlsx) and CSV reports.
- Integrated Side-by-Side Comparison with Metadata Inspector.
"""
from __future__ import annotations

import ctypes
import os
import shutil
import string
import subprocess
from concurrent.futures import ThreadPoolExecutor
import weakref
from typing import Dict, List, Optional, Tuple

from PySide6.QtCore import QEvent, Qt, QTimer, Signal, Slot
from PySide6.QtGui import QColor, QFont, QKeySequence, QShortcut
from PySide6.QtWidgets import (
    QApplication,
    QCheckBox,
    QComboBox,
    QDialog,
    QFileDialog,
    QFrame,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QMainWindow,
    QMenu,
    QMessageBox,
    QProgressBar,
    QPushButton,
    QScrollArea,
    QSplitter,
    QTreeWidget,
    QTreeWidgetItem,
    QVBoxLayout,
    QWidget,
)

from dialogs import CorruptFilesDialog, ExcludeFoldersDialog, RestoreDialog
from export_utils import export_to_csv, export_to_excel, export_to_html
from preview_panel import ComparePreviewPanel
from safe_trash import SafeTrashManager
from shell_utils import open_in_file_explorer
from scanner import (
    CATEGORY_ALL,
    CATEGORY_ARCHIVE,
    CATEGORY_AUDIO,
    CATEGORY_DOCS,
    CATEGORY_IMAGE,
    CATEGORY_PDF,
    CATEGORY_VIDEO,
    SCAN_MODE_EXACT_NAME,
    SCAN_MODE_FAST,
    SCAN_MODE_FULL_HASH,
    SCAN_MODE_PAGE_TITLE,
    SCAN_MODE_SAFE,
    SCAN_MODE_SIMILAR_NAME,
    SCAN_MODE_VISUAL_COVER,
    DuplicateGroup,
    FastScanWorker,
    CzkawkaScanWorker,
    FileInfo,
    get_name_similarity_ratio,
    is_heuristic_match,
)
from theme import apply_theme, THEME_COLORS
from category_picker import CategoryExtensionPicker
from shell_extension import install_context_menu, uninstall_context_menu, is_context_menu_installed
from action_worker import ActionFile, perform_verified_action


class MetricCard(QFrame):
    """Modern metric card widget for live statistics with theme-adaptive colors."""
    def __init__(self, title: str, initial_value: str = "0", color_key: str = "metric_files", color: str = "#38bdf8", parent=None):
        super().__init__(parent)
        self.setObjectName("MetricCard")
        self.setProperty("class", "MetricCard")
        self.color_key = color_key

        lay = QVBoxLayout(self)
        lay.setContentsMargins(14, 10, 14, 10)
        lay.setSpacing(3)

        self.lbl_title = QLabel(title)
        self.lbl_title.setObjectName("metric_title")
        self.lbl_title.setStyleSheet("font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;")
        lay.addWidget(self.lbl_title)

        self.lbl_value = QLabel(initial_value)
        self.lbl_value.setObjectName("metric_value")
        self.lbl_value.setStyleSheet(f"color: {color}; font-size: 20px; font-weight: 700;")
        lay.addWidget(self.lbl_value)

    def set_value(self, val: str):
        self.lbl_value.setText(val)

    def apply_theme_colors(self, theme_name: str):
        palette = THEME_COLORS.get(theme_name.lower(), THEME_COLORS["dark"])
        val_color = palette.get(self.color_key, palette["text_primary"])
        title_color = palette["text_secondary"]
        self.lbl_title.setStyleSheet(f"color: {title_color}; font-size: 11px; font-weight: 600; text-transform: uppercase; letter-spacing: 0.5px;")
        self.lbl_value.setStyleSheet(f"color: {val_color}; font-size: 20px; font-weight: 700;")


class LocationChip(QFrame):
    removed = Signal(str)
    set_master = Signal(str)

    def __init__(self, path: str, is_master: bool = False, current_theme: str = "dark", parent=None):
        super().__init__(parent)
        self.path = os.path.abspath(path)
        self.is_master = is_master
        self.current_theme = current_theme
        self.setObjectName("location_chip")
        self._init_ui()

    def _init_ui(self):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 3, 8, 3)
        lay.setSpacing(6)

        icon, dtype = self._get_drive_type_info(self.path)

        self.btn_master = QPushButton("⭐" if self.is_master else "☆")
        self.btn_master.setToolTip(
            "⭐ Master Location (Protected - Files here are prioritized to keep)"
            if self.is_master
            else "Click to set as Master / Protected location (always kept safe)"
        )
        self.btn_master.setFixedSize(22, 22)
        self.btn_master.setCursor(Qt.PointingHandCursor)
        self.btn_master.clicked.connect(lambda: self.set_master.emit(self.path))
        lay.addWidget(self.btn_master)

        name = os.path.basename(self.path)
        if not name:
            name = self.path
        self.lbl_name = QLabel(f"{icon}  {name}")
        self.lbl_name.setToolTip(f"{self.path} ({dtype})\n{'⭐ [MASTER / KEEP SAFE]' if self.is_master else '[COMPARED TARGET]'}")
        lay.addWidget(self.lbl_name)

        self.btn_remove = QPushButton("✕")
        self.btn_remove.setToolTip(f"Remove '{self.path}' from scan list")
        self.btn_remove.setFixedSize(18, 18)
        self.btn_remove.setCursor(Qt.PointingHandCursor)
        self.btn_remove.clicked.connect(lambda: self.removed.emit(self.path))
        lay.addWidget(self.btn_remove)

        self.update_style()

    def set_theme(self, theme_name: str):
        self.current_theme = theme_name
        self.update_style()

    def set_master_state(self, is_master: bool):
        self.is_master = is_master
        self.btn_master.setText("⭐" if self.is_master else "☆")
        self.update_style()

    def update_style(self):
        is_light = (self.current_theme.lower() == "light")
        if self.is_master:
            bg = "#eff6ff" if is_light else "#1e3a5f"
            border = "#2563eb" if is_light else "#38bdf8"
            text_col = "#1e3a8a" if is_light else "#f0f9ff"
            star_col = "#d97706" if is_light else "#fbbf24"
            x_col = "#dc2626" if is_light else "#f87171"
        else:
            bg = "#ffffff" if is_light else "#1e293b"
            border = "#cbd5e1" if is_light else "#475569"
            text_col = "#0f172a" if is_light else "#f8fafc"
            star_col = "#94a3b8" if is_light else "#64748b"
            x_col = "#64748b" if is_light else "#94a3b8"

        self.setStyleSheet(f"""
            QFrame#location_chip {{
                background-color: {bg};
                border: 1.5px solid {border};
                border-radius: 6px;
                padding: 1px 2px;
            }}
            QFrame#location_chip:hover {{
                border-color: #3b82f6;
            }}
        """)
        self.lbl_name.setStyleSheet(f"color: {text_col}; font-size: 12px; font-weight: 700; background: transparent; border: none;")
        self.btn_master.setStyleSheet(f"border: none; background: transparent; color: {star_col}; font-size: 13px; font-weight: bold;")
        self.btn_remove.setStyleSheet(f"""
            QPushButton {{
                border: none;
                background: transparent;
                color: {x_col};
                font-weight: bold;
                font-size: 12px;
            }}
            QPushButton:hover {{
                color: #ef4444;
            }}
        """)

    @staticmethod
    def _get_drive_type_info(path: str) -> Tuple[str, str]:
        try:
            drive_root = os.path.splitdrive(path)[0] + "\\"
            dtype_num = ctypes.windll.kernel32.GetDriveTypeW(drive_root)
            is_root = os.path.abspath(path).rstrip("\\/").upper() == drive_root.rstrip("\\/").upper()

            if dtype_num == 2:  # DRIVE_REMOVABLE
                return ("🔌", "USB / PenDrive")
            elif dtype_num == 3:  # DRIVE_FIXED
                return ("💾" if is_root else "📁", "Local Hard Drive" if is_root else "Folder")
            elif dtype_num == 4:  # DRIVE_REMOTE
                return ("🌐", "Network Share")
            elif dtype_num == 5:  # DRIVE_CDROM
                return ("💿", "CD/DVD Drive")
            else:
                return ("📁", "Folder")
        except Exception:
            return ("📁", "Folder")


class MainWindow(QMainWindow):
    _action_done = Signal(object)
    def __init__(self):
        super().__init__()
        self.setWindowTitle("All Files Duplicate Checker — Advanced Suite")
        self.resize(1360, 880)
        self.setMinimumSize(1020, 680)
        self.setAcceptDrops(True)

        self.current_theme = "dark"
        self.current_root_dirs: List[str] = []
        self.selected_locations: List[str] = []
        self.master_location: Optional[str] = None
        self.excluded_directories: Set[str] = set()
        self._location_chips: List[LocationChip] = []
        self.scan_worker: Optional[FastScanWorker] = None
        self._closing = False
        self._action_executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="VerifiedFileAction")
        self._action_future = None
        self._action_pending = False
        self._action_done.connect(self._on_action_done, Qt.QueuedConnection)
        self.duplicate_groups: List[DuplicateGroup] = []
        self._tree_group_map: Dict[QTreeWidgetItem, DuplicateGroup] = {}
        self._tree_file_map: Dict[QTreeWidgetItem, FileInfo] = {}
        self._corrupt_files: list = []

        # High-performance scale optimization attributes (Step 4 & 5)
        self._checked_count: int = 0
        self._checked_bytes: int = 0
        self._pending_render_groups: List[Tuple[int, DuplicateGroup]] = []
        self._render_chunk_size: int = 150
        self._render_in_progress: bool = False

        self._filter_timer = QTimer(self)
        self._filter_timer.setSingleShot(True)
        self._filter_timer.setInterval(150)
        self._filter_timer.timeout.connect(self._do_filter_tree)

        self._preview_debounce_timer = QTimer(self)
        self._preview_debounce_timer.setSingleShot(True)
        self._preview_debounce_timer.setInterval(50)
        self._preview_debounce_timer.timeout.connect(self._do_load_selected_preview)

        self._init_ui()
        apply_theme(self, self.current_theme)
        self.card_total_files.apply_theme_colors(self.current_theme)
        self.card_groups.apply_theme_colors(self.current_theme)
        self.card_copies.apply_theme_colors(self.current_theme)
        self.card_wasted.apply_theme_colors(self.current_theme)

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        dropped_folders = []
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path):
                dropped_folders.append(path)
            elif os.path.isfile(path):
                p_dir = os.path.dirname(path)
                if p_dir not in dropped_folders:
                    dropped_folders.append(p_dir)

        if dropped_folders:
            for df in dropped_folders:
                self._add_location(df)
            event.acceptProposedAction()

    def _init_ui(self):
        central = QWidget()
        self.setCentralWidget(central)
        root_lay = QVBoxLayout(central)
        root_lay.setContentsMargins(12, 12, 12, 12)
        root_lay.setSpacing(10)

        # -------------------------------------------------------------
        # 1. TOP HEADER & SCAN CONTROLS BAR
        # -------------------------------------------------------------
        top_box = QFrame()
        top_box.setObjectName("top_box")
        top_lay = QVBoxLayout(top_box)
        top_lay.setContentsMargins(10, 8, 10, 8)
        top_lay.setSpacing(8)

        # Row 1: Locations Chips Bar & Drive Manager
        row_locs = QHBoxLayout()
        row_locs.setSpacing(8)

        lbl_loc = QLabel("<b>Locations:</b>")
        lbl_loc.setStyleSheet("font-size: 12px;")
        row_locs.addWidget(lbl_loc)

        # Chips Scroll Area
        self.chips_scroll = QScrollArea()
        self.chips_scroll.setObjectName("chips_scroll")
        self.chips_scroll.setWidgetResizable(True)
        self.chips_scroll.setFixedHeight(44)
        self.chips_scroll.setVerticalScrollBarPolicy(Qt.ScrollBarAlwaysOff)
        self.chips_scroll.setHorizontalScrollBarPolicy(Qt.ScrollBarAsNeeded)

        self.chips_container = QWidget()
        self.chips_container.setObjectName("chips_container")
        self.chips_layout = QHBoxLayout(self.chips_container)
        self.chips_layout.setContentsMargins(4, 2, 4, 2)
        self.chips_layout.setSpacing(6)
        self.chips_layout.addStretch(1)

        self.lbl_no_locs = QLabel("No locations added. Click '+ Add Folder', '+ Add Drive' or Drag & Drop folders/drives here.")
        self.lbl_no_locs.setStyleSheet("color: #94a3b8; font-size: 11px; font-style: italic;")
        self.chips_layout.insertWidget(0, self.lbl_no_locs)

        self.chips_scroll.setWidget(self.chips_container)
        row_locs.addWidget(self.chips_scroll, stretch=1)

        # Hidden text entry for backward-compatibility with tests & CLI
        self.edit_folder = QLineEdit()
        self.edit_folder.setVisible(False)
        self.edit_folder.textChanged.connect(self._on_folder_changed)

        # Action Buttons for adding locations
        self.btn_add_folder = QPushButton("📁 + Add Folder...")
        self.btn_add_folder.setToolTip("Add any folder from internal or external drive to compare")
        self.btn_add_folder.clicked.connect(self._browse_add_folder)

        self.btn_add_drive = QPushButton("💾 + Add Drive / PenDrive ▼")
        self.btn_add_drive.setToolTip("Quickly select an entire internal disk, external hard drive, or USB PenDrive")
        self.btn_add_drive.clicked.connect(self._show_add_drive_menu)

        self.btn_clear_locs = QPushButton("🗑️ Clear")
        self.btn_clear_locs.setToolTip("Clear all selected locations")
        self.btn_clear_locs.clicked.connect(self._clear_all_locations)

        row_locs.addWidget(self.btn_add_folder)
        row_locs.addWidget(self.btn_add_drive)
        row_locs.addWidget(self.btn_clear_locs)
        top_lay.addLayout(row_locs)

        # Row 2: Controls, Filters & Scan Execution
        row_filters = QHBoxLayout()
        row_filters.setSpacing(8)

        self.btn_scan = QPushButton("⚡ Start Scan")
        self.btn_scan.setStyleSheet("""
            QPushButton {
                background-color: #2563eb;
                color: white;
                font-weight: bold;
                padding: 6px 18px;
                border-radius: 6px;
                font-size: 13px;
                border: none;
            }
            QPushButton:hover {
                background-color: #1d4ed8;
            }
            QPushButton:disabled {
                background-color: #64748b;
            }
        """)
        self.btn_scan.clicked.connect(self._start_scan)

        self.btn_cancel = QPushButton("⏹ Cancel")
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.clicked.connect(self._cancel_scan)

        # Interactive Granular Category & Extension Checklist Picker
        self.category_picker = CategoryExtensionPicker(theme=self.current_theme, parent=self)

        # Backwards-compatibility hidden combo
        self.combo_category = QComboBox()
        self.combo_category.setVisible(False)
        self.combo_category.addItem("📑 All Files (*.*)", CATEGORY_ALL)
        self.combo_category.addItem("📕 PDFs & eBooks", CATEGORY_PDF)
        self.combo_category.addItem("🖼️ Photos & Images", CATEGORY_IMAGE)
        self.combo_category.addItem("🎬 Videos & Movies", CATEGORY_VIDEO)
        self.combo_category.addItem("🎵 Audio & Music", CATEGORY_AUDIO)
        self.combo_category.addItem("📄 Office & Docs", CATEGORY_DOCS)
        self.combo_category.addItem("📦 Archives & Zip", CATEGORY_ARCHIVE)

        # Scan Mode Selector
        lbl_mode = QLabel("<b>Mode:</b>")
        self.combo_mode = QComboBox()
        self.combo_mode.addItem("🔒 Safe Match (Size + Hash)", SCAN_MODE_SAFE)
        self.combo_mode.addItem("🔤 Exact Name (समान नाम)", SCAN_MODE_EXACT_NAME)
        self.combo_mode.addItem("📝 Similar Name (लगभग समान नाम)", SCAN_MODE_SIMILAR_NAME)
        self.combo_mode.addItem("📖 Same Page Count + Title (PDFs/E-books)", SCAN_MODE_PAGE_TITLE)
        self.combo_mode.addItem("🖼️ Visual Cover Match (dHash)", SCAN_MODE_VISUAL_COVER)
        self.combo_mode.addItem("⚡ Ultra-Fast (Size Only)", SCAN_MODE_FAST)
        self.combo_mode.addItem("🛡️ Full SHA-256", SCAN_MODE_FULL_HASH)

        # Min Size Filter
        lbl_min_size = QLabel("<b>Min Size:</b>")
        self.combo_min_size = QComboBox()
        self.combo_min_size.addItem("All Sizes", 0)
        self.combo_min_size.addItem("> 100 KB", 100 * 1024)
        self.combo_min_size.addItem("> 500 KB", 500 * 1024)
        self.combo_min_size.addItem("> 1 MB", 1024 * 1024)
        self.combo_min_size.addItem("> 5 MB", 5 * 1024 * 1024)

        # 0-Byte Empty Files Filter Checkbox
        self.chk_empty_files = QCheckBox("Include 0-Byte")
        self.chk_empty_files.setToolTip("Include empty 0-byte or corrupted files in scan results")

        # Exclude Folders Button
        self.btn_exclude = QPushButton("🚫 Exclude (0)")
        self.btn_exclude.setToolTip("Configure directories or folders to ignore during scan")
        self.btn_exclude.clicked.connect(self._open_exclude_dialog)

        # Drive / Thread Speed Optimization
        self.combo_speed = QComboBox()
        self.combo_speed.addItem("⚡ Auto Drive", 0)
        self.combo_speed.addItem("🚀 Turbo SSD (16T)", 16)
        self.combo_speed.addItem("💾 Safe HDD (2T)", 2)
        self.combo_speed.setToolTip("Multithread disk concurrency:\n• Auto Drive: Intelligently detects SSD vs mechanical HDD\n• Turbo SSD: 16 parallel threads for maximum NVMe/SSD speed\n• Safe HDD: 2 threads to prevent mechanical disk head thrashing")

        # Windows Explorer Right-Click Shell Menu Integration
        self.btn_context_menu = QPushButton("🪟 Shell Menu")
        self.btn_context_menu.setToolTip("Configure Windows Explorer Right-Click Context Menu integration")
        self.btn_context_menu.clicked.connect(self._toggle_context_menu)
        self._update_context_menu_button_state()

        # Theme Toggle Button
        self.btn_theme = QPushButton("☀️ Light" if self.current_theme == "dark" else "🌙 Dark")
        self.btn_theme.setToolTip("Toggle Dark / Light Mode")
        self.btn_theme.clicked.connect(self._toggle_theme)

        row_filters.addWidget(self.btn_scan)
        row_filters.addWidget(self.btn_cancel)
        row_filters.addWidget(self.category_picker)
        row_filters.addWidget(lbl_mode)
        row_filters.addWidget(self.combo_mode)
        row_filters.addWidget(lbl_min_size)
        row_filters.addWidget(self.combo_min_size)
        row_filters.addWidget(self.chk_empty_files)
        row_filters.addWidget(self.combo_speed)
        row_filters.addWidget(self.btn_exclude)
        row_filters.addWidget(self.btn_context_menu)
        row_filters.addWidget(self.btn_theme)
        top_lay.addLayout(row_filters)

        # Row 3: Live Status & Progress
        row_status = QHBoxLayout()
        self.lbl_status = QLabel("Ready. Select or drag & drop folder(s) or drive(s) and click Start Scan.")
        self.lbl_status.setStyleSheet("font-size: 12px;")
        row_status.addWidget(self.lbl_status, stretch=1)
        top_lay.addLayout(row_status)

        root_lay.addWidget(top_box)

        # -------------------------------------------------------------
        # 2. METRICS CARDS & PROGRESS BAR
        # -------------------------------------------------------------
        stats_row = QHBoxLayout()
        stats_row.setSpacing(10)

        self.card_total_files = MetricCard("Total Files Scanned", "0", color_key="metric_files")
        self.card_total_pdfs = self.card_total_files  # Backwards compatibility
        self.card_groups = MetricCard("Duplicate Groups", "0", color_key="metric_groups")
        self.card_copies = MetricCard("Redundant Copies", "0", color_key="metric_redundant")
        self.card_wasted = MetricCard("Potential Space Savings", "0 MB", color_key="metric_savings")

        stats_row.addWidget(self.card_total_files)
        stats_row.addWidget(self.card_groups)
        stats_row.addWidget(self.card_copies)
        stats_row.addWidget(self.card_wasted)
        root_lay.addLayout(stats_row)

        self.progress_bar = QProgressBar()
        self.progress_bar.setFixedHeight(20)
        self.progress_bar.setTextVisible(True)
        self.progress_bar.setVisible(False)
        root_lay.addWidget(self.progress_bar)

        # -------------------------------------------------------------
        # 3. MAIN SPLITTER (LEFT: RESULTS & ACTIONS, RIGHT: PREVIEW)
        # -------------------------------------------------------------
        main_splitter = QSplitter(Qt.Horizontal)
        main_splitter.setChildrenCollapsible(False)

        # Left Container
        left_container = QWidget()
        left_lay = QVBoxLayout(left_container)
        left_lay.setContentsMargins(0, 0, 0, 0)
        left_lay.setSpacing(6)

        # Search / Filter Bar
        filter_bar = QHBoxLayout()
        self.edit_filter = QLineEdit()
        self.edit_filter.setPlaceholderText("🔍 Filter duplicate groups by filename or directory...")
        self.edit_filter.setToolTip("Filter duplicate groups by filename or directory (Shortcut: Ctrl+F)")
        self.edit_filter.textChanged.connect(self._filter_timer.start)
        filter_bar.addWidget(self.edit_filter, stretch=1)

        btn_expand = QPushButton("Expand")
        btn_expand.clicked.connect(lambda: self.tree.expandAll())
        btn_collapse = QPushButton("Collapse")
        btn_collapse.clicked.connect(lambda: self.tree.collapseAll())
        filter_bar.addWidget(btn_expand)
        filter_bar.addWidget(btn_collapse)
        left_lay.addLayout(filter_bar)

        # Smart Auto-Select Toolbar
        rule_bar = QHBoxLayout()
        rule_bar.setSpacing(6)
        rule_bar.addWidget(QLabel("<b>Auto-Select:</b>"))

        self.combo_rule = QComboBox()
        self.combo_rule.addItem("Keep in Master Folder (Primary Folder)", "master_folder")
        self.combo_rule.addItem("Keep Cleanest Name (Removes copy markers)", "cleanest_name")
        self.combo_rule.addItem("Keep Largest File (Best Quality)", "largest_file")
        self.combo_rule.addItem("Keep Smallest File (Saves Space)", "smallest_file")
        self.combo_rule.addItem("Keep Newest (Mark older copies to delete)", "newest")
        self.combo_rule.addItem("Keep Oldest (Mark newer copies to delete)", "oldest")
        self.combo_rule.addItem("Keep Shortest Path", "shortest_path")
        self.combo_rule.addItem("Keep Longest Path", "longest_path")
        self.combo_rule.addItem("Keep First Found", "first")
        rule_bar.addWidget(self.combo_rule, stretch=1)

        btn_apply_rule = QPushButton("Apply Rule")
        btn_apply_rule.clicked.connect(self._apply_selection_rule)
        rule_bar.addWidget(btn_apply_rule)

        btn_invert = QPushButton("Invert")
        btn_invert.setToolTip("Invert selection of duplicate copies across all groups")
        btn_invert.clicked.connect(self._invert_selection)
        rule_bar.addWidget(btn_invert)

        btn_uncheck_all = QPushButton("Uncheck All")
        btn_uncheck_all.clicked.connect(self._uncheck_all)
        rule_bar.addWidget(btn_uncheck_all)
        left_lay.addLayout(rule_bar)

        # Duplicate Tree Widget
        self.tree = QTreeWidget()
        self.tree.setColumnCount(4)
        self.tree.setHeaderLabels(["Item / Filename", "Match & Size", "Date Modified", "Location"])
        self.tree.header().setSectionResizeMode(0, QHeaderView.Stretch)
        self.tree.header().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(2, QHeaderView.ResizeToContents)
        self.tree.header().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.tree.itemSelectionChanged.connect(self._on_tree_selection_changed)
        self.tree.itemChanged.connect(self._on_item_checked)
        self.tree.setContextMenuPolicy(Qt.CustomContextMenu)
        self.tree.customContextMenuRequested.connect(self._show_context_menu)
        left_lay.addWidget(self.tree, stretch=1)

        # Bottom Actions Bar (Consolidated Action Center)
        actions_bar = QHBoxLayout()
        actions_bar.setSpacing(8)

        # 1. Primary Action: Move to Safe Trash
        self.btn_safe_delete = QPushButton("🗑️ Move Checked to Safe Trash")
        self.btn_safe_delete.setToolTip("Safely move checked duplicates to local '_deleted_files' (Shortcut: Delete)")
        self.btn_safe_delete.setStyleSheet("""
            QPushButton {
                background-color: #dc2626;
                color: white;
                font-weight: bold;
                padding: 7px 16px;
                border-radius: 6px;
                font-size: 12px;
                border: none;
            }
            QPushButton:hover {
                background-color: #b91c1c;
            }
            QPushButton:disabled {
                background-color: #64748b;
                color: #cbd5e1;
            }
        """)
        self.btn_safe_delete.setEnabled(False)
        self.btn_safe_delete.clicked.connect(self._confirm_safe_delete)

        # 2. More Actions Dropdown Menu
        self.btn_more_actions = QPushButton("⚡ More Actions ▼")
        self.btn_more_actions.setToolTip("Additional deduplication options: Recycle Bin, Hardlink, Custom Archive, Clean Folders")
        self.btn_more_actions.setEnabled(False)
        self.btn_more_actions.clicked.connect(self._show_more_actions_menu)

        # 3. Corrupt / 0-Byte Alert Button
        self.btn_corrupt = QPushButton("⚠️ Corrupt/0-Byte (0)")
        self.btn_corrupt.setStyleSheet("""
            QPushButton {
                background-color: #dc2626;
                color: white;
                font-weight: bold;
                padding: 7px 12px;
                border-radius: 6px;
                font-size: 12px;
                border: none;
            }
            QPushButton:hover {
                background-color: #b91c1c;
            }
        """)
        self.btn_corrupt.setVisible(False)
        self.btn_corrupt.clicked.connect(self._open_corrupt_files_dialog)

        # 4. Restore Manager Button
        self.btn_restore = QPushButton("🔄 Restore / Undo...")
        self.btn_restore.setStyleSheet("""
            QPushButton {
                background-color: #059669;
                color: white;
                font-weight: bold;
                padding: 7px 14px;
                border-radius: 6px;
                font-size: 12px;
                border: none;
            }
            QPushButton:hover {
                background-color: #047857;
            }
        """)
        self.btn_restore.clicked.connect(self._open_restore_dialog)

        # 5. Export Report Button
        self.btn_export = QPushButton("📊 Export Report ▼")
        self.btn_export.clicked.connect(self._show_export_menu)

        actions_bar.addWidget(self.btn_safe_delete, stretch=2)
        actions_bar.addWidget(self.btn_more_actions, stretch=1)
        actions_bar.addWidget(self.btn_corrupt, stretch=1)
        actions_bar.addWidget(self.btn_restore, stretch=1)
        actions_bar.addWidget(self.btn_export, stretch=1)
        left_lay.addLayout(actions_bar)

        # Right Container (Preview & Comparison Panel)
        self.preview_panel = ComparePreviewPanel()

        main_splitter.addWidget(left_container)
        main_splitter.addWidget(self.preview_panel)
        main_splitter.setSizes([640, 720])

        root_lay.addWidget(main_splitter, stretch=1)

        # Power-User Keyboard Navigation & Shortcuts (Phase 5)
        self.tree.installEventFilter(self)
        self._setup_shortcuts()

    def _setup_shortcuts(self):
        """Registers pro keyboard shortcuts for rapid audit & deduplication."""
        # 1. Ctrl+F: Focus live search filter
        self.sc_find = QShortcut(QKeySequence("Ctrl+F"), self)
        self.sc_find.activated.connect(self._focus_search)
        self.sc_find2 = QShortcut(QKeySequence.Find, self)
        self.sc_find2.activated.connect(self._focus_search)

        # 2. Delete: Safe Trash move
        self.sc_del = QShortcut(QKeySequence.Delete, self)
        self.sc_del.activated.connect(self._on_delete_shortcut)

        # 3. Zoom Shortcuts
        self.sc_zoom_in = QShortcut(QKeySequence.ZoomIn, self)
        self.sc_zoom_in.activated.connect(self.preview_panel.zoom_in)
        self.sc_zoom_in2 = QShortcut(QKeySequence(Qt.CTRL | Qt.Key_Equal), self)
        self.sc_zoom_in2.activated.connect(self.preview_panel.zoom_in)

        self.sc_zoom_out = QShortcut(QKeySequence.ZoomOut, self)
        self.sc_zoom_out.activated.connect(self.preview_panel.zoom_out)

        self.sc_zoom_reset = QShortcut(QKeySequence(Qt.CTRL | Qt.Key_0), self)
        self.sc_zoom_reset.activated.connect(self.preview_panel.reset_zoom)

        # 4. Synchronized Page Flipping
        self.sc_prev_pg = QShortcut(QKeySequence(Qt.ALT | Qt.Key_Left), self)
        self.sc_prev_pg.activated.connect(self.preview_panel.prev_page)
        self.sc_prev_pg2 = QShortcut(QKeySequence(Qt.Key_PageUp), self)
        self.sc_prev_pg2.activated.connect(self.preview_panel.prev_page)
        self.sc_prev_pg3 = QShortcut(QKeySequence(Qt.Key_BracketLeft), self)
        self.sc_prev_pg3.activated.connect(self.preview_panel.prev_page)

        self.sc_next_pg = QShortcut(QKeySequence(Qt.ALT | Qt.Key_Right), self)
        self.sc_next_pg.activated.connect(self.preview_panel.next_page)
        self.sc_next_pg2 = QShortcut(QKeySequence(Qt.Key_PageDown), self)
        self.sc_next_pg2.activated.connect(self.preview_panel.next_page)
        self.sc_next_pg3 = QShortcut(QKeySequence(Qt.Key_BracketRight), self)
        self.sc_next_pg3.activated.connect(self.preview_panel.next_page)

        # 5. F5: Refresh / Re-scan current target folders
        self.sc_refresh = QShortcut(QKeySequence.Refresh, self)
        self.sc_refresh.activated.connect(self._shortcut_refresh_scan)
        self.sc_refresh2 = QShortcut(QKeySequence(Qt.Key_F5), self)
        self.sc_refresh2.activated.connect(self._shortcut_refresh_scan)

    def _shortcut_refresh_scan(self):
        """Triggers scan refresh when F5 is pressed."""
        if hasattr(self, "btn_scan") and self.btn_scan.isEnabled():
            self._start_scan()

    def _focus_search(self):
        """Focuses and highlights search query box."""
        self.edit_filter.setFocus()
        self.edit_filter.selectAll()

    def _on_delete_shortcut(self):
        """Triggers safe trash deletion if not typing inside a text box."""
        focus = QApplication.focusWidget()
        if isinstance(focus, QLineEdit):
            return
        if self.btn_safe_delete.isEnabled():
            self._confirm_safe_delete()

    def eventFilter(self, watched, event):
        """Custom event filter for clean Space key checkbox toggling on the tree."""
        if watched == self.tree and event.type() == QEvent.KeyPress:
            if event.key() == Qt.Key_Space:
                selected = self.tree.selectedItems()
                if selected:
                    item = selected[0]
                    new_st = Qt.Unchecked if item.checkState(0) == Qt.Checked else Qt.Checked
                    item.setCheckState(0, new_st)
                    return True
        return super().eventFilter(watched, event)

    def _toggle_theme(self):
        if self.current_theme == "dark":
            self.current_theme = "light"
            self.btn_theme.setText("🌙 Dark")
        else:
            self.current_theme = "dark"
            self.btn_theme.setText("☀️ Light")
        apply_theme(self, self.current_theme)

        self.card_total_files.apply_theme_colors(self.current_theme)
        self.card_groups.apply_theme_colors(self.current_theme)
        self.card_copies.apply_theme_colors(self.current_theme)
        self.card_wasted.apply_theme_colors(self.current_theme)
        for chip in self._location_chips:
            chip.set_theme(self.current_theme)
        self.category_picker.apply_theme(self.current_theme)
        self._refresh_tree_item_colors()

    def _get_tree_status_colors(self) -> Tuple[QColor, QColor]:
        palette = THEME_COLORS.get(self.current_theme.lower(), THEME_COLORS["dark"])
        return QColor(palette["keep_color"]), QColor(palette["delete_color"])

    def get_keep_color(self) -> QColor:
        return self._get_tree_status_colors()[0]

    def get_delete_color(self) -> QColor:
        return self._get_tree_status_colors()[1]

    def _refresh_tree_item_colors(self):
        keep_col, del_col = self._get_tree_status_colors()
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            for c in range(group_item.childCount()):
                child = group_item.child(c)
                text0 = child.text(0)
                if self._is_reference_item(child):
                    child.setForeground(0, keep_col)
                else:
                    child.setForeground(0, del_col)

    def _browse_add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Compare", "")
        if folder:
            self._add_location(folder)

    def _browse_folder(self):
        """Compatibility alias for _browse_add_folder."""
        self._browse_add_folder()

    def _show_add_drive_menu(self):
        drives = self._get_available_drives()
        if not drives:
            QMessageBox.information(self, "No Drives", "No active disk drives detected.")
            return

        menu = QMenu(self)
        for d_path, d_type, d_free in drives:
            label = f"{d_type}: {d_path} ({d_free})" if d_free else f"{d_type}: {d_path}"
            action = menu.addAction(label)
            action.triggered.connect(lambda checked=False, p=d_path: self._add_location(p))

        menu.exec(self.btn_add_drive.mapToGlobal(self.btn_add_drive.rect().bottomLeft()))

    @staticmethod
    def _get_available_drives() -> List[Tuple[str, str, str]]:
        """Scans and returns all available Windows drives with type description and free space."""
        results = []
        try:
            GetDriveTypeW = ctypes.windll.kernel32.GetDriveTypeW
            for letter in string.ascii_uppercase:
                d_path = f"{letter}:\\"
                if os.path.exists(d_path):
                    dtype = GetDriveTypeW(d_path)
                    if dtype == 2:
                        type_str = "🔌 USB PenDrive"
                    elif dtype == 3:
                        type_str = "💾 Local Fixed Drive"
                    elif dtype == 4:
                        type_str = "🌐 Network Drive"
                    elif dtype == 5:
                        type_str = "💿 CD/DVD Drive"
                    else:
                        type_str = "📁 Drive"

                    try:
                        _, _, free_b = shutil.disk_usage(d_path)
                        free_str = f"{free_b / (1024**3):.1f} GB free"
                    except Exception:
                        free_str = ""

                    results.append((d_path, type_str, free_str))
        except Exception:
            pass
        return results

    def _open_exclude_dialog(self):
        dlg = ExcludeFoldersDialog(self.excluded_directories, self)
        res = dlg.exec()
        if res == QDialog.Accepted or res == 1:
            self.excluded_directories = set(dlg.excludes)
            self.btn_exclude.setText(f"🚫 Exclude ({len(self.excluded_directories)})")

    def _toggle_context_menu(self):
        installed = is_context_menu_installed()
        if installed:
            ans = QMessageBox.question(
                self,
                "Windows Context Menu",
                "Windows Right-Click Menu is currently ACTIVE.\n\n"
                "Would you like to UNINSTALL it from Windows Explorer?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No
            )
            if ans == QMessageBox.Yes:
                succ, msg = uninstall_context_menu()
                if succ:
                    QMessageBox.information(self, "Context Menu", msg)
                else:
                    QMessageBox.warning(self, "Error", msg)
        else:
            ans = QMessageBox.question(
                self,
                "Windows Context Menu",
                "Would you like to install the Windows Explorer Right-Click Menu?\n\n"
                "This allows you to right-click any folder or drive in Windows Explorer and select 'Scan for Duplicates with Duplicate Checker'.",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if ans == QMessageBox.Yes:
                succ, msg = install_context_menu()
                if succ:
                    QMessageBox.information(self, "Context Menu", msg)
                else:
                    QMessageBox.warning(self, "Error", msg)
        self._update_context_menu_button_state()

    def _update_context_menu_button_state(self):
        if not hasattr(self, "btn_context_menu"):
            return
        installed = is_context_menu_installed()
        if installed:
            self.btn_context_menu.setText("🪟 Shell Menu (Active)")
            self.btn_context_menu.setStyleSheet("color: #34d399; font-weight: 600;")
        else:
            self.btn_context_menu.setText("🪟 Shell Menu")
            self.btn_context_menu.setStyleSheet("")

    def _add_location(self, path: str):
        path = os.path.abspath(path.strip())
        if not os.path.isdir(path):
            return
        if path not in self.selected_locations:
            self.selected_locations.append(path)
            if not self.master_location:
                self.master_location = path
            self._sync_locations_state()

    def _remove_location(self, path: str):
        path = os.path.abspath(path.strip())
        if path in self.selected_locations:
            self.selected_locations.remove(path)
            if self.master_location == path:
                self.master_location = self.selected_locations[0] if self.selected_locations else None
            self._sync_locations_state()

    def _set_master_location(self, path: str):
        path = os.path.abspath(path.strip())
        if path in self.selected_locations:
            self.master_location = path
            # Reorder so master is first in current_root_dirs
            self.selected_locations.remove(path)
            self.selected_locations.insert(0, path)
            self._sync_locations_state()

    def _clear_all_locations(self):
        self.selected_locations.clear()
        self.master_location = None
        self._sync_locations_state()

    def _sync_locations_state(self):
        self.current_root_dirs = list(self.selected_locations)
        self.edit_folder.blockSignals(True)
        self.edit_folder.setText(";".join(self.selected_locations))
        self.edit_folder.blockSignals(False)
        self._refresh_location_chips()

    def _refresh_location_chips(self):
        for chip in self._location_chips:
            chip.setParent(None)
        self._location_chips.clear()

        if not self.selected_locations:
            self.lbl_no_locs.setVisible(True)
        else:
            self.lbl_no_locs.setVisible(False)
            for idx, loc in enumerate(self.selected_locations):
                is_master = (loc == self.master_location) or (idx == 0 and not self.master_location)
                chip = LocationChip(loc, is_master=is_master, current_theme=self.current_theme, parent=self.chips_container)
                chip.removed.connect(self._remove_location)
                chip.set_master.connect(self._set_master_location)
                self.chips_layout.insertWidget(idx, chip)
                self._location_chips.append(chip)

    def _on_folder_changed(self, text: str):
        paths = [os.path.abspath(p.strip()) for p in text.split(";") if p.strip() and os.path.isdir(p.strip())]
        self.selected_locations = paths
        if paths and self.master_location not in paths:
            self.master_location = paths[0]
        self.current_root_dirs = list(self.selected_locations)
        self._refresh_location_chips()

    def closeEvent(self, event):
        self._closing = True
        if self.scan_worker and self.scan_worker.isRunning():
            self.scan_worker.cancel()
            event.ignore()
            QTimer.singleShot(50, self.close)
            return
        if self._action_pending:
            event.ignore()
            QTimer.singleShot(50, self.close)
            return
        self.preview_panel.close()
        self._action_executor.shutdown(wait=False)
        super().closeEvent(event)

    def _start_scan(self):
        if self._closing or (self.scan_worker and self.scan_worker.isRunning()) or (self._action_future and not self._action_future.done()):
            return
        if not self.current_root_dirs:
            # Check if text in edit_folder has something
            paths = [os.path.abspath(p.strip()) for p in self.edit_folder.text().split(";") if p.strip() and os.path.isdir(p.strip())]
            if paths:
                for p in paths:
                    self._add_location(p)
            else:
                QMessageBox.warning(self, "No Locations", "Please click '+ Add Folder' or '+ Add Drive' to select at least one location to scan.")
                return

        paths = list(self.current_root_dirs)
        self.btn_scan.setEnabled(False)
        self.btn_cancel.setEnabled(True)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat("Stage 1: Scanning directories & discovering files...")

        mode = self.combo_mode.currentData() or SCAN_MODE_SAFE
        min_size = self.combo_min_size.currentData() or 0
        custom_exts = self.category_picker.get_selected_extensions()
        if custom_exts == set():
            self._ensure_cancelled_ui_reset()
            self.lbl_status.setText("Select at least one file extension.")
            return
        mode_desc = self.combo_mode.currentText()
        _, cat_desc, _ = self.category_picker.filter_state.get_summary_display()
        self.lbl_status.setText(f"Scanning {cat_desc} ({mode_desc})...")

        # Clear existing & reset metric cards
        self._render_in_progress = False
        self._pending_render_groups = []
        self.tree.clear()
        self._tree_group_map.clear()
        self._tree_file_map.clear()
        self.duplicate_groups.clear()
        import gc
        self.preview_panel.unload_all()
        gc.collect()
        self._corrupt_files.clear()
        self.btn_corrupt.setVisible(False)
        self.card_total_files.set_value("0")
        self.card_groups.set_value("0")
        self.card_copies.set_value("0")
        self.card_wasted.set_value("0 B")

        concurrency_val = self.combo_speed.currentData() or None

        # Launch background worker with multithreading, category filter & caching
        worker_class = CzkawkaScanWorker if mode == SCAN_MODE_SAFE else FastScanWorker
        self.scan_worker = worker_class(
            root_path=self.current_root_dirs,
            scan_mode=mode,
            category=CATEGORY_ALL,
            custom_extensions=custom_exts,
            min_size_bytes=min_size,
            exclude_dirs=self.excluded_directories,
            use_cache=True,
            include_empty_files=self.chk_empty_files.isChecked(),
            concurrency=concurrency_val,
            parent=self,
            pdf_pool=self.preview_panel._get_pdf_pool() if mode in (SCAN_MODE_PAGE_TITLE, SCAN_MODE_VISUAL_COVER) else None
        )
        worker = self.scan_worker
        for signal, handler in ((worker.progress, self._on_scan_progress), (worker.stage2_progress, self._on_stage2_progress),
                                (worker.scan_finished, self._on_scan_finished), (worker.corrupt_files_found, self._on_corrupt_files_found),
                                (worker.scan_cancelled, self._on_scan_cancelled), (worker.scan_error, self._on_scan_error)):
            signal.connect(lambda *args, w=worker, h=handler: self._scan_event(w, h, args), Qt.QueuedConnection)
        if isinstance(worker, CzkawkaScanWorker):
            worker.cli_progress.connect(lambda progress, w=worker: self._scan_event(w, self._on_cli_progress, (progress,)), Qt.QueuedConnection)
        self.scan_worker.start()

    def _scan_event(self, worker, handler, args):
        if self.scan_worker is worker and not self._closing:
            if worker._is_cancelled and handler not in (self._on_scan_cancelled, self._on_scan_error):
                return
            handler(*args)

    def _on_cli_progress(self, progress):
        if self.scan_worker and self.scan_worker._is_cancelled:
            return
        labels = {"running": "Czkawka: scanning directories / BLAKE3 verification",
                  "traversal": "Scanning directories", "prehash": "Computing pre-hashes",
                  "full_hash": "BLAKE3 hashing", "completed": "Loading verified results"}
        self.progress_bar.setRange(0, 0)
        self.progress_bar.setFormat(labels.get(progress.phase, progress.message))
        self.lbl_status.setText(labels.get(progress.phase, progress.message))

    def _cancel_scan(self):
        self._render_in_progress = False
        self._pending_render_groups.clear()
        if self.scan_worker:
            self.scan_worker.cancel()
        if self.scan_worker and self.scan_worker.isRunning():
            self.lbl_status.setText("⏹️ Cancelling scan immediately...")
            self.btn_cancel.setEnabled(False)
            self.btn_cancel.setText("Cancelling...")
            # Disconnect progress signals so GUI does not process any backlog of updates
            try:
                self.scan_worker.progress.disconnect()
            except Exception:
                pass
            try:
                self.scan_worker.stage2_progress.disconnect()
            except Exception:
                pass
            # Fast responsive fallback: ensure UI buttons reset without freeze
            QTimer.singleShot(250, self._ensure_cancelled_ui_reset)
        else:
            self._ensure_cancelled_ui_reset()
            self.lbl_status.setText("Scan cancelled. Ready for new scan.")

    def _ensure_cancelled_ui_reset(self):
        if self.scan_worker and self.scan_worker.isRunning():
            QTimer.singleShot(50, self._ensure_cancelled_ui_reset)
            return
        self._render_in_progress = False
        self._pending_render_groups.clear()
        self.btn_scan.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("⏹ Cancel")
        self.progress_bar.setVisible(False)
        if "Cancelling" in self.lbl_status.text():
            self.lbl_status.setText("⏹️ Scan cancelled. Ready for new scan.")

    def _on_scan_cancelled(self, files_scanned: int, elapsed: float):
        self._render_in_progress = False
        self._pending_render_groups.clear()
        self.btn_scan.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.btn_cancel.setText("⏹ Cancel")
        self.progress_bar.setVisible(False)
        self.lbl_status.setText(
            f"⏹️ Scan cancelled ({files_scanned:,} files examined in {elapsed:.1f}s). Ready for new scan."
        )

    def _on_scan_progress(self, file_count: int, current_dir: str, dup_groups: int):
        self.card_total_files.set_value(f"{file_count:,}")
        self.card_groups.set_value(f"{dup_groups:,}")
        folder_display = os.path.basename(current_dir) or current_dir
        self.progress_bar.setFormat(f"Stage 1: Discovered {file_count:,} files ({dup_groups:,} duplicate candidates)")
        self.lbl_status.setText(f"Stage 1: Scanning... {file_count:,} files examined | {dup_groups:,} duplicate groups | {folder_display}")

    def _on_stage2_progress(self, current: int, total: int, filename: str, speed_mb: float):
        if total > 0:
            self.progress_bar.setRange(0, total)
            self.progress_bar.setValue(current)
            pct = int((current / total) * 100)
            fname = os.path.basename(filename)
            if speed_mb > 0:
                self.progress_bar.setFormat(f"%p% | Multi-core SHA-256: {current:,} / {total:,} ({speed_mb:.1f} MB/s)")
                self.lbl_status.setText(f"Stage 2: 🔒 Multi-core SHA-256 ({current:,}/{total:,}) • {pct}% • {speed_mb:.1f} MB/s • {fname}")
            elif "Grouping" in filename or "Matching" in filename:
                self.progress_bar.setFormat(f"%p% | {filename}: {current:,} / {total:,}")
                self.lbl_status.setText(f"Stage 2: 🔤 {filename}: {current:,} / {total:,} ({pct}%)")
            else:
                self.progress_bar.setFormat(f"%p% | Verifying duplicate hashes: {current:,} / {total:,}")
                self.lbl_status.setText(f"Stage 2: ⚡ Verifying duplicate hashes: {current:,} / {total:,} ({pct}%) • {fname}")

    def _on_scan_error(self, err: str):
        self.btn_scan.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.lbl_status.setText(f"Scan error: {err}")
        QMessageBox.critical(self, "Scan Error", err)

    @staticmethod
    def _format_bytes(s: int) -> str:
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if s < 1024.0:
                return f"{s:.2f} {unit}" if unit != 'B' else f"{s} Bytes"
            s /= 1024.0
        return f"{s:.2f} PB"

    def recalculate_metrics(self):
        """Recalculates duplicate groups, redundant copies, and potential space savings dynamically."""
        total_dup_copies = sum(max(0, len(g.files) - 1) for g in self.duplicate_groups)
        total_wasted_bytes = sum(g.wasted_bytes for g in self.duplicate_groups)

        self.card_groups.set_value(f"{len(self.duplicate_groups):,}")
        self.card_copies.set_value(f"{total_dup_copies:,}")
        self.card_wasted.set_value(self._format_bytes(total_wasted_bytes))
        if any(is_heuristic_match(g.match_type) for g in self.duplicate_groups):
            self.card_copies.lbl_title.setText("Potential Matches")
            self.card_wasted.lbl_title.setText("Manual Review Required")
            self.card_wasted.set_value("Unverified")
        else:
            self.card_copies.lbl_title.setText("Redundant Copies")
            self.card_wasted.lbl_title.setText("Potential Space Savings")

    @staticmethod
    def _is_reference_item(item):
        return any(marker in item.text(0) for marker in ("[KEEP]", "[REFERENCE]"))

    @staticmethod
    def _file_role_text(group, info, reference):
        if group and is_heuristic_match(group.match_type):
            role = "[REFERENCE]" if reference else "POTENTIAL MATCH (Manual Review Required)"
            return f"{role} {info.filename}"
        return f"⭐ [KEEP] {info.filename}" if reference else f"🗑️ {info.filename}"

    @staticmethod
    def _group_space_text(group):
        return "POTENTIAL MATCH (Manual Review Required)" if is_heuristic_match(group.match_type) else f"Wasted: {group.formatted_wasted}"

    def _results_summary(self):
        if any(is_heuristic_match(g.match_type) for g in self.duplicate_groups):
            return f"{len(self.duplicate_groups):,} candidate groups. POTENTIAL MATCH (Manual Review Required)."
        copies = sum(max(0,len(g.files)-1) for g in self.duplicate_groups)
        return f"Found {len(self.duplicate_groups):,} duplicate groups ({copies:,} redundant copies)."

    def _on_corrupt_files_found(self, corrupt_list: list):
        self._corrupt_files = list(corrupt_list)
        if self._corrupt_files:
            self.btn_corrupt.setText(f"⚠️ Corrupt/0-Byte ({len(self._corrupt_files)})")
            self.btn_corrupt.setVisible(True)
        else:
            self.btn_corrupt.setVisible(False)

    def _on_scan_finished(self, groups: List[DuplicateGroup], total_files: int, total_pdfs: int, elapsed: float):
        if self.scan_worker and self.scan_worker._is_cancelled:
            return  # Drop finished event if user requested cancellation

        self.btn_scan.setEnabled(True)
        self.btn_cancel.setEnabled(False)
        self.progress_bar.setVisible(False)
        self.duplicate_groups = groups

        if self._corrupt_files:
            self.btn_corrupt.setText(f"⚠️ Corrupt/0-Byte ({len(self._corrupt_files)})")
            self.btn_corrupt.setVisible(True)
        else:
            self.btn_corrupt.setVisible(False)

        self.card_total_pdfs.set_value(f"{total_pdfs:,}")
        self.card_total_files.set_value(f"{total_pdfs:,}")
        self.recalculate_metrics()

        total_dup_copies = sum(max(0, len(g.files) - 1) for g in groups)
        speed = total_pdfs / max(0.01, elapsed)
        self.lbl_status.setText(
            f"✅ Completed in {elapsed:.2f}s ({speed:.0f} files/sec). "
            + self._results_summary()
        )

        self._populate_tree()

    def _populate_tree(self):
        self._render_in_progress = False
        self.tree.setUpdatesEnabled(False)
        self.tree.clear()
        self._tree_group_map.clear()
        self._tree_file_map.clear()
        self._checked_count = 0
        self._checked_bytes = 0

        self._pending_render_groups = list(enumerate(self.duplicate_groups, start=1))
        self._render_in_progress = True

        # Render first chunk immediately so UI populates instantly
        self._render_next_chunk(initial=True)

    def _render_next_chunk(self, initial: bool = False):
        if not self._render_in_progress:
            return

        chunk = self._pending_render_groups[:self._render_chunk_size]
        self._pending_render_groups = self._pending_render_groups[self._render_chunk_size:]

        rule = self.combo_rule.currentData() or "cleanest_name"
        primary_root = self.current_root_dirs[0] if self.current_root_dirs else ""
        keep_col, del_col = self._get_tree_status_colors()

        items_to_add = []
        for group_idx, group in chunk:
            files = SafeTrashManager.sort_files_by_rule(group.files, rule, preferred_folder=primary_root)
            group.files = files  # Synchronize group.files with sorted order (index 0 is [KEEP])

            group_item = QTreeWidgetItem()
            group_item.setText(0, f"📦 Group {group_idx}: {group.match_type} ({len(files)} copies)")
            group_item.setText(1, group.formatted_size)
            group_item.setText(2, self._group_space_text(group))
            group_item.setText(3, f"{len(files)} files ({group.size:,} bytes)")
            group_item.setFont(0, QFont("Segoe UI", 9, QFont.Bold))
            group_item.setFlags(group_item.flags() | Qt.ItemIsUserCheckable)
            group_item.setCheckState(0, Qt.Checked if len(files) > 1 else Qt.Unchecked)

            self._tree_group_map[group_item] = group

            for f_idx, f in enumerate(files):
                child = QTreeWidgetItem(group_item)
                child.setFlags(child.flags() | Qt.ItemIsUserCheckable)

                if f_idx == 0:
                    child.setText(0, self._file_role_text(group, f, True))
                    child.setForeground(0, keep_col)
                    child.setCheckState(0, Qt.Unchecked)
                else:
                    child.setText(0, self._file_role_text(group, f, False))
                    child.setForeground(0, del_col)
                    child.setCheckState(0, Qt.Checked)
                    self._checked_count += 1
                    self._checked_bytes += f.size

                if "Name Match" in group.match_type:
                    if f_idx == 0:
                        child.setText(1, f"Kept • {f.formatted_size}")
                    else:
                        ratio = get_name_similarity_ratio(files[0].filename, f.filename)
                        pct = int(ratio * 100)
                        child.setText(1, f"{pct}% Match • {f.formatted_size}")
                else:
                    child.setText(1, f.formatted_size)
                child.setText(2, f.formatted_mtime)

                try:
                    rel_dir = os.path.relpath(f.folder, primary_root)
                    display_folder = "." if rel_dir == "." else rel_dir
                except Exception:
                    display_folder = f.folder
                child.setText(3, display_folder)

                self._tree_file_map[child] = f

            items_to_add.append((group_idx, group_item))

        top_items = [item for _, item in items_to_add]
        self.tree.setUpdatesEnabled(False)
        try:
            self.tree.addTopLevelItems(top_items)
            # Auto-expand first 25 groups for immediate clarity after attaching to tree
            for g_idx, g_item in items_to_add:
                if g_idx <= 25:
                    g_item.setExpanded(True)

            # Check if an active search filter query exists
            query = self.edit_filter.text().strip().lower()
            if query:
                for _, group_item in items_to_add:
                    group_visible = False
                    for c in range(group_item.childCount()):
                        child = group_item.child(c)
                        finfo = self._tree_file_map.get(child)
                        if finfo:
                            match = (query in finfo.filename.lower()) or (query in finfo.folder.lower())
                            child.setHidden(not match)
                            if match:
                                group_visible = True
                    group_item.setHidden(not group_visible)
        finally:
            self.tree.setUpdatesEnabled(True)

        if initial:
            self._refresh_action_buttons_label()
            if self.duplicate_groups:
                first_group = self.duplicate_groups[0]
                self.preview_panel.load_duplicate_group(first_group.files, match_type=first_group.match_type)

        if self._pending_render_groups and self._render_in_progress:
            loaded_count = len(self.duplicate_groups) - len(self._pending_render_groups)
            self.lbl_status.setText(f"Loading results... ({loaded_count:,} / {len(self.duplicate_groups):,} groups)")
            QTimer.singleShot(10, self._render_next_chunk)
        elif not self._pending_render_groups and self._render_in_progress:
            self._render_in_progress = False
            self._refresh_action_buttons_label()
            total_dup_copies = sum(max(0, len(g.files) - 1) for g in self.duplicate_groups)
            self.lbl_status.setText(
                "✅ Ready. " + self._results_summary()
            )

    def _apply_selection_rule(self):
        rule = self.combo_rule.currentData() or "cleanest_name"
        primary_root = self.current_root_dirs[0] if self.current_root_dirs else ""

        self.tree.blockSignals(True)
        self.tree.setUpdatesEnabled(False)
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            group = self._tree_group_map.get(group_item)
            if not group:
                continue

            child_pairs = []
            for c in range(group_item.childCount()):
                child = group_item.child(c)
                finfo = self._tree_file_map.get(child)
                if finfo:
                    child_pairs.append((child, finfo))

            if not child_pairs:
                continue

            f_list = [cp[1] for cp in child_pairs]
            sorted_f = SafeTrashManager.sort_files_by_rule(f_list, rule, preferred_folder=primary_root)
            group.files = sorted_f  # Keep group.files in sync with sorted order (index 0 is [KEEP])

            keep_col, del_col = self._get_tree_status_colors()
            f_to_child = {cp[1].path: cp[0] for cp in child_pairs}
            for idx, finfo in enumerate(sorted_f):
                child = f_to_child.get(finfo.path)
                if not child:
                    continue
                if idx == 0:
                    child.setText(0, self._file_role_text(group, finfo, True))
                    child.setForeground(0, keep_col)
                    child.setCheckState(0, Qt.Unchecked)
                else:
                    child.setText(0, self._file_role_text(group, finfo, False))
                    child.setForeground(0, del_col)
                    child.setCheckState(0, Qt.Checked)

                if "Name Match" in group.match_type:
                    if idx == 0:
                        child.setText(1, f"Kept • {finfo.formatted_size}")
                    else:
                        ratio = get_name_similarity_ratio(sorted_f[0].filename, finfo.filename)
                        pct = int(ratio * 100)
                        child.setText(1, f"{pct}% Match • {finfo.formatted_size}")

            group_item.setCheckState(0, Qt.Checked if len(sorted_f) > 1 else Qt.Unchecked)

        self.tree.setUpdatesEnabled(True)
        self.tree.blockSignals(False)
        self._recalculate_checked_totals()

    def _uncheck_all(self):
        self.tree.blockSignals(True)
        self.tree.setUpdatesEnabled(False)
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            group_item.setCheckState(0, Qt.Unchecked)
            for c in range(group_item.childCount()):
                group_item.child(c).setCheckState(0, Qt.Unchecked)
        self.tree.setUpdatesEnabled(True)
        self.tree.blockSignals(False)
        self._checked_count = 0
        self._checked_bytes = 0
        self._refresh_action_buttons_label()

    def _invert_selection(self):
        self.tree.blockSignals(True)
        self.tree.setUpdatesEnabled(False)
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            total_dups = 0
            checked_dups = 0
            for c in range(group_item.childCount()):
                child = group_item.child(c)
                # Keep master / [KEEP] copy protected from accidental selection
                if self._is_reference_item(child):
                    child.setCheckState(0, Qt.Unchecked)
                else:
                    new_st = Qt.Unchecked if child.checkState(0) == Qt.Checked else Qt.Checked
                    child.setCheckState(0, new_st)
                    total_dups += 1
                    if new_st == Qt.Checked:
                        checked_dups += 1

            if total_dups > 0:
                if checked_dups == total_dups:
                    group_item.setCheckState(0, Qt.Checked)
                elif checked_dups == 0:
                    group_item.setCheckState(0, Qt.Unchecked)
                else:
                    group_item.setCheckState(0, Qt.PartiallyChecked)

        self.tree.setUpdatesEnabled(True)
        self.tree.blockSignals(False)
        self._recalculate_checked_totals()

    def _on_item_checked(self, item: QTreeWidgetItem, column: int):
        if column != 0:
            return

        if item in self._tree_group_map:
            # Group header tri-state checkbox clicked!
            st = item.checkState(0)
            self.tree.blockSignals(True)
            for c in range(item.childCount()):
                child = item.child(c)
                if self._is_reference_item(child):
                    child.setCheckState(0, Qt.Unchecked)
                else:
                    child.setCheckState(0, Qt.Checked if st == Qt.Checked else Qt.Unchecked)
            self.tree.blockSignals(False)
            self._recalculate_checked_totals()

        elif item in self._tree_file_map:
            # Child item clicked, update parent group header state
            parent = item.parent()
            if parent and parent in self._tree_group_map:
                total_dups = 0
                checked_dups = 0
                for c in range(parent.childCount()):
                    child = parent.child(c)
                    if not self._is_reference_item(child):
                        total_dups += 1
                        if child.checkState(0) == Qt.Checked:
                            checked_dups += 1

                self.tree.blockSignals(True)
                if total_dups > 0:
                    if checked_dups == total_dups:
                        parent.setCheckState(0, Qt.Checked)
                    elif checked_dups == 0:
                        parent.setCheckState(0, Qt.Unchecked)
                    else:
                        parent.setCheckState(0, Qt.PartiallyChecked)
                self.tree.blockSignals(False)

            self._recalculate_checked_totals()

    def _recalculate_checked_totals(self):
        c_cnt = 0
        c_bytes = 0
        for item, finfo in self._tree_file_map.items():
            if item.checkState(0) == Qt.Checked:
                c_cnt += 1
                c_bytes += finfo.size
        self._checked_count = c_cnt
        self._checked_bytes = c_bytes
        self._refresh_action_buttons_label()

    def _update_action_buttons_state(self):
        self._recalculate_checked_totals()

    def _show_more_actions_menu(self):
        menu = QMenu(self)

        act_recycle = menu.addAction(f"♻️ Move Checked ({self._checked_count}) to Windows Recycle Bin")
        act_recycle.setEnabled(self._checked_count > 0)
        act_recycle.triggered.connect(self._confirm_recycle_bin)

        act_archive = menu.addAction(f"📦 Move Checked ({self._checked_count}) to Custom Archive...")
        act_archive.setEnabled(self._checked_count > 0)
        act_archive.triggered.connect(self._confirm_archive_to_custom_folder)

        act_hardlink = menu.addAction(f"🔗 Replace Checked ({self._checked_count}) with NTFS Hardlinks (Exact Hash Only)")
        act_hardlink.setEnabled(self._checked_count > 0)
        act_hardlink.triggered.connect(self._confirm_replace_with_hardlinks)

        menu.addSeparator()

        act_clean = menu.addAction("🧹 Scan & Clean Empty Subfolders in Target Folders...")
        act_clean.triggered.connect(self._confirm_clean_empty_folders)

        act_corrupt = menu.addAction("⚠️ Inspect 0-Byte / Damaged Files...")
        act_corrupt.triggered.connect(self._open_corrupt_files_dialog)

        menu.exec(self.btn_more_actions.mapToGlobal(self.btn_more_actions.rect().bottomLeft()))

    def _open_corrupt_files_dialog(self):
        if not self._corrupt_files:
            QMessageBox.information(
                self, "No 0-Byte / Damaged Files", "No 0-byte or corrupted files were detected in the target directories."
            )
            return
        dlg = CorruptFilesDialog(self._corrupt_files, self.current_root_dirs, self)
        dlg.exec()
        if hasattr(dlg, "corrupt_files"):
            self._corrupt_files = dlg.corrupt_files
        if self._corrupt_files:
            self.btn_corrupt.setText(f"⚠️ Corrupt/0-Byte ({len(self._corrupt_files)})")
            self.btn_corrupt.setVisible(True)
        else:
            self.btn_corrupt.setVisible(False)

    def _refresh_action_buttons_label(self):
        if self._checked_count > 0:
            size_str = self._format_bytes(self._checked_bytes)
            self.btn_safe_delete.setText(f"🗑️ Move {self._checked_count} Checked to Safe Trash ({size_str})")
            self.btn_safe_delete.setEnabled(True)
            self.btn_more_actions.setEnabled(True)
        else:
            self.btn_safe_delete.setText("🗑️ Move Checked to Safe Trash")
            self.btn_safe_delete.setEnabled(False)
            self.btn_more_actions.setEnabled(False)

    def _on_tree_selection_changed(self):
        self._preview_debounce_timer.start()

    def _do_load_selected_preview(self):
        selected_items = self.tree.selectedItems()
        if not selected_items:
            return

        item = selected_items[0]
        if item in self._tree_group_map:
            group = self._tree_group_map[item]
            self.preview_panel.load_duplicate_group(
                group.files,
                match_type=group.match_type,
                selected_idx_a=0,
                selected_idx_b=1 if len(group.files) > 1 else 0
            )
        elif item in self._tree_file_map:
            parent = item.parent()
            if parent and parent in self._tree_group_map:
                group = self._tree_group_map[parent]
                file_info = self._tree_file_map[item]
                try:
                    idx = group.files.index(file_info)
                except ValueError:
                    idx = 0

                # If this group is already loaded in the preview panel, preserve Pane 1's page and zoom!
                if getattr(self.preview_panel, 'current_files', None) == group.files:
                    if idx != 0:
                        self.preview_panel.select_comparison_file(idx)
                else:
                    other_idx = 1 if len(group.files) > 1 else 0
                    self.preview_panel.load_duplicate_group(
                        group.files,
                        match_type=group.match_type,
                        selected_idx_a=0,
                        selected_idx_b=idx if idx != 0 else other_idx
                    )

    def _filter_tree(self, text: str = ""):
        self._filter_timer.stop()
        self._do_filter_tree(text)

    def _do_filter_tree(self, text: Optional[str] = None):
        if text is None:
            text = self.edit_filter.text()
        query = text.strip().lower()
        self.tree.setUpdatesEnabled(False)
        try:
            for i in range(self.tree.topLevelItemCount()):
                group_item = self.tree.topLevelItem(i)
                group_visible = False
                for c in range(group_item.childCount()):
                    child = group_item.child(c)
                    finfo = self._tree_file_map.get(child)
                    if finfo:
                        match = (query in finfo.filename.lower()) or (query in finfo.folder.lower())
                        child.setHidden(not match and bool(query))
                        if match or not query:
                            group_visible = True
                group_item.setHidden(not group_visible and bool(query))
        finally:
            self.tree.setUpdatesEnabled(True)

    def _show_context_menu(self, pos):
        item = self.tree.itemAt(pos)
        if not item:
            return

        menu = QMenu(self)

        # 1. Group Header Context Menu
        if item in self._tree_group_map:
            act_check_dups = menu.addAction("✔ Check All Duplicates in this Group")
            act_uncheck_group = menu.addAction("✕ Uncheck All in this Group")
            menu.addSeparator()
            is_expanded = item.isExpanded()
            act_toggle_expand = menu.addAction("Collapse Group" if is_expanded else "Expand Group")

            action = menu.exec(self.tree.viewport().mapToGlobal(pos))
            if action == act_check_dups:
                item.setCheckState(0, Qt.Checked)
            elif action == act_uncheck_group:
                item.setCheckState(0, Qt.Unchecked)
            elif action == act_toggle_expand:
                item.setExpanded(not is_expanded)
            return

        # 2. Child File Item Context Menu
        if item not in self._tree_file_map:
            return

        finfo = self._tree_file_map[item]
        act_preview = menu.addAction("👁️ Preview this File")
        act_open_ext = menu.addAction("↗ Open in Default Application")
        act_reveal = menu.addAction("📁 Reveal in File Explorer")
        menu.addSeparator()

        parent = item.parent()
        act_make_keep = menu.addAction("⭐ Make this the KEPT original (Unmark & Protect)")
        is_checked = item.checkState(0) == Qt.Checked
        act_toggle = menu.addAction("Unmark from Delete" if is_checked else "Mark for Delete")

        action = menu.exec(self.tree.viewport().mapToGlobal(pos))
        if action == act_preview:
            self.preview_panel.preview_single_file(finfo)
        elif action == act_open_ext:
            try:
                os.startfile(finfo.path)
            except Exception as e:
                QMessageBox.warning(self, "Open Error", str(e))
        elif action == act_reveal:
            try:
                open_in_file_explorer(finfo.path)
            except Exception as e:
                QMessageBox.warning(self, "Reveal Error", str(e))
        elif action == act_make_keep and parent:
            keep_col, del_col = self._get_tree_status_colors()
            self.tree.blockSignals(True)
            for c in range(parent.childCount()):
                child = parent.child(c)
                c_finfo = self._tree_file_map.get(child)
                if child == item:
                    child.setText(0, self._file_role_text(self._tree_group_map.get(parent), finfo, True))
                    child.setForeground(0, keep_col)
                    child.setCheckState(0, Qt.Unchecked)
                else:
                    if c_finfo:
                        child.setText(0, self._file_role_text(self._tree_group_map.get(parent), c_finfo, False))
                    child.setForeground(0, del_col)
                    child.setCheckState(0, Qt.Checked)
            parent.setCheckState(0, Qt.Checked)
            self.tree.blockSignals(False)

            # CRITICAL: Synchronize data model group.files and preview panel
            if parent in self._tree_group_map:
                group = self._tree_group_map[parent]
                other_files = [f for f in group.files if f.path != finfo.path]
                group.files = [finfo] + other_files
                self.preview_panel.load_duplicate_group(group.files, match_type=group.match_type)

            self._recalculate_checked_totals()
        elif action == act_toggle:
            item.setCheckState(0, Qt.Unchecked if is_checked else Qt.Checked)

    def _validate_safe_deletion(self) -> Tuple[bool, List[QTreeWidgetItem], List[str], int]:
        """
        Validates that checked files are safe to delete.
        CRITICAL SAFETY GUARDS:
        1. Ensures that no duplicate group has 100% of its copies marked for deletion (At least 1 kept).
        2. Warns user if search filter is actively hiding checked files from view before deletion.
        """
        items_to_delete = []
        files_to_delete = []
        total_bytes = 0
        heuristic_groups = set()

        # Guard 1: Verify at least one kept file per group
        for i in range(self.tree.topLevelItemCount()):
            group_item = self.tree.topLevelItem(i)
            total_children = group_item.childCount()
            checked_in_group = []
            for c in range(total_children):
                child = group_item.child(c)
                if child.checkState(0) == Qt.Checked:
                    checked_in_group.append(child)

            if total_children > 0 and len(checked_in_group) == total_children:
                QMessageBox.warning(
                    self,
                    "Safety Protection: All Copies Selected",
                    f"Safety guard triggered on:\n{group_item.text(0)}\n\n"
                    f"ALL copies in this group are marked for deletion! "
                    f"To prevent total data loss, at least ONE file must be kept.\n"
                    f"Please uncheck at least one copy in this group before proceeding."
                )
                return False, [], [], 0

            group = self._tree_group_map.get(group_item)
            if checked_in_group and group and "Exact Hash" not in group.match_type:
                heuristic_groups.add(group.match_type)

            for child in checked_in_group:
                finfo = self._tree_file_map.get(child)
                if finfo:
                    items_to_delete.append(child)
                    files_to_delete.append(finfo.path)
                    total_bytes += finfo.size

        # Name, size, page/title and visual matches are candidate-finding
        # strategies, not proof that file contents are identical. Require an
        # explicit acknowledgement before any move/recycle/archive action.
        if heuristic_groups and files_to_delete:
            match_types = ", ".join(sorted(heuristic_groups))
            reply = QMessageBox.question(
                self,
                "Review Heuristic Matches",
                f"The selected files include matches found by: {match_types}.\n\n"
                "These strategies can include files whose contents differ. "
                "Compare the selected files before continuing. Do you want to "
                "continue to the action confirmation?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.No,
            )
            if reply != QMessageBox.Yes:
                return False, [], [], 0

        # Guard 2: Check if search filter is hiding checked files
        query = self.edit_filter.text().strip()
        if query and items_to_delete:
            hidden_checked_items = []
            visible_checked_items = []

            for child in items_to_delete:
                if child.isHidden() or (child.parent() and child.parent().isHidden()):
                    hidden_checked_items.append(child)
                else:
                    visible_checked_items.append(child)

            if hidden_checked_items:
                msg_box = QMessageBox(self)
                msg_box.setWindowTitle("Search Filter Active — Hidden Files Detected")
                msg_box.setIcon(QMessageBox.Warning)
                msg_box.setText(
                    f"An active search filter ('{query}') is hiding some checked duplicates.\n\n"
                    f"• Visible checked files: {len(visible_checked_items)}\n"
                    f"• Hidden checked files (filtered out): {len(hidden_checked_items)}\n"
                    f"• Total checked files: {len(items_to_delete)}\n\n"
                    f"Which files do you want to delete?"
                )
                btn_del_visible = msg_box.addButton(f"Delete ONLY {len(visible_checked_items)} Visible Files", QMessageBox.ActionRole)
                btn_del_all = msg_box.addButton(f"Delete ALL {len(items_to_delete)} Checked Files", QMessageBox.ActionRole)
                btn_cancel = msg_box.addButton("Cancel", QMessageBox.RejectRole)

                msg_box.exec()
                clicked = msg_box.clickedButton()

                if clicked == btn_cancel:
                    return False, [], [], 0
                elif clicked == btn_del_visible:
                    # Uncheck hidden files so only visible are deleted
                    self.tree.blockSignals(True)
                    for h_child in hidden_checked_items:
                        h_child.setCheckState(0, Qt.Unchecked)
                    self.tree.blockSignals(False)
                    self._recalculate_checked_totals()

                    items_to_delete = visible_checked_items
                    files_to_delete = [self._tree_file_map[c].path for c in visible_checked_items if c in self._tree_file_map]
                    total_bytes = sum(self._tree_file_map[c].size for c in visible_checked_items if c in self._tree_file_map)
                elif clicked != btn_del_all:
                    # Escape, the window close button, or any unexpected
                    # dialog result must behave as Cancel.
                    return False, [], [], 0

        return True, items_to_delete, files_to_delete, total_bytes

    def _confirm_safe_delete(self):
        is_safe, items_to_delete, files_to_delete, total_bytes = self._validate_safe_deletion()
        if not is_safe or not files_to_delete:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Safe Move to '_deleted_files'",
            f"Safely move {len(files_to_delete)} duplicate file(s) to local '_deleted_files' folders?\n\n"
            f"Files will be kept safely on their local drives with full manifest.\n"
            "Files can be restored from 'Restore / Undo' while their trash copy and manifest remain available.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self._queue_verified_action("quarantine", items_to_delete, "Safe Move Completed")

    def _confirm_recycle_bin(self):
        is_safe, items_to_delete, files_to_delete, total_bytes = self._validate_safe_deletion()
        if not is_safe or not files_to_delete:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Move to Windows Recycle Bin",
            f"Send {len(files_to_delete)} duplicate file(s) to the Windows Recycle Bin?\n\n"
            f"Files remain recoverable while retained in Windows Recycle Bin. Disk space may remain occupied.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self._queue_verified_action("recycle", items_to_delete, "Recycle Bin")

    def _confirm_archive_to_custom_folder(self):
        is_safe, items_to_delete, files_to_delete, total_bytes = self._validate_safe_deletion()
        if not is_safe or not files_to_delete:
            return

        target_folder = QFileDialog.getExistingDirectory(
            self, "Select Archive Directory to Move Duplicates", ""
        )
        if not target_folder:
            return

        reply = QMessageBox.question(
            self,
            "Confirm Move to Custom Archive",
            f"Move {len(files_to_delete)} duplicate file(s) to:\n{target_folder}?\n\n"
            f"This safely moves files out of your collection into the selected archive folder.",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        self._queue_verified_action("archive", items_to_delete, "Archive Completed", target_folder)

    def _confirm_replace_with_hardlinks(self):
        is_safe, items_to_delete, files_to_delete, total_bytes = self._validate_safe_deletion()
        if not is_safe or not files_to_delete:
            return

        unsafe_groups = set()
        pairs: List[Tuple[str, str]] = []

        for item in items_to_delete:
            parent = item.parent()
            if parent and parent in self._tree_group_map:
                group = self._tree_group_map[parent]
                # Enforce cryptographic exact match for hardlink safety
                if "Exact Hash" not in group.match_type and "Safe Match" not in group.match_type:
                    unsafe_groups.add(group.match_type)
                    continue

                # Locate the KEPT (unchecked) original file in this specific group
                source_finfo = None
                for c in range(parent.childCount()):
                    child_item = parent.child(c)
                    if child_item.checkState(0) == Qt.Unchecked:
                        source_finfo = self._tree_file_map.get(child_item)
                        break

                if not source_finfo and group.files:
                    source_finfo = group.files[0]

                target_finfo = self._tree_file_map.get(item)
                if source_finfo and target_finfo and source_finfo.path != target_finfo.path:
                    pairs.append((source_finfo.path, target_finfo.path))

        if unsafe_groups:
            QMessageBox.warning(
                self,
                "Non-Hash Matches Skipped",
                f"NTFS Hardlinks can ONLY be created for 100% verified cryptographic matches.\n\n"
                f"Some selected files were from non-hash modes ({', '.join(unsafe_groups)}), "
                f"meaning their content may not be 100% identical.\n"
                f"To protect your data, non-hash groups will not be hardlinked."
            )

        if not pairs:
            QMessageBox.information(
                self,
                "No Valid Hardlink Pairs",
                "No valid Exact Hash pairs found to replace with hardlinks."
            )
            return

        reply = QMessageBox.question(
            self,
            "Confirm NTFS Hardlink Deduplication",
            f"Replace {len(pairs)} duplicate file(s) with NTFS Hardlinks to their original files?\n\n"
            f"Benefits:\n"
            f"• Duplicate paths will share one underlying file.\n"
            f"• File names and directory paths stay completely intact everywhere.\n"
            f"• Editing shared content through either path affects both copies.\n\n"
            f"Do you want to proceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        valid_paths = {target for _, target in pairs}
        valid_items = [item for item in items_to_delete if self._tree_file_map[item].path in valid_paths]
        self._queue_verified_action("hardlink", valid_items, "Hardlink Deduplication")

    def _queue_verified_action(self, kind, items, title, target=None):
        if self._closing or self._action_pending:
            return
        snapshots = []
        for item in items:
            info = self._tree_file_map[item]
            parent = item.parent()
            group = self._tree_group_map.get(parent)
            exact = bool(group and ("Exact Hash" in group.match_type or group.match_type == "Safe Match"))
            keeper = next((self._tree_file_map[c].path for i in range(parent.childCount())
                           if (c := parent.child(i)).checkState(0) == Qt.Unchecked and c in self._tree_file_map), "")
            snapshots.append(ActionFile(info.path, info.size, info.mtime, keeper, exact))
        releases = [f for viewer in self.preview_panel.viewers for f in (viewer._active_future, viewer._metadata_future) if f and not f.done()]
        self.preview_panel.unload_all()
        self._action_pending = True
        self.centralWidget().setEnabled(False)
        self.progress_bar.setVisible(True)
        self.progress_bar.setRange(0, 0)
        self.lbl_status.setText("Verifying current file contents before action...")
        self._action_future = self._action_executor.submit(perform_verified_action, kind, snapshots, list(self.current_root_dirs), target, releases)
        weak = weakref.ref(self)
        def completed(future):
            try:
                outcome = future.result()
            except Exception as error:
                outcome = error
            window = weak()
            if window is not None:
                try:
                    window._action_done.emit((list(items), title, outcome))
                except RuntimeError:
                    pass
        self._action_future.add_done_callback(completed)

    @Slot(object)
    def _on_action_done(self, payload):
        items, title, outcome = payload
        self._action_pending = False
        self.centralWidget().setEnabled(True)
        self.progress_bar.setVisible(False)
        if isinstance(outcome, Exception):
            self.lbl_status.setText(f"Action failed: {outcome}")
            if not self._closing:
                QMessageBox.critical(self, title, str(outcome))
            return
        self._remove_successful_items_from_tree(items, outcome.successful_paths)
        message = f"Successfully processed {len(outcome.successful_paths)} file(s)."
        self.lbl_status.setText(message)
        if outcome.errors:
            message += "\n\nErrors:\n" + "\n".join(outcome.errors[:5])
            self.lbl_status.setText(
                f"Processed {len(outcome.successful_paths)} file(s). {len(outcome.errors)} error(s): {outcome.errors[0]}"
            )
            if not self._closing:
                dialog = QMessageBox(QMessageBox.Icon.Warning, title, message, QMessageBox.StandardButton.Ok, self)
                dialog.setModal(False)
                dialog.setAttribute(Qt.WidgetAttribute.WA_DeleteOnClose)
                dialog.show()

    def _confirm_clean_empty_folders(self):
        roots = [r for r in self.current_root_dirs if os.path.isdir(r)]
        if not roots:
            QMessageBox.information(self, "Folder Required", "Please select a scanned root folder or drive first.")
            return

        roots_str = "\n".join(f"• {r}" for r in roots[:5])
        reply = QMessageBox.question(
            self,
            "Confirm Clean Empty Folders",
            f"Scan and remove empty subfolders inside:\n{roots_str}\n\n"
            f"This will recursively delete only folders that contain 0 files.\n"
            f"Do you want to proceed?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if reply != QMessageBox.Yes:
            return

        removed_cnt, removed_list = SafeTrashManager.clean_empty_folders(
            roots, exclude_dirs=self.excluded_directories
        )
        msg = f"Cleaned {removed_cnt} empty folder(s)."
        QMessageBox.information(self, "Clean Complete", msg)

    def _gather_checked(self):
        items_to_delete = []
        files_to_delete = []
        total_bytes = 0

        for item, finfo in list(self._tree_file_map.items()):
            if item.checkState(0) == Qt.Checked:
                items_to_delete.append(item)
                files_to_delete.append(finfo.path)
                total_bytes += finfo.size

        return items_to_delete, files_to_delete, total_bytes

    def _remove_successful_items_from_tree(self, selected_items: list, successful_paths: List[str]):
        succeeded = {os.path.normcase(os.path.abspath(path)) for path in successful_paths}
        items = [
            item for item in selected_items
            if (finfo := self._tree_file_map.get(item)) is not None
            and os.path.normcase(os.path.abspath(finfo.path)) in succeeded
        ]
        self._remove_deleted_items_from_tree(items)

    def _remove_deleted_items_from_tree(self, items_to_delete: list):
        affected_parents = set()
        for item in items_to_delete:
            parent = item.parent()
            finfo = self._tree_file_map.pop(item, None)
            if parent:
                affected_parents.add(parent)
                group = self._tree_group_map.get(parent)
                if group and finfo in group.files:
                    group.files.remove(finfo)
                parent.removeChild(item)

        # Check all affected parents
        for parent in list(affected_parents):
            if parent.childCount() <= 1:
                # Clean up remaining child mapping to prevent memory leak
                for c in range(parent.childCount()):
                    rem_child = parent.child(c)
                    self._tree_file_map.pop(rem_child, None)

                group = self._tree_group_map.pop(parent, None)
                if group and group in self.duplicate_groups:
                    self.duplicate_groups.remove(group)
                idx = self.tree.indexOfTopLevelItem(parent)
                if idx >= 0:
                    self.tree.takeTopLevelItem(idx)
            else:
                # Update header text for remaining duplicates in group
                group = self._tree_group_map.get(parent)
                if group:
                    parent.setText(0, f"📦 {group.match_type} ({parent.childCount()} copies)")
                    parent.setText(2, self._group_space_text(group))
                    parent.setText(3, f"{parent.childCount()} files ({group.size:,} bytes)")

        self._update_action_buttons_state()
        self.recalculate_metrics()
        self.preview_panel.unload_all()

    def _open_restore_dialog(self):
        roots = [r for r in self.current_root_dirs if os.path.isdir(r)]
        if not roots:
            QMessageBox.information(self, "Folder Required", "Please select at least one valid scanned root folder.")
            return

        dlg = RestoreDialog(roots, self)
        dlg.exec()
        if getattr(dlg, "restored_count", 0) > 0:
            reply = QMessageBox.question(
                self,
                "Files Restored — Refresh Results?",
                f"Successfully restored {dlg.restored_count} file(s) to original directories.\n\n"
                f"Would you like to re-scan now to update duplicate groups and space metrics?",
                QMessageBox.Yes | QMessageBox.No,
                QMessageBox.Yes
            )
            if reply == QMessageBox.Yes:
                self._start_scan()
            else:
                self.recalculate_metrics()

    def _show_export_menu(self):
        if not self.duplicate_groups:
            QMessageBox.information(self, "No Results", "No duplicate groups to export. Run a scan first.")
            return

        menu = QMenu(self)
        act_html = menu.addAction("🌐 Export to Interactive HTML Dashboard (.html)")
        act_excel = menu.addAction("📊 Export to Excel Spreadsheet (.xlsx)")
        act_csv = menu.addAction("📄 Export to CSV (.csv)")

        action = menu.exec(self.btn_export.mapToGlobal(self.btn_export.rect().bottomLeft()))
        if action == act_html:
            path, _ = QFileDialog.getSaveFileName(self, "Save HTML Dashboard", "Duplicates_Report.html", "HTML Files (*.html)")
            if path:
                ok, msg = export_to_html(self.duplicate_groups, path)
                if ok:
                    reply = QMessageBox.question(
                        self, "Export Successful", f"{msg}\n\nWould you like to open it in your web browser now?",
                        QMessageBox.Yes | QMessageBox.No, QMessageBox.Yes
                    )
                    if reply == QMessageBox.Yes:
                        import webbrowser
                        webbrowser.open(os.path.abspath(path))
                else:
                    QMessageBox.critical(self, "Export Failed", msg)
        elif action == act_excel:
            path, _ = QFileDialog.getSaveFileName(self, "Save Excel Report", "Duplicates_Report.xlsx", "Excel Files (*.xlsx)")
            if path:
                ok, msg = export_to_excel(self.duplicate_groups, path)
                if ok:
                    QMessageBox.information(self, "Export Successful", msg)
                else:
                    QMessageBox.critical(self, "Export Failed", msg)
        elif action == act_csv:
            path, _ = QFileDialog.getSaveFileName(self, "Save CSV Report", "Duplicates_Report.csv", "CSV Files (*.csv)")
            if path:
                ok, msg = export_to_csv(self.duplicate_groups, path)
                if ok:
                    QMessageBox.information(self, "Export Successful", msg)
                else:
                    QMessageBox.critical(self, "Export Failed", msg)
