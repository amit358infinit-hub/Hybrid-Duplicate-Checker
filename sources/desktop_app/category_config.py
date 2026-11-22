"""
Category & Extension Configuration & Taxonomy Model.
All Files Duplicate Checker — Studio Edition.
"""
from __future__ import annotations

import json
import os
from typing import Dict, List, Optional, Set, Tuple

# Standard Category Keys
CATEGORY_ALL = "all"
CATEGORY_PDF = "pdf"
CATEGORY_DOCS = "docs"
CATEGORY_IMAGE = "image"
CATEGORY_VIDEO = "video"
CATEGORY_AUDIO = "audio"
CATEGORY_ARCHIVE = "archive"
CATEGORY_CODE = "code"

# Curated Taxonomy: (Key, Display Name, Icon, Extensions: List[Tuple[ext, description, default_active]])
CATEGORY_TAXONOMY: Dict[str, Dict] = {
    CATEGORY_PDF: {
        "name": "PDFs & eBooks",
        "icon": "📕",
        "items": [
            (".pdf", "PDF Document", True),
            (".epub", "Electronic Publication (EPUB)", False),
            (".mobi", "Mobipocket eBook", False),
            (".djvu", "DjVu Document", False),
            (".azw3", "Kindle eBook (AZW3)", False),
            (".fb2", "FictionBook 2", False),
            (".cbr", "Comic Book RAR", False),
            (".cbz", "Comic Book ZIP", False),
        ]
    },
    CATEGORY_DOCS: {
        "name": "Documents & Office",
        "icon": "📄",
        "items": [
            (".docx", "Microsoft Word Document", True),
            (".doc", "Word 97-2003 Document", True),
            (".xlsx", "Microsoft Excel Spreadsheet", True),
            (".xls", "Excel 97-2003 Spreadsheet", True),
            (".pptx", "PowerPoint Presentation", True),
            (".ppt", "PowerPoint 97-2003", True),
            (".txt", "Plain Text Document", True),
            (".csv", "Comma Separated Values", True),
            (".rtf", "Rich Text Format", False),
            (".md", "Markdown Documentation", False),
            (".json", "JSON Data File", False),
            (".xml", "XML Data File", False),
            (".odt", "OpenDocument Text", False),
            (".ods", "OpenDocument Spreadsheet", False),
            (".odp", "OpenDocument Presentation", False),
        ]
    },
    CATEGORY_IMAGE: {
        "name": "Photos & Images",
        "icon": "🖼️",
        "items": [
            (".jpg", "JPEG Image", True),
            (".jpeg", "JPEG Image", True),
            (".png", "PNG Image (Lossless)", True),
            (".webp", "WebP Modern Image", True),
            (".bmp", "Bitmap Graphic", False),
            (".gif", "GIF Animation / Image", False),
            (".svg", "Scalable Vector Graphic", False),
            (".tiff", "TIFF High-Res Image", False),
            (".heic", "Apple High Efficiency (HEIC)", False),
            (".heif", "High Efficiency Image (HEIF)", False),
            (".ico", "Windows Icon", False),
            (".psd", "Adobe Photoshop Document", False),
            (".raw", "Digital Camera RAW", False),
            (".cr2", "Canon Camera RAW", False),
            (".nef", "Nikon Camera RAW", False),
        ]
    },
    CATEGORY_VIDEO: {
        "name": "Videos & Movies",
        "icon": "🎬",
        "items": [
            (".mp4", "MPEG-4 Video", True),
            (".mkv", "Matroska Multimedia Video", True),
            (".avi", "Audio Video Interleave", True),
            (".mov", "Apple QuickTime Movie", True),
            (".wmv", "Windows Media Video", False),
            (".flv", "Flash Video", False),
            (".webm", "WebM Video", False),
            (".m4v", "Apple Video File", False),
            (".3gp", "3GPP Mobile Video", False),
            (".ts", "MPEG Transport Stream", False),
            (".vob", "DVD Video Object", False),
        ]
    },
    CATEGORY_AUDIO: {
        "name": "Audio & Music",
        "icon": "🎵",
        "items": [
            (".mp3", "MP3 Audio", True),
            (".wav", "Waveform Audio (Uncompressed)", True),
            (".flac", "Free Lossless Audio Codec", True),
            (".m4a", "MPEG-4 Audio", True),
            (".aac", "Advanced Audio Coding", False),
            (".ogg", "Ogg Vorbis Audio", False),
            (".wma", "Windows Media Audio", False),
            (".opus", "Opus Audio", False),
            (".alac", "Apple Lossless Audio", False),
            (".aiff", "Audio Interchange File", False),
        ]
    },
    CATEGORY_ARCHIVE: {
        "name": "Archives & Zip",
        "icon": "📦",
        "items": [
            (".zip", "ZIP Compressed Archive", True),
            (".rar", "WinRAR Compressed Archive", True),
            (".7z", "7-Zip Compressed Archive", True),
            (".tar", "Tape Archive", False),
            (".gz", "Gzip Compressed Archive", False),
            (".iso", "Optical Disk Image", False),
            (".bz2", "Bzip2 Compressed Archive", False),
            (".xz", "XZ Compressed Archive", False),
            (".cab", "Cabinet File", False),
            (".dmg", "Apple Disk Image", False),
        ]
    },
    CATEGORY_CODE: {
        "name": "Code & Scripts",
        "icon": "💻",
        "items": [
            (".py", "Python Source File", True),
            (".java", "Java Source File", False),
            (".cpp", "C++ Source File", False),
            (".c", "C Source File", False),
            (".h", "C/C++ Header File", False),
            (".cs", "C# Source File", False),
            (".js", "JavaScript File", False),
            (".ts", "TypeScript File", False),
            (".html", "HTML Web Page", False),
            (".css", "Cascading Style Sheet", False),
            (".sql", "SQL Database Script", False),
            (".bat", "Windows Batch Script", False),
            (".ps1", "PowerShell Script", False),
            (".sh", "Shell Script", False),
        ]
    },
}

# Mapping of category key -> set of all extensions in that category
CATEGORY_EXTENSIONS: Dict[str, Optional[Set[str]]] = {
    CATEGORY_ALL: None,
    **{
        cat: {ext.lower() for ext, _, _ in data["items"]}
        for cat, data in CATEGORY_TAXONOMY.items()
    }
}

# Mapping of ext -> category key
EXTENSION_TO_CATEGORY: Dict[str, str] = {}
for _cat, _data in CATEGORY_TAXONOMY.items():
    for _ext, _, _ in _data["items"]:
        EXTENSION_TO_CATEGORY[_ext.lower()] = _cat


class ExtensionFilterState:
    """
    Manages user selection of file extensions across all categories.
    Can be serialized to and loaded from local JSON preference cache.
    """
    PREF_FILE = os.path.join(
        os.path.expanduser("~"), ".hybrid_duplicate_checker", "extension_filter_pref.json"
    )

    def __init__(self):
        # Mode: 'all' or 'custom'
        self.mode: str = "all"
        # Set of selected extensions (e.g. {'.pdf'})
        self.selected_extensions: Set[str] = set()
        # Active category currently being viewed/focused
        self.active_category: str = CATEGORY_PDF
        self._load_from_prefs()

    def set_all_files_mode(self):
        """Switch to scanning all files (*.*)."""
        self.mode = "all"
        self._save_to_prefs()

    def is_all_files(self) -> bool:
        return self.mode == "all"

    def get_active_extensions(self) -> Optional[Set[str]]:
        """
        Returns None for 'All Files (*.*)', or Set of lowercase extensions (e.g. {'.pdf'}).
        """
        if self.is_all_files():
            return None
        return {ext.lower() for ext in self.selected_extensions}

    def is_extension_selected(self, ext: str) -> bool:
        if self.mode == "all":
            return True
        return ext.lower() in self.selected_extensions

    def set_extension_selected(self, ext: str, selected: bool):
        """Toggles an individual extension."""
        ext_clean = ext.lower().strip()
        if not ext_clean.startswith("."):
            ext_clean = f".{ext_clean}"

        self.mode = "custom"
        if selected:
            self.selected_extensions.add(ext_clean)
        else:
            self.selected_extensions.discard(ext_clean)
        self._save_to_prefs()

    def select_only_extension(self, ext: str):
        """1-Click shortcut: clear all other extensions and keep ONLY this one."""
        ext_clean = ext.lower().strip()
        if not ext_clean.startswith("."):
            ext_clean = f".{ext_clean}"
        self.mode = "custom"
        self.selected_extensions = {ext_clean}
        cat = EXTENSION_TO_CATEGORY.get(ext_clean)
        if cat:
            self.active_category = cat
        self._save_to_prefs()

    def select_all_in_category(self, cat_key: str):
        """Selects all extensions in the given category."""
        if cat_key in CATEGORY_TAXONOMY:
            self.mode = "custom"
            for ext, _, _ in CATEGORY_TAXONOMY[cat_key]["items"]:
                self.selected_extensions.add(ext.lower())
            self._save_to_prefs()

    def deselect_all_in_category(self, cat_key: str):
        """Deselects all extensions in the given category."""
        if cat_key in CATEGORY_TAXONOMY:
            for ext, _, _ in CATEGORY_TAXONOMY[cat_key]["items"]:
                self.selected_extensions.discard(ext.lower())
            self._save_to_prefs()

    def get_selected_count_in_category(self, cat_key: str) -> int:
        if cat_key not in CATEGORY_TAXONOMY:
            return 0
        cat_exts = {ext.lower() for ext, _, _ in CATEGORY_TAXONOMY[cat_key]["items"]}
        if self.mode == "all":
            return len(cat_exts)
        return len(cat_exts.intersection(self.selected_extensions))

    def get_total_count_in_category(self, cat_key: str) -> int:
        if cat_key not in CATEGORY_TAXONOMY:
            return 0
        return len(CATEGORY_TAXONOMY[cat_key]["items"])

    def get_summary_display(self) -> Tuple[str, str, str]:
        """
        Returns (icon, short_text, tooltip_text).
        Examples:
          ("📑", "All Files (*.*)", "Scanning all files regardless of extension")
          ("📕", "PDF: .pdf only (1)", "Active extensions: .pdf")
          ("🎬", "Videos: mp4, mkv (2)", "Active extensions: .mp4, .mkv")
          ("⚙️", "Custom (5 ext)", "Active extensions: .pdf, .docx, .jpg, ...")
        """
        if self.is_all_files():
            return ("📑", "All Files (*.*)", "Scanning all files (*.*) regardless of extension")

        active = sorted(list(self.selected_extensions))
        total = len(active)
        if total == 1:
            ext = active[0]
            cat = EXTENSION_TO_CATEGORY.get(ext)
            icon = CATEGORY_TAXONOMY.get(cat, {}).get("icon", "📄") if cat else "📄"
            return (icon, f"{ext} only (1)", f"Scanning strictly: {ext}")

        # Check if all selected belong to a single category
        categories_represented = {EXTENSION_TO_CATEGORY.get(ext) for ext in active} - {None}
        if len(categories_represented) == 1:
            cat_key = next(iter(categories_represented))
            cat_info = CATEGORY_TAXONOMY[cat_key]
            icon = cat_info["icon"]
            cat_name = cat_info["name"].split("&")[0].strip()
            # If all items in this category are selected
            total_in_cat = len(cat_info["items"])
            if total == total_in_cat:
                return (icon, f"{cat_info['name']} ({total})", f"All {total} {cat_info['name']} extensions active")
            # List up to 2 extensions
            ext_preview = ", ".join(e.lstrip(".") for e in active[:2])
            if total > 2:
                ext_preview += f" +{total-2}"
            return (icon, f"{cat_name} ({ext_preview})", f"Active: {', '.join(active)}")

        # Multi-category custom mix
        first_few = ", ".join(e.lstrip(".") for e in active[:3])
        return ("⚙️", f"Custom ({total} ext)", f"Active {total} extensions: {', '.join(active)}")

    def _save_to_prefs(self):
        try:
            os.makedirs(os.path.dirname(self.PREF_FILE), exist_ok=True)
            data = {
                "mode": self.mode,
                "selected_extensions": list(self.selected_extensions),
                "active_category": self.active_category
            }
            with open(self.PREF_FILE, "w", encoding="utf-8") as f:
                json.dump(data, f, indent=2)
        except Exception:
            pass

    def _load_from_prefs(self):
        try:
            if os.path.isfile(self.PREF_FILE):
                with open(self.PREF_FILE, "r", encoding="utf-8") as f:
                    data = json.load(f)
                self.mode = data.get("mode", "all")
                self.selected_extensions = set(data.get("selected_extensions", []))
                self.active_category = data.get("active_category", CATEGORY_PDF)
        except Exception:
            self.mode = "all"
            self.selected_extensions = set()
            self.active_category = CATEGORY_PDF
