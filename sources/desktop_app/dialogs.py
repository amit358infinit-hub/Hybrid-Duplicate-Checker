"""
Dialogs for Fast PDF Duplicate Finder:
- RestoreDialog: View and restore files from '_deleted_pdfs' folder.
- ConfirmDeleteDialog: Clear safety prompt before moving files.
"""
from __future__ import annotations

import os
import subprocess
from typing import List, Set, Union

from PySide6.QtCore import Qt
from PySide6.QtWidgets import (
    QDialog,
    QFileDialog,
    QHBoxLayout,
    QHeaderView,
    QLabel,
    QLineEdit,
    QListWidget,
    QMessageBox,
    QPushButton,
    QTableWidget,
    QTableWidgetItem,
    QVBoxLayout,
)

from safe_trash import DeletedRecord, SafeTrashManager


class RestoreDialog(QDialog):
    """Allows user to view previously deleted files across all scanned folders and restore them."""

    def __init__(self, root_dirs: Union[str, List[str]], parent=None):
        super().__init__(parent)
        if isinstance(root_dirs, str):
            self.root_dirs = [root_dirs] if root_dirs else []
        else:
            self.root_dirs = [r for r in root_dirs if r]

        self.setWindowTitle("Restore Deleted Files — Safe History (Multi-Folder)")
        self.resize(900, 520)
        self._records: List[DeletedRecord] = []
        self.restored_count: int = 0
        self.restored_paths: Set[str] = set()

        self._init_ui()
        self.load_records()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        # Header Info
        head_label = QLabel(
            "<b>Deleted Files History:</b><br>"
            "These files were safely moved to the <code>_deleted_files</code> folder. "
            "You can restore any file back to its exact original location anytime."
        )
        head_label.setWordWrap(True)
        layout.addWidget(head_label)

        # Table
        self.table = QTableWidget()
        self.table.setColumnCount(5)
        self.table.setHorizontalHeaderLabels([
            "Select", "Filename", "Original Location", "Date Moved", "Size"
        ])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(4, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        # Selection helpers
        sel_bar = QHBoxLayout()
        btn_sel_all = QPushButton("Select All")
        btn_sel_all.clicked.connect(self._select_all)
        btn_unsel_all = QPushButton("Unselect All")
        btn_unsel_all.clicked.connect(self._unselect_all)
        sel_bar.addWidget(btn_sel_all)
        sel_bar.addWidget(btn_unsel_all)
        sel_bar.addStretch(1)

        btn_open_folder = QPushButton("📁 Open '_deleted_files' Folder")
        btn_open_folder.clicked.connect(self._open_deleted_dir)
        sel_bar.addWidget(btn_open_folder)

        layout.addLayout(sel_bar)

        # Bottom Buttons
        btn_box = QHBoxLayout()
        self.btn_restore_selected = QPushButton("♻️ Restore Selected")
        self.btn_restore_selected.setStyleSheet(
            "background-color: #10b981; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px;"
        )
        self.btn_restore_selected.clicked.connect(self._restore_selected)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)

        btn_box.addWidget(self.btn_restore_selected)
        btn_box.addStretch(1)
        btn_box.addWidget(btn_close)
        layout.addLayout(btn_box)

    def load_records(self):
        all_records: List[DeletedRecord] = []
        seen_ids = set()

        # Gather manifests from all known root directories
        for root in self.root_dirs:
            if not root or not os.path.isdir(root):
                continue
            manifests = SafeTrashManager.load_manifest(root)
            for rec in manifests:
                if rec.id not in seen_ids:
                    seen_ids.add(rec.id)
                    all_records.append(rec)

        self._records = all_records
        self.table.setRowCount(len(self._records))

        for row, rec in enumerate(self._records):
            # Checkbox item
            item_chk = QTableWidgetItem()
            item_chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item_chk.setCheckState(Qt.Unchecked)
            self.table.setItem(row, 0, item_chk)

            # Name
            self.table.setItem(row, 1, QTableWidgetItem(rec.filename))

            # Original path
            self.table.setItem(row, 2, QTableWidgetItem(rec.original_path))

            # Date
            self.table.setItem(row, 3, QTableWidgetItem(rec.deleted_time))

            # Size
            size_mb = rec.size / (1024 * 1024)
            self.table.setItem(row, 4, QTableWidgetItem(f"{size_mb:.2f} MB"))

    def _select_all(self):
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(Qt.Checked)

    def _unselect_all(self):
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(Qt.Unchecked)

    def _restore_selected(self):
        selected_records: List[DeletedRecord] = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.checkState() == Qt.Checked:
                selected_records.append(self._records[r])

        if not selected_records:
            QMessageBox.information(self, "No Selection", "Please check at least one file to restore.")
            return

        success, errors = SafeTrashManager.restore_multiple(selected_records, self.root_dirs)
        if success > 0:
            self.restored_count += success
            for r in selected_records:
                self.restored_paths.add(r.original_path)

        msg = f"Successfully restored {success} of {len(selected_records)} file(s)."
        if errors:
            msg += "\n\nErrors encountered:\n" + "\n".join(errors[:5])
        QMessageBox.information(self, "Restore Complete", msg)

        # Refresh table
        self.load_records()

    def _open_deleted_dir(self):
        opened = False
        for root in self.root_dirs:
            trash_dir = SafeTrashManager.get_trash_dir(root)
            if os.path.isdir(trash_dir):
                try:
                    os.startfile(trash_dir)
                    opened = True
                    break
                except Exception:
                    subprocess.run(["explorer", os.path.normpath(trash_dir)])
                    opened = True
                    break

        if not opened:
            QMessageBox.information(self, "Folder Empty", "No '_deleted_pdfs' folder found in the selected directories.")


class ExcludeFoldersDialog(QDialog):
    """Allows user to configure folders or folder name patterns to ignore during scan."""

    def __init__(self, current_excludes: Set[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Configure Excluded Folders & Patterns")
        self.resize(580, 420)
        self.setAcceptDrops(True)
        self.excludes: Set[str] = set(current_excludes)

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        lbl_info = QLabel(
            "<b>Excluded Folders & Patterns:</b><br>"
            "Any directory matching these folder names or absolute paths will be skipped during scanning.<br>"
            "<i>(Standard system directories like $recycle.bin, .git, and _deleted_files are always excluded by default).</i>"
        )
        lbl_info.setWordWrap(True)
        layout.addWidget(lbl_info)

        # Input & Add Bar (Type directly or Browse)
        add_bar = QHBoxLayout()
        add_bar.setSpacing(6)
        self.edit_input = QLineEdit()
        self.edit_input.setPlaceholderText("Type folder name/keyword (e.g. backup, node_modules, temp) and press Enter...")
        self.edit_input.returnPressed.connect(self._add_from_input)

        btn_add_inline = QPushButton("➕ Add")
        btn_add_inline.clicked.connect(self._add_from_input)

        btn_browse_path = QPushButton("📁 Browse...")
        btn_browse_path.clicked.connect(self._browse_folder_path)

        add_bar.addWidget(self.edit_input, stretch=1)
        add_bar.addWidget(btn_add_inline)
        add_bar.addWidget(btn_browse_path)
        layout.addLayout(add_bar)

        # Excluded List Widget
        self.list_widget = QListWidget()
        for item in sorted(self.excludes):
            self.list_widget.addItem(item)
        layout.addWidget(self.list_widget, stretch=1)

        # Manage Buttons Bar
        manage_bar = QHBoxLayout()
        btn_remove = QPushButton("🗑️ Remove Selected")
        btn_remove.clicked.connect(self._remove_selected)
        btn_clear = QPushButton("Clear All")
        btn_clear.clicked.connect(self._clear_all)

        manage_bar.addWidget(btn_remove)
        manage_bar.addWidget(btn_clear)
        manage_bar.addStretch(1)
        layout.addLayout(manage_bar)

        # Bottom OK / Cancel
        bottom_bar = QHBoxLayout()
        btn_ok = QPushButton("Save & Apply")
        btn_ok.setStyleSheet("background-color: #2563eb; color: white; font-weight: bold; padding: 6px 18px; border-radius: 4px;")
        btn_ok.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        bottom_bar.addStretch(1)
        bottom_bar.addWidget(btn_ok)
        bottom_bar.addWidget(btn_cancel)
        layout.addLayout(bottom_bar)

    def _add_from_input(self):
        text = self.edit_input.text().strip().lower()
        if text:
            if text not in self.excludes:
                self.excludes.add(text)
                self.list_widget.addItem(text)
            self.edit_input.clear()

    def _browse_folder_path(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Exclude")
        if folder:
            val = os.path.normpath(folder).lower()
            if val not in self.excludes:
                self.excludes.add(val)
                self.list_widget.addItem(val)

    def _remove_selected(self):
        for item in self.list_widget.selectedItems():
            text = item.text()
            self.excludes.discard(text)
            self.list_widget.takeItem(self.list_widget.row(item))

    def _clear_all(self):
        self.excludes.clear()
        self.list_widget.clear()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        for url in event.mimeData().urls():
            p = url.toLocalFile()
            if os.path.isdir(p):
                val = os.path.normpath(p).lower()
                if val not in self.excludes:
                    self.excludes.add(val)
                    self.list_widget.addItem(val)
        event.acceptProposedAction()

    def accept(self):
        # Synchronize self.excludes from list widget items
        self.excludes = {
            self.list_widget.item(i).text().strip().lower()
            for i in range(self.list_widget.count())
            if self.list_widget.item(i).text().strip()
        }
        super().accept()


class CorruptFilesDialog(QDialog):
    """Allows user to inspect 0-byte or corrupted files discovered during scan, and safely delete them."""

    def __init__(self, corrupt_files: List, root_dirs: Union[str, List[str]], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Corrupted / 0-Byte Files")
        self.resize(780, 420)
        self.corrupt_files = list(corrupt_files)
        if isinstance(root_dirs, str):
            self.root_dirs = [root_dirs] if root_dirs else []
        else:
            self.root_dirs = [r for r in root_dirs if r]
        self.deleted_count = 0

        self._init_ui()
        self._load_table()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        lbl_info = QLabel(
            "<b>Corrupted / Empty Files Detected:</b><br>"
            "These files have 0-byte size or invalid/damaged headers. "
            "You can review them and safely move them to <code>_deleted_files</code>."
        )
        lbl_info.setWordWrap(True)
        layout.addWidget(lbl_info)

        self.table = QTableWidget()
        self.table.setColumnCount(4)
        self.table.setHorizontalHeaderLabels(["Select", "Filename", "Folder", "Issue / Size"])
        self.table.horizontalHeader().setSectionResizeMode(0, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(1, QHeaderView.ResizeToContents)
        self.table.horizontalHeader().setSectionResizeMode(2, QHeaderView.Stretch)
        self.table.horizontalHeader().setSectionResizeMode(3, QHeaderView.ResizeToContents)
        self.table.setSelectionBehavior(QTableWidget.SelectRows)
        layout.addWidget(self.table)

        # Selection bar
        sel_bar = QHBoxLayout()
        btn_sel_all = QPushButton("Select All")
        btn_sel_all.clicked.connect(self._select_all)
        btn_unsel_all = QPushButton("Unselect All")
        btn_unsel_all.clicked.connect(self._unselect_all)
        sel_bar.addWidget(btn_sel_all)
        sel_bar.addWidget(btn_unsel_all)
        sel_bar.addStretch(1)
        layout.addLayout(sel_bar)

        # Bottom buttons
        btn_box = QHBoxLayout()
        self.btn_delete = QPushButton("🗑️ Move Checked to '_deleted_files'")
        self.btn_delete.setStyleSheet(
            "background-color: #dc2626; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px;"
        )
        self.btn_delete.clicked.connect(self._delete_checked)

        btn_close = QPushButton("Close")
        btn_close.clicked.connect(self.accept)

        btn_box.addWidget(self.btn_delete)
        btn_box.addStretch(1)
        btn_box.addWidget(btn_close)
        layout.addLayout(btn_box)

    def _load_table(self):
        self.table.setRowCount(len(self.corrupt_files))
        for row, finfo in enumerate(self.corrupt_files):
            item_chk = QTableWidgetItem()
            item_chk.setFlags(Qt.ItemIsUserCheckable | Qt.ItemIsEnabled)
            item_chk.setCheckState(Qt.Checked)
            self.table.setItem(row, 0, item_chk)

            self.table.setItem(row, 1, QTableWidgetItem(getattr(finfo, 'filename', os.path.basename(getattr(finfo, 'path', '')))))
            self.table.setItem(row, 2, QTableWidgetItem(getattr(finfo, 'folder', os.path.dirname(getattr(finfo, 'path', '')))))
            issue = getattr(finfo, 'issue', '') or "0-Byte File"
            self.table.setItem(row, 3, QTableWidgetItem(issue))

    def _select_all(self):
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(Qt.Checked)

    def _unselect_all(self):
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item:
                item.setCheckState(Qt.Unchecked)

    def _delete_checked(self):
        paths_to_delete = []
        indices_to_remove = []
        for r in range(self.table.rowCount()):
            item = self.table.item(r, 0)
            if item and item.checkState() == Qt.Checked:
                finfo = self.corrupt_files[r]
                paths_to_delete.append(getattr(finfo, 'path', str(finfo)))
                indices_to_remove.append(r)

        if not paths_to_delete:
            QMessageBox.information(self, "No Selection", "Please check at least one file to move.")
            return

        confirm = QMessageBox.question(
            self,
            "Confirm Safe Move",
            f"Are you sure you want to move {len(paths_to_delete)} file(s) to '_deleted_files'?",
            QMessageBox.Yes | QMessageBox.No,
            QMessageBox.No
        )
        if confirm != QMessageBox.Yes:
            return

        successful_paths = []
        success, errors, _ = SafeTrashManager.safe_delete_files(
            paths_to_delete, root_dirs=self.root_dirs, successful_paths=successful_paths
        )
        self.deleted_count += success

        succeeded = {os.path.normcase(os.path.abspath(path)) for path in successful_paths}
        for idx in sorted(indices_to_remove, reverse=True):
            finfo = self.corrupt_files[idx]
            path = getattr(finfo, 'path', str(finfo))
            if os.path.normcase(os.path.abspath(path)) in succeeded:
                del self.corrupt_files[idx]
        self._load_table()

        msg = f"Successfully moved {success} file(s) to '_deleted_files'."
        if errors:
            msg += "\n\nErrors encountered:\n" + "\n".join(errors[:5])
        QMessageBox.information(self, "Done", msg)


class ManageFoldersDialog(QDialog):
    """Allows user to inspect, add, remove, and manage multiple scan directories easily."""

    def __init__(self, current_folders: List[str], parent=None):
        super().__init__(parent)
        self.setWindowTitle("Manage Target Folders")
        self.resize(600, 380)
        self.setAcceptDrops(True)
        self.folders: List[str] = [f for f in current_folders if f]

        self._init_ui()

    def _init_ui(self):
        layout = QVBoxLayout(self)
        layout.setSpacing(10)

        lbl_info = QLabel(
            "<b>Target Scan Folders:</b><br>"
            "Add one or more directories to scan for duplicate files. "
            "You can also drag and drop folders directly from File Explorer into this window."
        )
        lbl_info.setWordWrap(True)
        layout.addWidget(lbl_info)

        self.list_widget = QListWidget()
        for f in self.folders:
            self.list_widget.addItem(f)
        layout.addWidget(self.list_widget, stretch=1)

        btn_bar = QHBoxLayout()
        btn_add = QPushButton("➕ Add Folder...")
        btn_add.clicked.connect(self._add_folder)
        btn_remove = QPushButton("🗑️ Remove Selected")
        btn_remove.clicked.connect(self._remove_selected)
        btn_clear = QPushButton("Clear All")
        btn_clear.clicked.connect(self._clear_all)

        btn_bar.addWidget(btn_add)
        btn_bar.addWidget(btn_remove)
        btn_bar.addWidget(btn_clear)
        layout.addLayout(btn_bar)

        bottom_bar = QHBoxLayout()
        btn_save = QPushButton("Save & Close")
        btn_save.setStyleSheet("background-color: #2563eb; color: white; font-weight: bold; padding: 6px 16px; border-radius: 4px;")
        btn_save.clicked.connect(self.accept)
        btn_cancel = QPushButton("Cancel")
        btn_cancel.clicked.connect(self.reject)

        bottom_bar.addStretch(1)
        bottom_bar.addWidget(btn_save)
        bottom_bar.addWidget(btn_cancel)
        layout.addLayout(bottom_bar)

    def _add_folder(self):
        folder = QFileDialog.getExistingDirectory(self, "Select Folder to Add")
        if folder:
            norm = os.path.normpath(folder)
            if norm not in self.folders:
                self.folders.append(norm)
                self.list_widget.addItem(norm)

    def _remove_selected(self):
        for item in self.list_widget.selectedItems():
            text = item.text()
            if text in self.folders:
                self.folders.remove(text)
            self.list_widget.takeItem(self.list_widget.row(item))

    def _clear_all(self):
        self.folders.clear()
        self.list_widget.clear()

    def dragEnterEvent(self, event):
        if event.mimeData().hasUrls():
            event.acceptProposedAction()
        else:
            event.ignore()

    def dropEvent(self, event):
        urls = event.mimeData().urls()
        for url in urls:
            path = url.toLocalFile()
            if os.path.isdir(path):
                norm = os.path.normpath(path)
                if norm not in self.folders:
                    self.folders.append(norm)
                    self.list_widget.addItem(norm)
        event.acceptProposedAction()
