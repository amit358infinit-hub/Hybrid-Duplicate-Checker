"""
Interactive Category & Granular Extension Checklist Picker Widget.
All Files Duplicate Checker — Studio Edition.

Features high-contrast Dark and Light themes with zero washed-out fonts.
"""
from __future__ import annotations

from typing import Dict, List, Optional, Set, Tuple

from PySide6.QtCore import Qt, QPoint, Signal, QEvent
from PySide6.QtGui import QColor, QFont, QCursor
from PySide6.QtWidgets import (
    QWidget,
    QFrame,
    QHBoxLayout,
    QVBoxLayout,
    QPushButton,
    QLabel,
    QCheckBox,
    QScrollArea,
    QGraphicsDropShadowEffect,
    QApplication,
)

from category_config import (
    CATEGORY_ALL,
    CATEGORY_PDF,
    CATEGORY_TAXONOMY,
    ExtensionFilterState,
)


class CategoryListItem(QWidget):
    """Left column category row with hover tracking and badge count."""
    hovered = Signal(str)
    clicked = Signal(str)

    def __init__(self, cat_key: str, name: str, icon: str, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.cat_key = cat_key
        self.theme = theme
        self.is_active = False
        self._init_ui(name, icon)

    def _init_ui(self, name: str, icon: str):
        self.setAttribute(Qt.WA_Hover, True)
        self.setCursor(Qt.PointingHandCursor)
        lay = QHBoxLayout(self)
        lay.setContentsMargins(10, 8, 10, 8)
        lay.setSpacing(8)

        self.lbl_icon = QLabel(icon)
        self.lbl_icon.setStyleSheet("font-size: 15px; background: transparent;")
        lay.addWidget(self.lbl_icon)

        self.lbl_name = QLabel(name)
        self.lbl_name.setStyleSheet("font-weight: 600; font-size: 12px; background: transparent;")
        lay.addWidget(self.lbl_name, 1)

        self.lbl_badge = QLabel("0")
        lay.addWidget(self.lbl_badge)
        self.set_active_state(False, self.theme)

    def set_badge(self, selected_count: int, total_count: int, is_all_mode: bool):
        if is_all_mode:
            self.lbl_badge.setText("All")
            self.lbl_badge.setStyleSheet("""
                QLabel {
                    background: rgba(37, 99, 235, 0.35);
                    color: #93c5fd;
                    border: 1px solid rgba(59, 130, 246, 0.5);
                    border-radius: 9px;
                    padding: 1px 7px;
                    font-size: 10px;
                    font-weight: 700;
                }
            """)
        elif selected_count > 0:
            self.lbl_badge.setText(f"{selected_count}/{total_count}")
            self.lbl_badge.setStyleSheet("""
                QLabel {
                    background: rgba(16, 185, 129, 0.3);
                    color: #34d399;
                    border: 1px solid rgba(16, 185, 129, 0.5);
                    border-radius: 9px;
                    padding: 1px 7px;
                    font-size: 10px;
                    font-weight: 700;
                }
            """)
        else:
            self.lbl_badge.setText("0")
            bg = "rgba(148, 163, 184, 0.12)" if self.theme == "dark" else "rgba(100, 116, 139, 0.12)"
            fg = "#94a3b8" if self.theme == "dark" else "#64748b"
            self.lbl_badge.setStyleSheet(f"""
                QLabel {{
                    background: {bg};
                    color: {fg};
                    border-radius: 9px;
                    padding: 1px 7px;
                    font-size: 10px;
                    font-weight: 600;
                }}
            """)

    def set_active_state(self, active: bool, theme: str = "dark"):
        self.is_active = active
        self.theme = theme
        is_dark = self.theme == "dark"

        if active:
            bg = "#2563eb"
            fg = "#ffffff"
            hover_bg = "#1d4ed8"
        else:
            bg = "transparent"
            fg = "#f8fafc" if is_dark else "#0f172a"
            hover_bg = "rgba(59, 130, 246, 0.15)" if is_dark else "rgba(37, 99, 235, 0.1)"

        self.setStyleSheet(f"""
            CategoryListItem {{
                background-color: {bg};
                border-radius: 6px;
            }}
            CategoryListItem:hover {{
                background-color: {hover_bg};
            }}
            QLabel {{
                color: {fg};
                background: transparent;
            }}
        """)

    def enterEvent(self, event):
        self.hovered.emit(self.cat_key)
        super().enterEvent(event)

    def mousePressEvent(self, event):
        if event.button() == Qt.LeftButton:
            self.clicked.emit(self.cat_key)
        super().mousePressEvent(event)


class ExtensionRowWidget(QWidget):
    """Right column individual extension row with high contrast checkbox, description and 'Only' button."""
    toggled = Signal(str, bool)
    only_clicked = Signal(str)

    def __init__(self, ext: str, desc: str, is_checked: bool, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.ext = ext
        self.theme = theme
        self._init_ui(ext, desc, is_checked)
        self.apply_theme(self.theme)

    def _init_ui(self, ext: str, desc: str, is_checked: bool):
        lay = QHBoxLayout(self)
        lay.setContentsMargins(8, 4, 8, 4)
        lay.setSpacing(10)

        self.chk = QCheckBox(ext)
        self.chk.setChecked(is_checked)
        self.chk.setCursor(Qt.PointingHandCursor)
        self.chk.toggled.connect(lambda c: self.toggled.emit(self.ext, c))
        lay.addWidget(self.chk)

        self.lbl_desc = QLabel(desc)
        lay.addWidget(self.lbl_desc, 1)

        self.btn_only = QPushButton("Only")
        self.btn_only.setToolTip(f"Scan strictly {ext} only (uncheck all other extensions)")
        self.btn_only.setFixedSize(44, 22)
        self.btn_only.setCursor(Qt.PointingHandCursor)
        self.btn_only.clicked.connect(lambda: self.only_clicked.emit(self.ext))
        lay.addWidget(self.btn_only)

    def set_checked_silent(self, checked: bool):
        self.chk.blockSignals(True)
        self.chk.setChecked(checked)
        self.chk.blockSignals(False)

    def apply_theme(self, theme: str):
        self.theme = theme.lower()
        is_dark = self.theme == "dark"

        row_hover = "rgba(59, 130, 246, 0.18)" if is_dark else "rgba(37, 99, 235, 0.12)"
        fg_title = "#f8fafc" if is_dark else "#0f172a"
        fg_desc = "#cbd5e1" if is_dark else "#475569"

        # Checkbox indicator styling
        chk_bg = "#1e293b" if is_dark else "#f8fafc"
        chk_border = "#64748b" if is_dark else "#94a3b8"
        chk_border_hover = "#60a5fa" if is_dark else "#2563eb"

        # Only button styling
        btn_bg = "#1e293b" if is_dark else "#eff6ff"
        btn_border = "#3b82f6" if is_dark else "#93c5fd"
        btn_fg = "#93c5fd" if is_dark else "#1d4ed8"

        self.setStyleSheet(f"""
            ExtensionRowWidget {{
                background-color: transparent;
                border-radius: 5px;
            }}
            ExtensionRowWidget:hover {{
                background-color: {row_hover};
            }}
            QCheckBox {{
                color: {fg_title};
                font-weight: 700;
                font-size: 12px;
                spacing: 8px;
                background: transparent;
            }}
            QCheckBox::indicator {{
                width: 17px;
                height: 17px;
                border-radius: 4px;
                border: 2px solid {chk_border};
                background-color: {chk_bg};
            }}
            QCheckBox::indicator:hover {{
                border: 2px solid {chk_border_hover};
            }}
            QCheckBox::indicator:checked {{
                background-color: #2563eb;
                border: 2px solid #3b82f6;
            }}
            QLabel {{
                color: {fg_desc};
                font-size: 11px;
                background: transparent;
            }}
            QPushButton {{
                background-color: {btn_bg};
                color: {btn_fg};
                border: 1px solid {btn_border};
                border-radius: 4px;
                font-size: 10px;
                font-weight: 700;
            }}
            QPushButton:hover {{
                background-color: #2563eb;
                color: #ffffff;
                border: 1px solid #2563eb;
            }}
        """)


class ExtensionFlyoutPopup(QFrame):
    """
    Cascading 2-column popup window:
    - Left Column: Categories list with active cursor hover tracking.
    - Right Column: Extension checklist with Select All / Deselect All / Only buttons.
    - Bottom Bar: Live count summary and Done button.
    """
    filter_changed = Signal()

    def __init__(self, filter_state: ExtensionFilterState, theme: str = "dark", parent=None):
        super().__init__(parent, Qt.Popup | Qt.FramelessWindowHint)
        self.filter_state = filter_state
        self.theme = theme
        self.active_category = self.filter_state.active_category or CATEGORY_PDF
        self.category_widgets: Dict[str, CategoryListItem] = {}
        self.extension_rows: List[ExtensionRowWidget] = []

        self.setFixedSize(560, 430)
        self.setAttribute(Qt.WA_DeleteOnClose, False)

        # Shadow effect for elevated floating menu feel
        shadow = QGraphicsDropShadowEffect(self)
        shadow.setBlurRadius(24)
        shadow.setColor(QColor(0, 0, 0, 180))
        shadow.setOffset(0, 8)
        self.setGraphicsEffect(shadow)

        self._init_ui()
        self.apply_theme(self.theme)

    def _init_ui(self):
        root_lay = QVBoxLayout(self)
        root_lay.setContentsMargins(0, 0, 0, 0)
        root_lay.setSpacing(0)

        # Header bar
        self.header = QFrame()
        self.header.setObjectName("popup_header")
        h_lay = QHBoxLayout(self.header)
        h_lay.setContentsMargins(14, 10, 14, 10)
        self.lbl_title = QLabel("📁 Select File Types to Scan")
        self.lbl_title.setStyleSheet("font-weight: 700; font-size: 13px; background: transparent;")
        h_lay.addWidget(self.lbl_title)
        h_lay.addStretch()

        self.btn_all_files = QPushButton("📑 All Files (*.*)")
        self.btn_all_files.setCursor(Qt.PointingHandCursor)
        self.btn_all_files.clicked.connect(self._on_select_all_files_mode)
        h_lay.addWidget(self.btn_all_files)
        root_lay.addWidget(self.header)

        # Main 2-column body
        self.body = QWidget()
        self.body.setObjectName("popup_body")
        b_lay = QHBoxLayout(self.body)
        b_lay.setContentsMargins(10, 8, 10, 8)
        b_lay.setSpacing(10)

        # Left Column: Category List
        self.left_col = QFrame()
        self.left_col.setObjectName("left_col")
        self.left_col.setFixedWidth(205)
        l_lay = QVBoxLayout(self.left_col)
        l_lay.setContentsMargins(6, 6, 6, 6)
        l_lay.setSpacing(4)

        for cat_key, cat_data in CATEGORY_TAXONOMY.items():
            item_widget = CategoryListItem(cat_key, cat_data["name"], cat_data["icon"], theme=self.theme, parent=self.left_col)
            item_widget.hovered.connect(self._on_category_hovered)
            item_widget.clicked.connect(self._on_category_hovered)
            l_lay.addWidget(item_widget)
            self.category_widgets[cat_key] = item_widget

        l_lay.addStretch()
        b_lay.addWidget(self.left_col)

        # Right Column: Extensions Checklist
        self.right_col = QFrame()
        self.right_col.setObjectName("right_col")
        r_lay = QVBoxLayout(self.right_col)
        r_lay.setContentsMargins(10, 8, 10, 8)
        r_lay.setSpacing(6)

        # Right column sub-header with Category Name & Select/Deselect buttons
        sub_hdr = QHBoxLayout()
        sub_hdr.setContentsMargins(2, 2, 2, 2)
        self.lbl_active_cat = QLabel("📕 PDFs & eBooks")
        self.lbl_active_cat.setStyleSheet("font-weight: 700; font-size: 12px; background: transparent;")
        sub_hdr.addWidget(self.lbl_active_cat)
        sub_hdr.addStretch()

        self.btn_cat_select_all = QPushButton("Select All")
        self.btn_cat_select_all.setCursor(Qt.PointingHandCursor)
        self.btn_cat_select_all.clicked.connect(self._on_category_select_all)
        sub_hdr.addWidget(self.btn_cat_select_all)

        self.btn_cat_deselect_all = QPushButton("Deselect All")
        self.btn_cat_deselect_all.setCursor(Qt.PointingHandCursor)
        self.btn_cat_deselect_all.clicked.connect(self._on_category_deselect_all)
        sub_hdr.addWidget(self.btn_cat_deselect_all)
        r_lay.addLayout(sub_hdr)

        # Scroll area for extension items
        self.scroll_area = QScrollArea()
        self.scroll_area.setObjectName("popup_scroll")
        self.scroll_area.setWidgetResizable(True)
        self.scroll_area.setFrameShape(QFrame.NoFrame)
        self.scroll_area.viewport().setAutoFillBackground(False)

        self.scroll_content = QWidget()
        self.scroll_content.setObjectName("popup_scroll_content")
        self.scroll_layout = QVBoxLayout(self.scroll_content)
        self.scroll_layout.setContentsMargins(2, 2, 2, 2)
        self.scroll_layout.setSpacing(3)
        self.scroll_layout.addStretch()
        self.scroll_area.setWidget(self.scroll_content)
        r_lay.addWidget(self.scroll_area, 1)

        b_lay.addWidget(self.right_col, 1)
        root_lay.addWidget(self.body, 1)

        # Bottom Bar: Summary & Close Button
        self.bottom_bar = QFrame()
        self.bottom_bar.setObjectName("bottom_bar")
        btm_lay = QHBoxLayout(self.bottom_bar)
        btm_lay.setContentsMargins(14, 8, 14, 8)

        self.lbl_summary = QLabel("Summary: All Files (*.*)")
        self.lbl_summary.setStyleSheet("font-size: 11px; font-weight: 600; background: transparent;")
        btm_lay.addWidget(self.lbl_summary, 1)

        self.btn_done = QPushButton("Apply / Done")
        self.btn_done.setCursor(Qt.PointingHandCursor)
        self.btn_done.clicked.connect(self.close)
        btm_lay.addWidget(self.btn_done)
        root_lay.addWidget(self.bottom_bar)

        self._refresh_category_badges()
        self._load_category_extensions(self.active_category)

    def _on_category_hovered(self, cat_key: str):
        if cat_key == self.active_category:
            return
        self.active_category = cat_key
        self.filter_state.active_category = cat_key
        for k, w in self.category_widgets.items():
            w.set_active_state(k == cat_key, self.theme)
        self._load_category_extensions(cat_key)

    def _load_category_extensions(self, cat_key: str):
        if cat_key not in CATEGORY_TAXONOMY:
            return

        cat_info = CATEGORY_TAXONOMY[cat_key]
        self.lbl_active_cat.setText(f"{cat_info['icon']} {cat_info['name']}")

        # Clear existing rows
        for r in self.extension_rows:
            r.deleteLater()
        self.extension_rows.clear()

        # Rebuild rows
        is_all = self.filter_state.is_all_files()
        for ext, desc, _ in cat_info["items"]:
            is_chk = self.filter_state.is_extension_selected(ext) if not is_all else True
            row = ExtensionRowWidget(ext, desc, is_chk, theme=self.theme, parent=self.scroll_content)
            row.toggled.connect(self._on_extension_toggled)
            row.only_clicked.connect(self._on_extension_only_clicked)
            # Insert before final stretch
            self.scroll_layout.insertWidget(self.scroll_layout.count() - 1, row)
            self.extension_rows.append(row)

        self._update_summary_label()

    def _on_extension_toggled(self, ext: str, checked: bool):
        if self.filter_state.is_all_files():
            self.filter_state.mode = "custom"
            self.filter_state.selected_extensions = set()
            for r in self.extension_rows:
                if r.ext == ext:
                    if checked:
                        self.filter_state.selected_extensions.add(ext.lower())
                elif r.chk.isChecked():
                    self.filter_state.selected_extensions.add(r.ext.lower())
        else:
            self.filter_state.set_extension_selected(ext, checked)

        self._refresh_category_badges()
        self._update_summary_label()
        self.filter_changed.emit()

    def _on_extension_only_clicked(self, ext: str):
        self.filter_state.select_only_extension(ext)
        for r in self.extension_rows:
            r.set_checked_silent(r.ext.lower() == ext.lower())
        self._refresh_category_badges()
        self._update_summary_label()
        self.filter_changed.emit()

    def _on_category_select_all(self):
        self.filter_state.select_all_in_category(self.active_category)
        for r in self.extension_rows:
            r.set_checked_silent(True)
        self._refresh_category_badges()
        self._update_summary_label()
        self.filter_changed.emit()

    def _on_category_deselect_all(self):
        self.filter_state.deselect_all_in_category(self.active_category)
        for r in self.extension_rows:
            r.set_checked_silent(False)
        self._refresh_category_badges()
        self._update_summary_label()
        self.filter_changed.emit()

    def _on_select_all_files_mode(self):
        self.filter_state.set_all_files_mode()
        for r in self.extension_rows:
            r.set_checked_silent(True)
        self._refresh_category_badges()
        self._update_summary_label()
        self.filter_changed.emit()

    def _refresh_category_badges(self):
        is_all = self.filter_state.is_all_files()
        for cat_key, widget in self.category_widgets.items():
            sel = self.filter_state.get_selected_count_in_category(cat_key)
            tot = self.filter_state.get_total_count_in_category(cat_key)
            widget.set_badge(sel, tot, is_all)
            widget.set_active_state(cat_key == self.active_category, self.theme)

    def _update_summary_label(self):
        _, text, tooltip = self.filter_state.get_summary_display()
        self.lbl_summary.setText(f"Active: {text}")
        self.lbl_summary.setToolTip(tooltip)

    def apply_theme(self, theme_name: str):
        self.theme = theme_name.lower()
        is_dark = self.theme == "dark"

        # Cohesive color palette matching main app
        bg_main = "#0b132b" if is_dark else "#f8fafc"
        bg_header = "#1c2541" if is_dark else "#ffffff"
        bg_left = "#1c2541" if is_dark else "#ffffff"
        bg_right = "#0b132b" if is_dark else "#ffffff"
        border_col = "#334155" if is_dark else "#cbd5e1"
        fg_main = "#f8fafc" if is_dark else "#0f172a"
        fg_muted = "#cbd5e1" if is_dark else "#475569"

        # Action button colors
        btn_action_bg = "#1e293b" if is_dark else "#f1f5f9"
        btn_action_fg = "#cbd5e1" if is_dark else "#334155"
        btn_action_border = "#475569" if is_dark else "#cbd5e1"

        all_files_bg = "rgba(37, 99, 235, 0.25)" if is_dark else "rgba(37, 99, 235, 0.12)"
        all_files_fg = "#93c5fd" if is_dark else "#1d4ed8"
        all_files_border = "#3b82f6" if is_dark else "#60a5fa"

        self.setStyleSheet(f"""
            ExtensionFlyoutPopup {{
                background-color: {bg_main};
                border: 2px solid {border_col};
                border-radius: 10px;
            }}
            #popup_header {{
                background-color: {bg_header};
                border-top-left-radius: 8px;
                border-top-right-radius: 8px;
                border-bottom: 1px solid {border_col};
            }}
            #popup_body {{
                background-color: {bg_main};
            }}
            #left_col {{
                background-color: {bg_left};
                border-radius: 8px;
                border: 1px solid {border_col};
            }}
            #right_col {{
                background-color: {bg_right};
                border-radius: 8px;
                border: 1px solid {border_col};
            }}
            #popup_scroll, #popup_scroll > QWidget > QWidget, #popup_scroll_content {{
                background-color: transparent !important;
                border: none !important;
            }}
            #bottom_bar {{
                background-color: {bg_header};
                border-bottom-left-radius: 8px;
                border-bottom-right-radius: 8px;
                border-top: 1px solid {border_col};
            }}
            QLabel {{
                color: {fg_main};
                background: transparent;
            }}
            QPushButton#btn_done {{
                background-color: #2563eb;
                color: #ffffff;
                font-weight: 700;
                font-size: 11px;
                border-radius: 5px;
                padding: 5px 16px;
                border: none;
            }}
            QPushButton#btn_done:hover {{
                background-color: #1d4ed8;
            }}
        """)

        # Style All Files button
        self.btn_all_files.setStyleSheet(f"""
            QPushButton {{
                background: {all_files_bg};
                color: {all_files_fg};
                border: 1px solid {all_files_border};
                border-radius: 5px;
                padding: 4px 12px;
                font-weight: 700;
                font-size: 11px;
            }}
            QPushButton:hover {{
                background: #2563eb;
                color: #ffffff;
                border-color: #2563eb;
            }}
        """)

        # Style Select All / Deselect All sub-header buttons
        sub_btn_css = f"""
            QPushButton {{
                background: {btn_action_bg};
                color: {btn_action_fg};
                border: 1px solid {btn_action_border};
                border-radius: 4px;
                font-size: 10px;
                font-weight: 600;
                padding: 3px 8px;
            }}
            QPushButton:hover {{
                background: #334155;
                color: #ffffff;
                border-color: #60a5fa;
            }}
        """ if is_dark else f"""
            QPushButton {{
                background: {btn_action_bg};
                color: {btn_action_fg};
                border: 1px solid {btn_action_border};
                border-radius: 4px;
                font-size: 10px;
                font-weight: 600;
                padding: 3px 8px;
            }}
            QPushButton:hover {{
                background: #e2e8f0;
                color: #0f172a;
                border-color: #94a3b8;
            }}
        """
        self.btn_cat_select_all.setStyleSheet(sub_btn_css)
        self.btn_cat_deselect_all.setStyleSheet(sub_btn_css)

        # Style Apply / Done button
        self.btn_done.setObjectName("btn_done")
        self.btn_done.setStyleSheet("""
            QPushButton {
                background: #2563eb;
                color: #ffffff;
                font-weight: 700;
                font-size: 11px;
                border-radius: 5px;
                padding: 5px 16px;
                border: 1px solid #3b82f6;
            }
            QPushButton:hover {
                background: #1d4ed8;
            }
        """)

        # Update category items and child extension rows
        for w in self.category_widgets.values():
            w.set_active_state(w.cat_key == self.active_category, self.theme)

        for r in self.extension_rows:
            r.apply_theme(self.theme)


class CategoryExtensionPicker(QPushButton):
    """
    Main toolbar trigger button that displays active extension summary
    and opens the cascading ExtensionFlyoutPopup on click.
    """
    extensions_changed = Signal(object)  # Emits Set[str] or None

    def __init__(self, theme: str = "dark", parent=None):
        super().__init__(parent)
        self.current_theme = theme
        self.filter_state = ExtensionFilterState()
        self.popup = ExtensionFlyoutPopup(self.filter_state, theme=self.current_theme, parent=self)
        self.popup.filter_changed.connect(self._on_filter_changed)

        self.setCursor(Qt.PointingHandCursor)
        self.setFixedHeight(30)
        self.apply_theme(self.current_theme)
        self._update_button_display()
        self.clicked.connect(self.show_popup)

    def show_popup(self):
        """Positions and displays the flyout menu directly below this button."""
        self.popup.apply_theme(self.current_theme)
        self.popup._refresh_category_badges()
        self.popup._load_category_extensions(self.popup.active_category)

        # Map button bottom-left coordinates to global screen
        btn_pos = self.mapToGlobal(QPoint(0, self.height() + 4))
        self.popup.move(btn_pos)
        self.popup.show()
        self.popup.raise_()
        self.popup.activateWindow()

    def _on_filter_changed(self):
        self._update_button_display()
        self.extensions_changed.emit(self.get_selected_extensions())

    def _update_button_display(self):
        icon, short_text, tooltip = self.filter_state.get_summary_display()
        self.setText(f"{icon} Type: {short_text}  ▾")
        self.setToolTip(f"{tooltip}\nClick to toggle specific file categories and extensions.")

    def get_selected_extensions(self) -> Optional[Set[str]]:
        """Returns None for All Files (*.*) or Set of lowercase extensions (e.g. {'.pdf'})."""
        return self.filter_state.get_active_extensions()

    def set_only_extension(self, ext: str):
        """Programmatic shortcut for testing or presets."""
        self.filter_state.select_only_extension(ext)
        self._on_filter_changed()

    def set_all_files(self):
        """Programmatic shortcut for All Files."""
        self.filter_state.set_all_files_mode()
        self._on_filter_changed()

    def apply_theme(self, theme_name: str):
        self.current_theme = theme_name.lower()
        is_dark = self.current_theme == "dark"

        # High-contrast button styling
        bg = "#1e293b" if is_dark else "#ffffff"
        fg = "#f8fafc" if is_dark else "#0f172a"
        border = "#334155" if is_dark else "#cbd5e1"
        hover_bg = "#273549" if is_dark else "#f1f5f9"
        hover_border = "#3b82f6" if is_dark else "#2563eb"

        self.setStyleSheet(f"""
            CategoryExtensionPicker {{
                background-color: {bg};
                color: {fg};
                border: 1px solid {border};
                border-radius: 6px;
                padding: 4px 12px;
                font-weight: 600;
                font-size: 12px;
                text-align: left;
            }}
            CategoryExtensionPicker:hover {{
                background-color: {hover_bg};
                border-color: {hover_border};
            }}
            CategoryExtensionPicker:pressed {{
                background-color: {bg};
            }}
        """)
        self.popup.apply_theme(self.current_theme)
