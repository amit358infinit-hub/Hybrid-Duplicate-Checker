"""
Modern Fluent Theme System for All Files Duplicate Checker.
Supports:
- Dark Mode (Studio Slate / High-contrast, Eye-comfort, WCAG-compliant)
- Light Mode (Clean Paper / Professional Document UI)
Provides complete styling for all controls, tables, trees, scrollbars, and widgets.
"""
from __future__ import annotations

THEME_COLORS = {
    "dark": {
        "text_primary": "#f8fafc",
        "text_secondary": "#94a3b8",
        "text_dim": "#64748b",
        "metric_files": "#38bdf8",
        "metric_groups": "#fb923c",
        "metric_redundant": "#f87171",
        "metric_savings": "#34d399",
        "keep_color": "#34d399",
        "delete_color": "#f87171",
        "card_selected_bg": "#1e3a5f",
        "card_selected_border": "#3b82f6",
        "card_bg": "#1e293b",
        "card_border": "#334155",
    },
    "light": {
        "text_primary": "#0f172a",
        "text_secondary": "#475569",
        "text_dim": "#64748b",
        "metric_files": "#0284c7",
        "metric_groups": "#ea580c",
        "metric_redundant": "#dc2626",
        "metric_savings": "#059669",
        "keep_color": "#059669",
        "delete_color": "#dc2626",
        "card_selected_bg": "#eff6ff",
        "card_selected_border": "#2563eb",
        "card_bg": "#ffffff",
        "card_border": "#cbd5e1",
    }
}

DARK_THEME_QSS = """
/* Base Window & Global Typography */
QMainWindow, QDialog, QMessageBox {
    background-color: #0b1120;
    color: #f8fafc;
}

QWidget {
    color: #f1f5f9;
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, 'SF Pro Display', Roboto, sans-serif;
    font-size: 12px;
}

/* Containers & Structural Panels */
QFrame#top_box, QFrame#control_box, QFrame.MetricCard {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
}

QFrame#meta_frame {
    background-color: #0f172a;
    border: 1px dashed #3b82f6;
    border-radius: 6px;
    padding: 6px;
}

QSplitter::handle {
    background-color: #1e293b;
}
QSplitter::handle:horizontal {
    width: 6px;
}
QSplitter::handle:vertical {
    height: 6px;
}

/* Results Tree Widget */
QTreeWidget {
    background-color: #111827;
    border: 1px solid #334155;
    border-radius: 8px;
    font-size: 12px;
    color: #f8fafc;
    alternate-background-color: #0f172a;
    padding: 2px;
}

QTreeWidget::item {
    padding: 6px 4px;
    border-bottom: 1px solid #1e293b;
    border-radius: 4px;
}

QTreeWidget::item:hover {
    background-color: #1f293d;
}

QTreeWidget::item:selected {
    background-color: #1e3a5f;
    border: 1px solid #3b82f6;
    color: #f8fafc;
}

/* Table Widgets (Archive Table, Metadata Comparison, Restore History) */
QTableWidget, QTableView {
    background-color: #111827;
    color: #f1f5f9;
    gridline-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 6px;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
    alternate-background-color: #0f172a;
}

QTableWidget::item, QTableView::item {
    padding: 5px 8px;
    color: #f1f5f9;
    border-bottom: 1px solid #1e293b;
}

QTableWidget::item:hover, QTableView::item:hover {
    background-color: #1f293d;
}

QTableWidget::item:selected, QTableView::item:selected {
    background-color: #2563eb;
    color: #ffffff;
}

QHeaderView::section {
    background-color: #1e293b;
    color: #cbd5e1;
    font-weight: 600;
    font-size: 11px;
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid #334155;
    border-right: 1px solid #334155;
}

QTableCornerButton::section {
    background-color: #1e293b;
    border: none;
    border-bottom: 1px solid #334155;
    border-right: 1px solid #334155;
}

/* Form Inputs, Combos, SpinBoxes */
QLineEdit, QComboBox, QSpinBox {
    border: 1px solid #475569;
    border-radius: 6px;
    padding: 5px 8px;
    background-color: #0f172a;
    color: #f8fafc;
    font-size: 12px;
    min-height: 22px;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #3b82f6;
    background-color: #0b1120;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid #334155;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}

QComboBox QAbstractItemView {
    background-color: #1e293b;
    color: #f8fafc;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
    border: 1px solid #475569;
    border-radius: 6px;
    padding: 4px;
}

/* PushButtons (General) */
QPushButton {
    border: 1px solid #475569;
    border-radius: 6px;
    padding: 5px 12px;
    background-color: #1e293b;
    color: #f8fafc;
    font-size: 12px;
    font-weight: 500;
    min-height: 22px;
}

QPushButton:hover {
    background-color: #334155;
    border-color: #64748b;
    color: #ffffff;
}

QPushButton:pressed {
    background-color: #0f172a;
}

QPushButton:disabled {
    background-color: #1e293b;
    color: #64748b;
    border-color: #334155;
}

/* Preview Toolbar Buttons (−, +, W, P, ↗, 📁) */
QPushButton#preview_tool_btn {
    border: 1px solid #475569;
    border-radius: 4px;
    background-color: #1e293b;
    color: #f8fafc;
    font-size: 13px;
    font-weight: bold;
    min-height: 24px;
    max-height: 24px;
    min-width: 24px;
    max-width: 24px;
    padding: 0px;
}

QPushButton#preview_tool_btn:hover {
    background-color: #3b82f6;
    border-color: #60a5fa;
    color: #ffffff;
}

QPushButton#preview_tool_btn:pressed {
    background-color: #1d4ed8;
}

/* CheckBoxes */
QCheckBox {
    color: #f1f5f9;
    font-size: 12px;
    spacing: 6px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #475569;
    border-radius: 4px;
    background-color: #0f172a;
}

QCheckBox::indicator:hover {
    border-color: #3b82f6;
}

QCheckBox::indicator:checked {
    background-color: #2563eb;
    border-color: #3b82f6;
}

/* Scroll Areas & Viewports */
QScrollArea {
    background-color: #0b1120;
    border: 1px solid #334155;
    border-radius: 6px;
}

QScrollArea#preview_scroll_area {
    background-color: #070d17;
    border: 1px solid #334155;
    border-radius: 6px;
}

QScrollArea#filmstrip_scroll {
    background-color: #0f172a;
    border: 1px dashed #334155;
    border-radius: 6px;
}

/* Filmstrip Cards */
QFrame#filmstrip_card {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 6px;
}

QFrame#filmstrip_card:hover {
    background-color: #273549;
    border-color: #64748b;
}

/* Text & Code Editors */
QPlainTextEdit, QTextEdit {
    background-color: #0b1120;
    color: #f1f5f9;
    border: 1px solid #334155;
    border-radius: 6px;
    padding: 6px;
    font-family: Consolas, 'Courier New', monospace;
    font-size: 12px;
    selection-background-color: #2563eb;
    selection-color: #ffffff;
}

QPlainTextEdit#preview_hex_editor {
    background-color: #050a14;
    color: #38bdf8;
    border: 1px solid #1e293b;
}

/* Labels & Status */
QLabel {
    color: #f1f5f9;
}

QLabel#preview_title_label {
    font-weight: bold;
    font-size: 12px;
    color: #f8fafc;
}

QLabel#preview_info_label {
    font-size: 11px;
    color: #94a3b8;
}

QLabel#preview_extra_info {
    font-size: 11px;
    color: #38bdf8;
}

QLabel#preview_image_label {
    background-color: #0b0f19;
    color: #94a3b8;
    font-size: 13px;
    padding: 8px;
}

QLabel#match_badge {
    background-color: #0369a1;
    color: #e0f2fe;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
}

/* Office Document Inspector Card */
QFrame#preview_office_card {
    background-color: #1e293b;
    border: 1px solid #334155;
    border-radius: 8px;
}

QLabel#preview_office_title {
    font-size: 14px;
    font-weight: bold;
    color: #f8fafc;
}

QTableWidget#preview_office_table {
    background-color: #0f172a;
    border: 1px solid #334155;
    border-radius: 6px;
    color: #f1f5f9;
}

/* ScrollBars */
QScrollBar:vertical {
    background: #0f172a;
    width: 10px;
    margin: 0px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background: #334155;
    min-height: 24px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #475569;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background: #0f172a;
    height: 10px;
    margin: 0px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background: #334155;
    min-width: 24px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background: #475569;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    height: 0px;
}

/* Progress Bar */
QProgressBar {
    border: 1px solid #334155;
    border-radius: 6px;
    background-color: #1e293b;
    text-align: center;
    color: #f8fafc;
    font-size: 11px;
    font-weight: bold;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:1 #38bdf8);
    border-radius: 5px;
}

QListWidget {
    background-color: #111827;
    border: 1px solid #334155;
    border-radius: 6px;
    color: #f8fafc;
    font-size: 12px;
}

QListWidget::item {
    padding: 4px;
}

QListWidget::item:selected {
    background-color: #2563eb;
    color: #ffffff;
}

QScrollArea#chips_scroll {
    border: 1px solid #334155;
    border-radius: 6px;
    background-color: #0f172a;
}

/* Menus */
QMenu {
    background-color: #1e293b;
    color: #f8fafc;
    border: 1px solid #475569;
    padding: 4px;
    border-radius: 6px;
}

QMenu::item {
    padding: 6px 20px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #2563eb;
    color: #ffffff;
}

QMenu::separator {
    height: 1px;
    background-color: #334155;
    margin: 4px 0px;
}
"""

LIGHT_THEME_QSS = """
/* Base Window & Global Typography */
QMainWindow, QDialog, QMessageBox {
    background-color: #f8fafc;
    color: #0f172a;
}

QWidget {
    color: #0f172a;
    font-family: 'Segoe UI', -apple-system, BlinkMacSystemFont, 'SF Pro Display', Roboto, sans-serif;
    font-size: 12px;
}

/* Containers & Structural Panels */
QFrame#top_box, QFrame#control_box, QFrame.MetricCard {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
}

QFrame#meta_frame {
    background-color: #f8fafc;
    border: 1px dashed #3b82f6;
    border-radius: 6px;
    padding: 6px;
}

QSplitter::handle {
    background-color: #e2e8f0;
}
QSplitter::handle:horizontal {
    width: 6px;
}
QSplitter::handle:vertical {
    height: 6px;
}

/* Results Tree Widget */
QTreeWidget {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
    font-size: 12px;
    color: #0f172a;
    alternate-background-color: #f8fafc;
    padding: 2px;
}

QTreeWidget::item {
    padding: 6px 4px;
    border-bottom: 1px solid #f1f5f9;
    border-radius: 4px;
}

QTreeWidget::item:hover {
    background-color: #f1f5f9;
}

QTreeWidget::item:selected {
    background-color: #dbeafe;
    color: #1e3a8a;
}

/* Table Widgets (Archive Table, Metadata Comparison, Restore History) */
QTableWidget, QTableView {
    background-color: #ffffff;
    color: #0f172a;
    gridline-color: #e2e8f0;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    selection-background-color: #dbeafe;
    selection-color: #1e3a8a;
    alternate-background-color: #f8fafc;
}

QTableWidget::item, QTableView::item {
    padding: 5px 8px;
    color: #0f172a;
    border-bottom: 1px solid #f1f5f9;
}

QTableWidget::item:hover, QTableView::item:hover {
    background-color: #f1f5f9;
}

QTableWidget::item:selected, QTableView::item:selected {
    background-color: #dbeafe;
    color: #1e3a8a;
}

QHeaderView::section {
    background-color: #f1f5f9;
    color: #334155;
    font-weight: 600;
    font-size: 11px;
    padding: 6px 8px;
    border: none;
    border-bottom: 1px solid #cbd5e1;
    border-right: 1px solid #cbd5e1;
}

QTableCornerButton::section {
    background-color: #f1f5f9;
    border: none;
    border-bottom: 1px solid #cbd5e1;
    border-right: 1px solid #cbd5e1;
}

/* Form Inputs, Combos, SpinBoxes */
QLineEdit, QComboBox, QSpinBox {
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 5px 8px;
    background-color: #ffffff;
    color: #0f172a;
    font-size: 12px;
    min-height: 22px;
}

QLineEdit:focus, QComboBox:focus, QSpinBox:focus {
    border: 1px solid #2563eb;
    background-color: #ffffff;
}

QComboBox::drop-down {
    subcontrol-origin: padding;
    subcontrol-position: top right;
    width: 22px;
    border-left: 1px solid #e2e8f0;
    border-top-right-radius: 6px;
    border-bottom-right-radius: 6px;
}

QComboBox QAbstractItemView {
    background-color: #ffffff;
    color: #0f172a;
    selection-background-color: #dbeafe;
    selection-color: #1e3a8a;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 4px;
}

/* PushButtons (General) */
QPushButton {
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 5px 12px;
    background-color: #ffffff;
    color: #1e293b;
    font-size: 12px;
    font-weight: 500;
    min-height: 22px;
}

QPushButton:hover {
    background-color: #f1f5f9;
    border-color: #94a3b8;
    color: #0f172a;
}

QPushButton:pressed {
    background-color: #e2e8f0;
}

QPushButton:disabled {
    background-color: #f8fafc;
    color: #94a3b8;
    border-color: #e2e8f0;
}

/* Preview Toolbar Buttons (−, +, W, P, ↗, 📁) */
QPushButton#preview_tool_btn {
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    background-color: #f8fafc;
    color: #1e293b;
    font-size: 13px;
    font-weight: bold;
    min-height: 24px;
    max-height: 24px;
    min-width: 24px;
    max-width: 24px;
    padding: 0px;
}

QPushButton#preview_tool_btn:hover {
    background-color: #dbeafe;
    border-color: #3b82f6;
    color: #1e40af;
}

QPushButton#preview_tool_btn:pressed {
    background-color: #bfdbfe;
}

/* CheckBoxes */
QCheckBox {
    color: #0f172a;
    font-size: 12px;
    spacing: 6px;
}

QCheckBox::indicator {
    width: 16px;
    height: 16px;
    border: 1px solid #cbd5e1;
    border-radius: 4px;
    background-color: #ffffff;
}

QCheckBox::indicator:hover {
    border-color: #2563eb;
}

QCheckBox::indicator:checked {
    background-color: #2563eb;
    border-color: #2563eb;
}

/* Scroll Areas & Viewports */
QScrollArea {
    background-color: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
}

QScrollArea#preview_scroll_area {
    background-color: #e2e8f0;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
}

QScrollArea#filmstrip_scroll {
    background-color: #f8fafc;
    border: 1px dashed #cbd5e1;
    border-radius: 6px;
}

/* Filmstrip Cards */
QFrame#filmstrip_card {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
}

QFrame#filmstrip_card:hover {
    background-color: #f1f5f9;
    border-color: #94a3b8;
}

/* Text & Code Editors */
QPlainTextEdit, QTextEdit {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    padding: 6px;
    font-family: Consolas, 'Courier New', monospace;
    font-size: 12px;
    selection-background-color: #dbeafe;
    selection-color: #1e3a8a;
}

QPlainTextEdit#preview_hex_editor {
    background-color: #0f172a;
    color: #38bdf8;
    border: 1px solid #cbd5e1;
}

/* Labels & Status */
QLabel {
    color: #0f172a;
}

QLabel#preview_title_label {
    font-weight: bold;
    font-size: 12px;
    color: #0f172a;
}

QLabel#preview_info_label {
    font-size: 11px;
    color: #475569;
}

QLabel#preview_extra_info {
    font-size: 11px;
    color: #0284c7;
}

QLabel#preview_image_label {
    background-color: #f1f5f9;
    color: #64748b;
    font-size: 13px;
    padding: 8px;
}

QLabel#match_badge {
    background-color: #e0f2fe;
    color: #0369a1;
    padding: 3px 8px;
    border-radius: 4px;
    font-size: 11px;
    font-weight: 600;
}

/* Office Document Inspector Card */
QFrame#preview_office_card {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 8px;
}

QLabel#preview_office_title {
    font-size: 14px;
    font-weight: bold;
    color: #0f172a;
}

QTableWidget#preview_office_table {
    background-color: #f8fafc;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    color: #0f172a;
}

/* ScrollBars */
QScrollBar:vertical {
    background: #f1f5f9;
    width: 10px;
    margin: 0px;
    border-radius: 5px;
}

QScrollBar::handle:vertical {
    background: #cbd5e1;
    min-height: 24px;
    border-radius: 5px;
}

QScrollBar::handle:vertical:hover {
    background: #94a3b8;
}

QScrollBar::add-line:vertical, QScrollBar::sub-line:vertical {
    height: 0px;
}

QScrollBar:horizontal {
    background: #f1f5f9;
    height: 10px;
    margin: 0px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal {
    background: #cbd5e1;
    min-width: 24px;
    border-radius: 5px;
}

QScrollBar::handle:horizontal:hover {
    background: #94a3b8;
}

QScrollBar::add-line:horizontal, QScrollBar::sub-line:horizontal {
    height: 0px;
}

/* Progress Bar */
QProgressBar {
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    background-color: #e2e8f0;
    text-align: center;
    color: #0f172a;
    font-size: 11px;
    font-weight: bold;
}

QProgressBar::chunk {
    background: qlineargradient(x1:0, y1:0, x2:1, y2:0, stop:0 #2563eb, stop:1 #60a5fa);
    border-radius: 5px;
}

QListWidget {
    background-color: #ffffff;
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    color: #0f172a;
    font-size: 12px;
}

QListWidget::item {
    padding: 4px;
}

QListWidget::item:selected {
    background-color: #dbeafe;
    color: #1e3a8a;
}

QScrollArea#chips_scroll {
    border: 1px solid #cbd5e1;
    border-radius: 6px;
    background-color: #ffffff;
}

/* Menus */
QMenu {
    background-color: #ffffff;
    color: #0f172a;
    border: 1px solid #cbd5e1;
    padding: 4px;
    border-radius: 6px;
}

QMenu::item {
    padding: 6px 20px;
    border-radius: 4px;
}

QMenu::item:selected {
    background-color: #dbeafe;
    color: #1e3a8a;
}

QMenu::separator {
    height: 1px;
    background-color: #e2e8f0;
    margin: 4px 0px;
}
"""


def apply_theme(widget, theme_name: str = "dark"):
    """Applies either dark or light theme to the window/application."""
    from PySide6.QtWidgets import QApplication
    mode = theme_name.lower()
    qss = LIGHT_THEME_QSS if mode == "light" else DARK_THEME_QSS
    widget.setStyleSheet(qss)
    app = QApplication.instance()
    if app:
        app.setStyleSheet(qss)
