"""
Fast & Safe file duplicate scanner for large collections.
Features:
- 2-Stage Hybrid Hashing: Ultra-fast size grouping + header/footer quick hash + SHA-256 confirmation.
- Safe mode verifies candidate content with SHA-256.
- Multiple Scan Modes:
  1. Safe Match (Size + Hash)
  2. Exact Name Match (समान नाम)
  3. Similar / Fuzzy Name Match (लगभग समान नाम)
  4. Same Page Count + Title
  5. Visual Cover Match
  6. Ultra-Fast (Size Only)
  7. Full SHA-256 Cryptographic Verification
- Multi-Folder scanning support with directory exclusion and minimum size filters.
"""
from __future__ import annotations

import concurrent.futures
import ctypes
from ctypes import wintypes
import difflib
import hashlib
import os
import re
import time
from typing import Callable, Dict, List, Optional, Set, Tuple, Union
from PySide6.QtCore import QThread, Signal

from hash_cache import get_hash_cache

_DRIVE_SSD_CACHE: Dict[str, bool] = {}

class DEVICE_SEEK_PENALTY_DESCRIPTOR(ctypes.Structure):
    _fields_ = [
        ("Version", wintypes.DWORD),
        ("Size", wintypes.DWORD),
        ("IncursSeekPenalty", ctypes.c_bool)
    ]

def is_path_on_ssd(path: str) -> bool:
    """
    Detects whether a filesystem path resides on a solid-state drive (SSD)
    or a rotational mechanical hard drive (HDD) using Windows IOCTL seek penalty check.
    Prevents physical head thrashing on mechanical drives.
    """
    global _DRIVE_SSD_CACHE
    drive = os.path.splitdrive(os.path.abspath(path))[0].upper()
    if not drive:
        return True
    if drive in _DRIVE_SSD_CACHE:
        return _DRIVE_SSD_CACHE[drive]

    try:
        drive_path = f"\\\\.\\{drive}"
        FILE_SHARE_READ = 1
        FILE_SHARE_WRITE = 2
        OPEN_EXISTING = 3
        # Query access 0 does NOT require administrator privileges
        hDevice = ctypes.windll.kernel32.CreateFileW(
            drive_path, 0, FILE_SHARE_READ | FILE_SHARE_WRITE,
            None, OPEN_EXISTING, 0, None
        )
        if hDevice != -1 and hDevice != 0:
            query = (ctypes.c_uint32 * 3)(7, 0, 0)
            desc = DEVICE_SEEK_PENALTY_DESCRIPTOR()
            bytes_returned = wintypes.DWORD(0)

            ok = ctypes.windll.kernel32.DeviceIoControl(
                hDevice, 0x002D1400, ctypes.byref(query), ctypes.sizeof(query),
                ctypes.byref(desc), ctypes.sizeof(desc), ctypes.byref(bytes_returned), None
            )
            ctypes.windll.kernel32.CloseHandle(hDevice)
            if ok:
                is_ssd = not desc.IncursSeekPenalty
                _DRIVE_SSD_CACHE[drive] = is_ssd
                return is_ssd
    except Exception:
        pass

    # Tier 2 Fallback: PowerShell Get-Partition/Get-Disk check
    try:
        import subprocess
        d_letter = drive.rstrip(':')
        cmd = ["powershell", "-NoProfile", "-Command",
               f"(Get-Partition -DriveLetter '{d_letter}' | Get-Disk).MediaType"]
        out = subprocess.check_output(cmd, timeout=3, stderr=subprocess.DEVNULL).decode('utf-8', errors='ignore').strip().upper()
        if out:
            is_ssd = ("SSD" in out or "NVME" in out)
            _DRIVE_SSD_CACHE[drive] = is_ssd
            return is_ssd
    except Exception:
        pass

    _DRIVE_SSD_CACHE[drive] = True
    return True


SCAN_MODE_SAFE = "safe"                      # Size + Quick Hash + SHA-256 (verified default)
SCAN_MODE_EXACT_NAME = "exact_name"          # Exact same filename (समान नाम)
SCAN_MODE_SIMILAR_NAME = "similar_name"      # Nearly identical / fuzzy filename (लगभग समान नाम)
SCAN_MODE_PAGE_TITLE = "page_title"          # Same Page Count + Title Match (PDFs/E-books)
SCAN_MODE_VISUAL_COVER = "visual_cover"      # Visual Cover Match via 64-bit dHash (PDFs/eBooks/Images)
SCAN_MODE_FAST = "fast"                      # Size only (Ultra-fast, legacy)
SCAN_MODE_FULL_HASH = "full_hash"            # Full SHA-256 on all size candidates

CATEGORY_ALL = "all"
CATEGORY_PDF = "pdf"
CATEGORY_IMAGE = "image"
CATEGORY_VIDEO = "video"
CATEGORY_AUDIO = "audio"
CATEGORY_DOCS = "docs"
CATEGORY_ARCHIVE = "archive"

CATEGORY_EXTENSIONS: Dict[str, Optional[Set[str]]] = {
    CATEGORY_ALL: None,
    CATEGORY_PDF: {".pdf", ".epub", ".mobi", ".djvu", ".cbr", ".cbz"},
    CATEGORY_IMAGE: {".jpg", ".jpeg", ".png", ".webp", ".heic", ".bmp", ".gif", ".svg", ".tiff", ".ico"},
    CATEGORY_VIDEO: {".mp4", ".mkv", ".avi", ".mov", ".wmv", ".flv", ".webm", ".m4v", ".3gp"},
    CATEGORY_AUDIO: {".mp3", ".wav", ".flac", ".m4a", ".aac", ".ogg", ".wma", ".opus"},
    CATEGORY_DOCS: {".docx", ".doc", ".xlsx", ".xls", ".pptx", ".ppt", ".txt", ".csv", ".rtf", ".odt", ".ods", ".md", ".json"},
    CATEGORY_ARCHIVE: {".zip", ".rar", ".7z", ".tar", ".gz", ".iso", ".bz2", ".xz"}
}


def compute_quick_hash(path: str, size: int, cancel_check: Optional[Callable[[], bool]] = None) -> str:
    """
    Computes a super-fast 3-point sparse partial hash using:
    - First 16KB (header)
    - Middle 16KB (body content)
    - Last 16KB (footer)
    - File size
    For files <= 48KB, reads entire file.
    Uses representative file regions before the full content hash stage.
    """
    if cancel_check and cancel_check():
        return ""
    chunk_size = 16384  # 16 KB
    h = hashlib.blake2b(digest_size=16)
    h.update(str(size).encode("ascii"))
    try:
        with open(path, "rb") as f:
            if cancel_check and cancel_check():
                return ""
            if size <= chunk_size * 3:
                h.update(f.read())
            else:
                # 1. Header (0..16KB)
                if cancel_check and cancel_check():
                    return ""
                h.update(f.read(chunk_size))
                # 2. Middle (mid..mid+16KB)
                if cancel_check and cancel_check():
                    return ""
                mid_pos = max(chunk_size, (size // 2) - (chunk_size // 2))
                f.seek(mid_pos, os.SEEK_SET)
                h.update(f.read(chunk_size))
                # 3. Footer (tail-16KB..tail)
                if cancel_check and cancel_check():
                    return ""
                f.seek(size - chunk_size, os.SEEK_SET)
                h.update(f.read(chunk_size))
        return h.hexdigest()
    except Exception:
        return ""


def compute_full_sha256(path: str, cancel_check: Optional[Callable[[], bool]] = None) -> str:
    """Computes standard full SHA-256 with 1MB buffered reads for maximum throughput and instant abort."""
    h = hashlib.sha256()
    buf_size = 1048576  # 1 MB read buffer
    try:
        with open(path, "rb", buffering=buf_size) as f:
            while chunk := f.read(buf_size):
                if cancel_check and cancel_check():
                    return ""
                h.update(chunk)
        return h.hexdigest()
    except Exception:
        return ""


def compute_visual_dhash(path: str, cancel_check: Optional[Callable[[], bool]] = None, pdf_pool=None) -> str:
    """
    Computes a 64-bit perceptual difference hash (dHash) of:
    - Page 0 (cover page) for Documents (PDF, EPUB, XPS, CBZ) using PyMuPDF
    - Visual pixels for Photos/Images (JPG, PNG, WEBP, BMP, etc.) using Pillow
    Robust against resolution changes, color/grayscale differences, and slight compression.
    """
    if cancel_check and cancel_check():
        return ""
    ext = os.path.splitext(path)[1].lower()
    try:
        # 1. Document Cover Page (PDF/EPUB/XPS/CBZ)
        if ext in (".pdf", ".epub", ".xps", ".cbz", ".mobi"):
            from pdf_worker import extract_cover_hash
            future = pdf_pool.extract_cover_hash(path) if pdf_pool else extract_cover_hash(path)
            return future.result(timeout=20) or ""

        # 2. Standalone Photos & Images
        elif ext in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif", ".ico"):
            from PIL import Image
            with Image.open(path) as img:
                img_gray = img.convert("L").resize((9, 8), Image.Resampling.BILINEAR)
                samples = list(img_gray.getdata())
        else:
            return ""

        if len(samples) < 72:
            return ""
        # Low contrast or solid blank check
        if max(samples) - min(samples) <= 2:
            return ""

        diff = []
        for y in range(8):
            for x in range(8):
                diff.append('1' if samples[y * 9 + x] > samples[y * 9 + x + 1] else '0')
        res = hex(int(''.join(diff), 2))[2:].zfill(16)
        if res in ("0000000000000000", "ffffffffffffffff"):
            return ""
        return res
    except Exception:
        return ""


def hamming_distance(h1: str, h2: str) -> int:
    """Returns number of differing bits between two 16-hex-digit 64-bit dHashes."""
    if not h1 or not h2 or h1 in ("0000000000000000", "ffffffffffffffff") or h2 in ("0000000000000000", "ffffffffffffffff"):
        return 64
    try:
        return bin(int(h1, 16) ^ int(h2, 16)).count('1')
    except Exception:
        return 64


def volume_signature(name: str) -> tuple:
    """Only explicit volume markers (including standalone Roman numerals)."""
    stem = os.path.splitext(name)[0].lower().replace("_", " ")
    marker = r"(?:volume|vol|part|khand|bhag|parva|ank|खंड|खण्ड|भाग|पर्व|अंक)"
    found = re.findall(rf"(?<!\w){marker}[\s.()\[\]-]*(\d+|[ivxlcdm]+)(?!\w)", stem)
    romans = re.findall(r"(?<!\w)(i|ii|iii|iv|v|vi|vii|viii|ix|x|xi|xii)(?!\w)", stem)
    def number(value):
        if value.isdecimal():
            return int(value)
        total = previous = 0
        for char in reversed(value):
            n = dict(i=1,v=5,x=10,l=50,c=100,d=500,m=1000)[char]
            total += -n if n < previous else n
            previous = max(previous, n)
        return total
    return tuple(sorted(set(number(value) for value in found + romans)))


def volumes_conflict(first: str, second: str) -> bool:
    a, b = volume_signature(first), volume_signature(second)
    return bool((a or b) and a != b)


def is_heuristic_match(match_type: str) -> bool:
    return not ("Exact Hash" in match_type or match_type in ("Safe Match", "Exact Match", "Full SHA-256"))


def clean_filename_for_similarity(name: str) -> str:
    """
    Normalizes filename for fuzzy and similar name comparison:
    - Strips leading catalog / barcode serial numbers (5 to 10 digits, e.g. '0000036_')
      while strictly preserving 1-4 digit volume/chapter numbers (e.g. '01_', '02_').
    - Strips copy markers: '(1)', '(2)', '(Copy 2)', '[1]', '- Copy', '_copy'
    - Strips common tags: '_text', '_ocr', '_scan', '_final', '_backup', '_restored'
    - Strips unicode corruption artifacts like '????'
    - Normalizes punctuation and whitespace
    """
    stem = os.path.splitext(name)[0].lower()
    # Strip leading catalog barcodes (5 to 10 digits followed by separator, e.g. '0000036_')
    sub_stem = re.sub(r'^\d{5,10}[\s_\-\.]+', '', stem)
    if sub_stem.strip():
        stem = sub_stem

    # Strip leading common articles ('the', 'a', 'an')
    sub_art = re.sub(r'^(?:the|a|an)\s+', '', stem)
    if sub_art.strip():
        stem = sub_art

    # Strip copy markers like (1), (2), (copy), [1], {1}, (copy 2)
    stem = re.sub(r'[\(\[\{]\s*(?:copy\s*)?\d*\s*[\)\]\}]', '', stem)
    # Strip ' - copy' or '_copy' or ' - copy 2'
    stem = re.sub(r'[-_\s]+copy(?:\s*\d+)?\b', '', stem)
    suffix = re.search(r'(?:_\d+|\s+-\s*\d+)\s*$', stem)
    if suffix:
        prefix = stem[:suffix.start()]
        if not re.search(r'(?:\b(?:vol|volume|part|khand|bhag|parva|ank)|खंड|खण्ड|भाग|पर्व|अंक)\s*$', prefix):
            stem = prefix
    # Strip suffix tags like _text, _ocr, _scan, _final, _backup, _restored, _compressed, _web, etc.
    stem = re.sub(r'[-_\s]+(?:text|ocr|scan|final|edit|backup|restored|compressed|web|print|hires|lowres)\b', '', stem)
    # Strip question marks or strange unicode replacement marks
    stem = re.sub(r'[\?]+', '', stem)
    # Collapse spaces and punctuation
    stem = re.sub(r'[-_\s\.]+', ' ', stem).strip()
    return stem


def normalize_filename(name: str) -> str:
    """Backwards-compatible alias for clean_filename_for_similarity."""
    return clean_filename_for_similarity(name)


def get_name_similarity_ratio(name1: str, name2: str) -> float:
    """Returns filename similarity ratio from 0.0 to 1.0 (1.0 = identical)."""
    c1 = clean_filename_for_similarity(name1)
    c2 = clean_filename_for_similarity(name2)
    # Guard against different volumes/editions (e.g. Vol 1 vs Vol 2)
    if volumes_conflict(name1, name2):
        return 0.0
    if c1 == c2:
        return 1.0
    return difflib.SequenceMatcher(None, c1, c2).ratio()


class FileInfo:
    """
    Ultra-lightweight slotted representation of a file's metadata.
    Reduces per-instance RAM footprint by ~72% for collections with 100,000+ files.
    """
    __slots__ = ('path', 'filename', 'size', 'mtime', 'folder', 'hash_val', 'quick_hash', 'sha256', 'page_count', 'extension', 'issue', 'title')

    def __init__(
        self,
        path: str,
        filename: str,
        size: int,
        mtime: float,
        folder: str,
        hash_val: Optional[str] = None,
        quick_hash: Optional[str] = None,
        sha256: Optional[str] = None,
        page_count: Optional[int] = None,
        extension: str = "",
        issue: str = "",
        title: str = ""
    ):
        self.path = path
        self.filename = filename
        self.size = size
        self.mtime = mtime
        self.folder = folder
        self.hash_val = hash_val
        self.quick_hash = quick_hash
        self.sha256 = sha256
        self.page_count = page_count
        self.extension = extension if extension else os.path.splitext(filename)[1].lower()
        self.issue = issue
        self.title = title

    @property
    def formatted_size(self) -> str:
        s = self.size
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if s < 1024.0:
                return f"{s:.2f} {unit}" if unit != 'B' else f"{s} Bytes"
            s /= 1024.0
        return f"{s:.2f} PB"

    @property
    def formatted_mtime(self) -> str:
        try:
            return time.strftime("%Y-%m-%d %H:%M:%S", time.localtime(self.mtime))
        except Exception:
            return "Unknown"


# Backwards-compatible alias
PDFFileInfo = FileInfo


class DuplicateGroup:
    """Slotted duplicate group container for memory-efficient multi-group storage."""
    __slots__ = ('size', 'files', 'match_type')

    def __init__(self, size: int, files: List[FileInfo], match_type: str = "Exact Match"):
        self.size = size
        self.files = files
        self.match_type = match_type

    @property
    def wasted_bytes(self) -> int:
        if len(self.files) <= 1:
            return 0
        # Wasted bytes is the sum of sizes of redundant copies (all except the kept file)
        return sum(f.size for f in self.files[1:])

    @property
    def formatted_size(self) -> str:
        sizes = [f.size for f in self.files]
        if len(set(sizes)) > 1:
            min_s = min(sizes)
            max_s = max(sizes)
            def fmt(sz):
                for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
                    if sz < 1024.0:
                        return f"{sz:.1f} {unit}" if unit != 'B' else f"{sz} B"
                    sz /= 1024.0
                return f"{sz:.1f} PB"
            return f"{fmt(min_s)} ~ {fmt(max_s)}"

        s = self.size
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if s < 1024.0:
                return f"{s:.2f} {unit}" if unit != 'B' else f"{s} Bytes"
            s /= 1024.0
        return f"{s:.2f} PB"

    @property
    def formatted_wasted(self) -> str:
        s = self.wasted_bytes
        for unit in ['B', 'KB', 'MB', 'GB', 'TB']:
            if s < 1024.0:
                return f"{s:.2f} {unit}" if unit != 'B' else f"{s} Bytes"
            s /= 1024.0
        return f"{s:.2f} PB"


class FastScanWorker(QThread):
    # Signals:
    # progress(scanned_files_count, current_dir, suspected_duplicates_count)
    progress = Signal(int, str, int)
    # stage2_progress(processed_candidates, total_candidates, current_filename, speed_mb_s)
    stage2_progress = Signal(int, int, str, float)
    # finished(duplicate_groups, total_scanned, total_pdf_count, elapsed_seconds)
    scan_finished = Signal(list, int, int, float)
    # corrupt_files_found(list_of_corrupt_or_empty_files)
    corrupt_files_found = Signal(list)
    # cancelled(total_scanned_so_far, elapsed_seconds)
    scan_cancelled = Signal(int, float)
    # error(message)
    scan_error = Signal(str)

    # Folders to ignore during scanning
    IGNORED_DIR_NAMES = {
        "_deleted_pdfs",
        "_deleted_files",
        "deleted_pdfs",
        "deleted",
        "$recycle.bin",
        "system volume information",
        ".git",
        "__pycache__"
    }

    def __init__(
        self,
        root_path: Union[str, List[str]],
        scan_mode: str = SCAN_MODE_SAFE,
        category: str = CATEGORY_ALL,
        custom_extensions: Optional[Set[str]] = None,
        min_size_bytes: int = 0,
        exclude_dirs: Optional[Set[str]] = None,
        use_cache: bool = True,
        include_empty_files: bool = False,
        concurrency: Optional[int] = None,
        parent=None,
        pdf_pool=None
    ):
        super().__init__(parent)
        roots = [root_path] if isinstance(root_path, str) else root_path
        self.root_paths = list(dict.fromkeys(
            os.path.normcase(os.path.realpath(p)) for p in roots if os.path.isdir(p)
        ))
        self.root_dirs = self.root_paths

        self.scan_mode = scan_mode
        self.category = category
        if custom_extensions is not None:
            self.target_extensions = {ext.lower() if ext.startswith('.') else f".{ext.lower()}" for ext in custom_extensions}
        else:
            self.target_extensions = CATEGORY_EXTENSIONS.get(category, None)

        self.min_size_bytes = max(0, min_size_bytes)
        self.use_cache = use_cache
        self.include_empty_files = include_empty_files
        self.concurrency = concurrency
        self.pdf_pool = pdf_pool
        self.exclude_dirs = set(self.IGNORED_DIR_NAMES)
        if exclude_dirs:
            self.exclude_dirs.update(d.lower() for d in exclude_dirs)

        self._is_cancelled = False
        self.duplicate_groups: List[DuplicateGroup] = []

    def cancel(self):
        self._is_cancelled = True

    def run(self):
        start_time = time.time()
        if not self.root_paths:
            self.scan_error.emit("No valid folders provided to scan.")
            return

        size_map: Dict[int, List[FileInfo]] = {}
        all_pdf_infos: List[FileInfo] = []
        empty_pdf_infos: List[FileInfo] = []
        total_scanned_files = 0
        total_target_files = 0
        duplicate_groups_count = 0
        last_progress_emit = 0.0

        stack = list(self.root_paths)
        visited_dirs = set()
        visited_files = set()

        try:
            # ----------------------------------------------------
            # STAGE 1: Fast Discovery & Collection
            # ----------------------------------------------------
            while stack and not self._is_cancelled:
                current_dir = stack.pop()
                dir_key = os.path.normcase(os.path.realpath(current_dir))
                if any(os.path.isabs(p) and (dir_key == p or dir_key.startswith(p.rstrip(os.sep) + os.sep)) for p in self.exclude_dirs):
                    continue
                if dir_key in visited_dirs:
                    continue
                visited_dirs.add(dir_key)

                try:
                    with os.scandir(current_dir) as entries:
                        for entry in entries:
                            if self._is_cancelled:
                                break

                            name_lower = entry.name.lower()

                            # If subdirectory
                            try:
                                if entry.is_dir(follow_symlinks=False):
                                    if name_lower not in self.exclude_dirs and not entry.name.startswith('.'):
                                        stack.append(entry.path)
                                    continue
                                if not entry.is_file(follow_symlinks=False):
                                    continue
                            except (PermissionError, OSError):
                                continue

                            # Match target extensions (or all files)
                            ext = os.path.splitext(name_lower)[1]
                            if self.target_extensions is None or ext in self.target_extensions:
                                try:
                                    # DirEntry.stat does not expose Windows file identity.
                                    stat = os.stat(entry.path, follow_symlinks=False)
                                    file_key = (stat.st_dev, stat.st_ino) if stat.st_ino else os.path.normcase(os.path.realpath(entry.path))
                                    if file_key in visited_files:
                                        continue
                                    visited_files.add(file_key)
                                    total_target_files += 1
                                    size = stat.st_size
                                    mtime = stat.st_mtime

                                    # Check minimum size filter or empty file filter
                                    if size == 0 and self.include_empty_files:
                                        info = FileInfo(
                                            path=entry.path,
                                            filename=entry.name,
                                            size=0,
                                            mtime=mtime,
                                            folder=current_dir,
                                            extension=ext
                                        )
                                        empty_pdf_infos.append(info)
                                    elif size >= self.min_size_bytes and size > 0:
                                        info = FileInfo(
                                            path=entry.path,
                                            filename=entry.name,
                                            size=size,
                                            mtime=mtime,
                                            folder=current_dir,
                                            extension=ext
                                        )

                                        all_pdf_infos.append(info)

                                        if size in size_map:
                                            size_map[size].append(info)
                                            if len(size_map[size]) == 2:
                                                duplicate_groups_count += 1
                                        else:
                                            size_map[size] = [info]

                                except (PermissionError, OSError):
                                    continue

                            total_scanned_files += 1

                            # Update progress periodically (every 100ms)
                            now = time.time()
                            if now - last_progress_emit >= 0.1:
                                self.progress.emit(total_target_files, current_dir, duplicate_groups_count)
                                last_progress_emit = now

                except (PermissionError, OSError):
                    continue

            if self._is_cancelled:
                self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                return

            # ----------------------------------------------------
            # STAGE 2: Verification Based on Selected Scan Mode
            # ----------------------------------------------------
            duplicate_groups: List[DuplicateGroup] = []

            # MODE A: EXACT FILENAME MATCH (समान नाम)
            if self.scan_mode == SCAN_MODE_EXACT_NAME:
                name_map: Dict[str, List[PDFFileInfo]] = {}
                total_c = len(all_pdf_infos)
                self.stage2_progress.emit(0, max(1, total_c), "Grouping exact matching filenames...", 0.0)
                for idx, finfo in enumerate(all_pdf_infos, start=1):
                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return
                    k = finfo.filename.lower().strip()
                    name_map.setdefault(k, []).append(finfo)
                    if idx % 1000 == 0 or idx == total_c:
                        self.stage2_progress.emit(idx, total_c, finfo.filename, 0.0)

                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return

                for name_key, files in name_map.items():
                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return
                    if len(files) >= 2:
                        duplicate_groups.append(DuplicateGroup(
                            size=files[0].size,
                            files=files,
                            match_type="Exact Name Match"
                        ))

                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return

            # MODE B: SIMILAR / FUZZY FILENAME MATCH (लगभग समान नाम)
            elif self.scan_mode == SCAN_MODE_SIMILAR_NAME:
                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return

                # 1. Cleaned copy tags group (matches (1), - Copy, _text, barcode prefixes)
                # Group by (cleaned_text, volume_numbers) to guarantee different chapters/volumes NEVER collide
                clean_map: Dict[Tuple[str, Tuple[str, ...]], List[PDFFileInfo]] = {}
                total_c = len(all_pdf_infos)
                self.stage2_progress.emit(0, max(1, total_c), "Analyzing filename patterns...", 0.0)

                for idx, finfo in enumerate(all_pdf_infos, start=1):
                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return
                    cleaned = clean_filename_for_similarity(finfo.filename)
                    if cleaned:
                        content_nums = volume_signature(finfo.filename)
                        clean_map.setdefault((cleaned, content_nums), []).append(finfo)
                    if idx % 2000 == 0 or idx == total_c:
                        self.stage2_progress.emit(idx, total_c, finfo.filename, 0.0)

                seen_paths = set()
                for (clean_key, _), files in clean_map.items():
                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return
                    if len(files) >= 2:
                        duplicate_groups.append(DuplicateGroup(
                            size=files[0].size,
                            files=files,
                            match_type="Similar Name Match"
                        ))
                        for f in files:
                            seen_paths.add(f.path)

                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return

                # 2. Multi-token inverted index for fuzzy matching (eliminates Prefix Trap & 200 Limit)
                STOP_WORDS = {
                    "the", "a", "an", "and", "of", "in", "to", "for", "with", "on", "at",
                    "by", "from", "is", "ka", "ki", "ke", "ko", "se", "aur", "ek"
                }

                unmatched_files = [f for f in all_pdf_infos if f.path not in seen_paths]
                token_to_files: Dict[str, List[PDFFileInfo]] = {}

                for idx, finfo in enumerate(unmatched_files, start=1):
                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return
                    cleaned = clean_filename_for_similarity(finfo.filename)
                    words = [w for w in re.split(r'\W+', cleaned) if len(w) >= 3]
                    content_words = [w for w in words if w not in STOP_WORDS]
                    index_words = content_words[:3] if content_words else words[:2]
                    for w in index_words:
                        token_to_files.setdefault(w, []).append(finfo)

                candidate_pairs: Dict[Tuple[str, str], Tuple[PDFFileInfo, PDFFileInfo]] = {}
                for token, t_files in token_to_files.items():
                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return
                    if len(t_files) < 2:
                        continue
                    # Skip excessively broad generic tokens (>300 files) to prevent quadratic hang
                    if len(t_files) > 300:
                        continue
                    # Sort by cleaned length to enable mathematical bound early pruning
                    t_files.sort(key=lambda f: len(clean_filename_for_similarity(f.filename)))
                    n_files = len(t_files)
                    for i in range(n_files):
                        if self._is_cancelled:
                            self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                            return
                        f1 = t_files[i]
                        p1 = f1.path
                        l1 = len(clean_filename_for_similarity(f1.filename))
                        for j in range(i + 1, n_files):
                            if self._is_cancelled:
                                self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                                return
                            f2 = t_files[j]
                            p2 = f2.path
                            l2 = len(clean_filename_for_similarity(f2.filename))
                            if (l1 + l2) > 0 and (2.0 * l1) / (l1 + l2) < 0.80:
                                break  # Remaining files in sorted bucket are even longer
                            if p1 != p2:
                                pair = (min(p1, p2), max(p1, p2))
                                if pair not in candidate_pairs:
                                    candidate_pairs[pair] = (f1, f2)

                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return

                # Cluster candidate pairs with similarity >= 84%
                adjacency: Dict[str, Set[str]] = {}
                path_to_info: Dict[str, PDFFileInfo] = {}

                pair_idx = 0
                total_pairs = len(candidate_pairs)
                if total_pairs > 0:
                    self.stage2_progress.emit(0, total_pairs, "Comparing fuzzy candidate names...", 0.0)

                for (p1, p2), (f1, f2) in candidate_pairs.items():
                    pair_idx += 1
                    if pair_idx % 100 == 0 or pair_idx == total_pairs:
                        if self._is_cancelled:
                            self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                            return
                        self.stage2_progress.emit(pair_idx, total_pairs, f"Fuzzy matching: {f1.filename}", 0.0)

                    path_to_info[p1] = f1
                    path_to_info[p2] = f2

                    name1 = clean_filename_for_similarity(f1.filename)
                    name2 = clean_filename_for_similarity(f2.filename)
                    l1, l2 = len(name1), len(name2)

                    # Quick mathematical bound on SequenceMatcher ratio:
                    # max possible ratio = (2 * min(l1, l2)) / (l1 + l2)
                    if (l1 + l2) > 0 and (2.0 * min(l1, l2)) / (l1 + l2) < 0.80:
                        continue

                    # Number token compatibility (prevent Vol 1 vs Vol 2 matching)
                    if volumes_conflict(f1.filename, f2.filename):
                        continue

                    # Word token compatibility to prevent topic collisions (e.g. Kumbh vs Mesh)
                    words1 = set(w for w in re.split(r'\W+', name1) if len(w) >= 3 and w not in STOP_WORDS)
                    words2 = set(w for w in re.split(r'\W+', name2) if len(w) >= 3 and w not in STOP_WORDS)
                    diff1 = words1 - words2
                    diff2 = words2 - words1
                    words_compatible = True
                    for w1 in diff1:
                        if not any(difflib.SequenceMatcher(None, w1, w2).ratio() >= 0.8 for w2 in diff2):
                            words_compatible = False
                            break
                    if not words_compatible:
                        continue

                    ratio = difflib.SequenceMatcher(None, name1, name2).ratio()
                    if ratio >= 0.82:
                        adjacency.setdefault(p1, set()).add(p2)
                        adjacency.setdefault(p2, set()).add(p1)

                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return

                # Find connected components (clusters)
                visited_paths = set()
                for start_path in adjacency:
                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return
                    if start_path in visited_paths or start_path in seen_paths:
                        continue
                    cluster_paths = []
                    q = [start_path]
                    visited_paths.add(start_path)
                    while q:
                        if self._is_cancelled:
                            self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                            return
                        curr = q.pop()
                        cluster_paths.append(curr)
                        for neighbor in adjacency.get(curr, []):
                            if neighbor not in visited_paths:
                                visited_paths.add(neighbor)
                                q.append(neighbor)

                    if len(cluster_paths) >= 2:
                        cluster_files = [path_to_info[cp] for cp in cluster_paths if cp in path_to_info]
                        for cp in cluster_paths:
                            seen_paths.add(cp)
                        duplicate_groups.append(DuplicateGroup(
                            size=cluster_files[0].size,
                            files=cluster_files,
                            match_type="Fuzzy Name Match"
                        ))

                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return

            # MODE C: SAME PAGE COUNT + TITLE SIMILARITY (PDFs/E-books)
            elif self.scan_mode == SCAN_MODE_PAGE_TITLE:
                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return
                self.stage2_progress.emit(0, max(1, len(all_pdf_infos)), "Reading document page counts...", 0.0)
                page_map: Dict[int, List[PDFFileInfo]] = {}
                total_c = len(all_pdf_infos)

                from pdf_worker import _pool
                metadata_pool = self.pdf_pool or _pool()
                pending = {}
                candidates = iter(f for f in all_pdf_infos if f.extension in (".pdf", ".epub", ".xps", ".cbz"))
                metadata_results = {}
                # Bound the batch below the pool's queue limit; keep every process
                # busy without enqueueing an unbounded library in memory.
                def replenish():
                    while len(pending) < 32 and not self._is_cancelled:
                        finfo = next(candidates, None)
                        if finfo is None:
                            break
                        pending[metadata_pool.extract_pdf_metadata(finfo.path)] = finfo
                try:
                    replenish()
                    completed = 0
                    while pending and not self._is_cancelled:
                        done, _ = concurrent.futures.wait(pending, timeout=.1, return_when=concurrent.futures.FIRST_COMPLETED)
                        for future in concurrent.futures.as_completed(done):
                            finfo = pending.pop(future)
                            try:
                                metadata_results[finfo.path] = future.result()
                            except Exception:
                                pass
                            completed += 1
                            self.stage2_progress.emit(completed, total_c, f"Counting pages: {finfo.filename}", 0.0)
                        replenish()
                finally:
                    for future in pending:
                        metadata_pool.cancel(future)

                for idx, finfo in enumerate(all_pdf_infos, start=1):
                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return
                    ext = os.path.splitext(finfo.path)[1].lower()
                    page_count = 0
                    if ext in (".pdf", ".epub", ".xps", ".cbz"):
                        try:
                            metadata = metadata_results[finfo.path]
                            if not metadata.is_damaged and not metadata.is_encrypted:
                                page_count = metadata.page_count
                                title = metadata.title.strip()
                                if len(title) >= 4 and not title.lower().startswith("untitled") and not title.lower().endswith(".pdf"):
                                    finfo.title = title
                        except Exception:
                            page_count = 0
                    elif ext in (".docx", ".xlsx", ".pptx", ".odt"):
                        try:
                            from preview_panel import extract_office_metadata
                            meta = extract_office_metadata(finfo.path)
                            page_count = meta.get("page_count", 0) or meta.get("slide_count", 0)
                        except Exception:
                            page_count = 0

                    finfo.page_count = page_count
                    if page_count > 0:
                        page_map.setdefault(page_count, []).append(finfo)

                    if idx % 10 == 0 or idx == total_c:
                        self.stage2_progress.emit(idx, total_c, f"Counting pages: {finfo.filename}", 0.0)

                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return

                for pg_cnt, files in page_map.items():
                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return
                    if len(files) < 2:
                        continue
                    clusters: List[List[PDFFileInfo]] = []
                    STOP_WORDS_PT = {
                        "the", "a", "an", "and", "of", "in", "to", "for", "with", "on", "at",
                        "by", "from", "is", "ka", "ki", "ke", "ko", "se", "aur", "ek"
                    }
                    GENERIC_VARIANTS_PT = {
                        "text", "copy", "scan", "part", "vol", "v1", "v2", "web", "compressed",
                        "print", "final", "draft", "book", "edition", "reprint", "hires", "lowres"
                    }

                    for f in sorted(files, key=lambda f: os.path.normcase(f.path)):
                        if self._is_cancelled:
                            self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                            return
                        matched_cluster = None
                        cleaned_f = clean_filename_for_similarity(f.filename)
                        words_f = set(w for w in re.split(r'\W+', cleaned_f) if len(w) >= 3 and w not in STOP_WORDS_PT)

                        for cluster in clusters:
                            rep = cluster[0]
                            cleaned_rep = clean_filename_for_similarity(rep.filename)

                            # Guard 1: Number / Volume guard (Vol 1 vs Vol 2 must never match)
                            if volumes_conflict(f.filename, rep.filename):
                                continue

                            # Guard 2: Distinctive topic keywords (e.g. 'kark' vs 'kumbh' zodiac separation)
                            words_rep = set(w for w in re.split(r'\W+', cleaned_rep) if len(w) >= 3 and w not in STOP_WORDS_PT)
                            if words_rep and words_f:
                                diff_rep = words_rep - words_f
                                diff_f = words_f - words_rep
                                if diff_rep and diff_f and not (diff_rep <= GENERIC_VARIANTS_PT or diff_f <= GENERIC_VARIANTS_PT):
                                    continue

                            # Title & filename similarity check (threshold 0.80)
                            sim = get_name_similarity_ratio(cleaned_rep, cleaned_f)
                            if sim >= 0.80:
                                matched_cluster = cluster
                                break

                            # If embedded document titles exist in both, check them as well
                            t_f = getattr(f, "title", None)
                            t_rep = getattr(rep, "title", None)
                            if t_f and t_rep:
                                c_t_f = clean_filename_for_similarity(t_f)
                                c_t_rep = clean_filename_for_similarity(t_rep)
                                if get_name_similarity_ratio(c_t_rep, c_t_f) >= 0.80:
                                    matched_cluster = cluster
                                    break
                        if matched_cluster is not None:
                            matched_cluster.append(f)
                        else:
                            clusters.append([f])

                    for cluster in clusters:
                        if len(cluster) >= 2:
                            duplicate_groups.append(DuplicateGroup(
                                size=cluster[0].size,
                                files=cluster,
                                match_type=f"Same Page Count ({pg_cnt} pgs)"
                            ))

                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return

            # MODE D: VISUAL COVER MATCH (dHash)
            elif self.scan_mode == SCAN_MODE_VISUAL_COVER:
                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return
                total_candidates = len(all_pdf_infos)
                if total_candidates > 0:
                    max_workers = min(8, max(2, (os.cpu_count() or 4)))
                    self.stage2_progress.emit(0, total_candidates, "Computing Cover dHashes...", 0.0)

                    completed_dh = 0
                    dhash_results: Dict[str, str] = {}
                    valid_infos: List[PDFFileInfo] = []

                    with concurrent.futures.ThreadPoolExecutor(max_workers=max_workers) as executor:
                        future_to_finfo = {
                            executor.submit(compute_visual_dhash, f.path, lambda: self._is_cancelled, self.pdf_pool): f 
                            for f in all_pdf_infos
                        }
                        try:
                            for future in concurrent.futures.as_completed(future_to_finfo):
                                if self._is_cancelled:
                                    executor.shutdown(wait=False, cancel_futures=True)
                                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                                    return
                                finfo = future_to_finfo[future]
                                try:
                                    dh = future.result()
                                    if dh:
                                        finfo.hash_val = dh
                                        dhash_results[finfo.path] = dh
                                        valid_infos.append(finfo)
                                    else:
                                        ext_l = os.path.splitext(finfo.path)[1].lower()
                                        is_corrupt = False
                                        if ext_l in (".pdf", ".epub", ".xps", ".cbz", ".mobi"):
                                            try:
                                                from pdf_worker import extract_pdf_metadata
                                                future = self.pdf_pool.extract_pdf_metadata(finfo.path) if self.pdf_pool else extract_pdf_metadata(finfo.path)
                                                metadata = future.result(timeout=20)
                                                is_corrupt = metadata.is_damaged
                                            except Exception:
                                                is_corrupt = True
                                        elif ext_l in (".jpg", ".jpeg", ".png", ".webp", ".bmp", ".tiff", ".gif", ".ico"):
                                            try:
                                                from PIL import Image
                                                with Image.open(finfo.path) as img:
                                                    img.verify()
                                            except Exception:
                                                is_corrupt = True

                                        if is_corrupt:
                                            finfo.issue = "Cannot Render / Corrupted File"
                                            empty_pdf_infos.append(finfo)
                                except Exception:
                                    finfo.issue = "Cannot Render / Corrupted File"
                                    empty_pdf_infos.append(finfo)

                                completed_dh += 1
                                if completed_dh % 10 == 0 or completed_dh == total_candidates:
                                    self.stage2_progress.emit(completed_dh, total_candidates, f"dHash: {finfo.filename}", 0.0)
                        finally:
                            executor.shutdown(wait=False, cancel_futures=True)

                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return

                    fingerprints = {}
                    def interior_matches(first, second):
                        documents = (".pdf", ".epub", ".xps", ".cbz", ".mobi")
                        if first.extension not in documents and second.extension not in documents:
                            return True
                        if first.extension not in documents or second.extension not in documents:
                            return False
                        from pdf_worker import extract_document_fingerprint
                        try:
                            for info in (first, second):
                                if info.path not in fingerprints:
                                    future = (self.pdf_pool.extract_document_fingerprint(info.path) if self.pdf_pool
                                              else extract_document_fingerprint(info.path))
                                    fingerprints[info.path] = future.result(timeout=20)
                            a, b = fingerprints[first.path], fingerprints[second.path]
                            first.page_count, second.page_count = a["page_count"], b["page_count"]
                            if abs(first.page_count-second.page_count) > .1 * min(first.page_count,second.page_count):
                                return False
                            return a["interior"] == b["interior"]
                        except Exception:
                            # Failed verification cannot establish a potential match.
                            return False

                    valid_infos.sort(key=lambda f: os.path.normcase(f.path))
                    # Cover candidates additionally require middle/last-page agreement.
                    visited_dh = set()
                    n_valid = len(valid_infos)
                    for i in range(n_valid):
                        if self._is_cancelled:
                            self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                            return
                        if valid_infos[i].path in visited_dh:
                            continue
                        cluster = [valid_infos[i]]
                        h1 = dhash_results[valid_infos[i].path]

                        for j in range(i + 1, n_valid):
                            if self._is_cancelled:
                                self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                                return
                            if valid_infos[j].path in visited_dh:
                                continue
                            h2 = dhash_results[valid_infos[j].path]
                            if hamming_distance(h1, h2) <= 4:
                                # Guard against different series volumes sharing identical covers
                                if volumes_conflict(valid_infos[i].filename, valid_infos[j].filename):
                                    continue

                                if not all(interior_matches(member, valid_infos[j]) for member in cluster):
                                    continue

                                cluster.append(valid_infos[j])
                                visited_dh.add(valid_infos[j].path)

                        if len(cluster) >= 2:
                            visited_dh.add(valid_infos[i].path)
                            duplicate_groups.append(DuplicateGroup(
                                size=cluster[0].size,
                                files=cluster,
                                match_type="Visual Cover Match (dHash)"
                            ))

                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return

            # MODE E: FAST SIZE ONLY (Legacy)
            elif self.scan_mode == SCAN_MODE_FAST:
                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return
                for size, files in size_map.items():
                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return
                    if len(files) >= 2:
                        duplicate_groups.append(DuplicateGroup(size=size, files=files, match_type="Exact Size"))

                if self._is_cancelled:
                    self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                    return

            # MODE D: SAFE MODE (Default) OR FULL_HASH MODE
            else:
                cache = get_hash_cache() if self.use_cache else None

                # Drive-aware worker pool to prevent rotational HDD head thrashing
                if self.concurrency is not None and self.concurrency > 0:
                    max_workers = self.concurrency
                else:
                    is_all_ssd = all(is_path_on_ssd(r) for r in self.root_paths) if self.root_paths else True
                    if is_all_ssd:
                        max_workers = min(16, (os.cpu_count() or 4) * 2)
                    else:
                        max_workers = 2  # Throttled to 2 workers to prevent mechanical disk seek thrashing

                # Collect all candidate files with shared sizes
                candidate_groups_by_size: Dict[int, List[PDFFileInfo]] = {
                    sz: files for sz, files in size_map.items() if len(files) >= 2
                }

                all_quick_candidates = [f for files in candidate_groups_by_size.values() for f in files]
                total_quick = len(all_quick_candidates)
                if total_quick > 0:
                    self.stage2_progress.emit(0, total_quick, "Starting Quick Hash verification...", 0.0)

                if self.scan_mode == SCAN_MODE_FULL_HASH:
                    # Full Hash directly on all size candidates
                    sha_candidates = all_quick_candidates
                else:
                    # 1. Parallel Quick Partial Hash Pass (with instant batch cache check)
                    quick_hash_by_size: Dict[int, Dict[str, List[PDFFileInfo]]] = {}
                    uncached_quick: List[PDFFileInfo] = []

                    cached_quick_batch = cache.get_hashes_batch([(f.path, f.size, f.mtime) for f in all_quick_candidates]) if cache else {}

                    for f in all_quick_candidates:
                        c_entry = cached_quick_batch.get(os.path.abspath(f.path))
                        if c_entry and c_entry[0]:
                            f.quick_hash = c_entry[0]
                            f.sha256 = c_entry[1]
                            f.hash_val = c_entry[0]
                            quick_hash_by_size.setdefault(f.size, {}).setdefault(c_entry[0], []).append(f)
                        else:
                            uncached_quick.append(f)

                    processed_q = total_quick - len(uncached_quick)

                    if uncached_quick and not self._is_cancelled:
                        def _do_quick_hash(f: PDFFileInfo) -> Tuple[PDFFileInfo, str]:
                            if self._is_cancelled:
                                return f, ""
                            h_val = compute_quick_hash(f.path, f.size, cancel_check=lambda: self._is_cancelled)
                            return f, h_val

                        new_cache_records = []
                        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
                        try:
                            future_to_file = {executor.submit(_do_quick_hash, f): f for f in uncached_quick}
                            for future in concurrent.futures.as_completed(future_to_file):
                                if self._is_cancelled:
                                    for fut in future_to_file:
                                        fut.cancel()
                                    executor.shutdown(wait=False, cancel_futures=True)
                                    break
                                try:
                                    finfo, qhash = future.result()
                                    finfo.quick_hash = qhash
                                    finfo.hash_val = qhash
                                    if qhash:
                                        quick_hash_by_size.setdefault(finfo.size, {}).setdefault(qhash, []).append(finfo)
                                        new_cache_records.append((finfo.path, finfo.size, finfo.mtime, qhash, None))
                                except Exception:
                                    pass
                                processed_q += 1
                                if processed_q % 10 == 0 or processed_q == total_quick:
                                    self.stage2_progress.emit(processed_q, total_quick, finfo.filename, 0.0)
                        finally:
                            executor.shutdown(wait=False, cancel_futures=True)

                        if cache and new_cache_records and not self._is_cancelled:
                            cache.batch_set(new_cache_records)

                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return

                    # Filter down to candidates matching in quick hash
                    sha_candidates = []
                    for sz, q_map in quick_hash_by_size.items():
                        for qh, matching_files in q_map.items():
                            if len(matching_files) >= 2:
                                sha_candidates.extend(matching_files)

                # 2. Parallel Full SHA-256 Pass (with instant batch cache check)
                total_sha = len(sha_candidates)
                if total_sha > 0 and not self._is_cancelled:
                    self.stage2_progress.emit(0, total_sha, "Starting Full SHA-256 confirmation...", 0.0)
                    sha_start_time = time.time()
                    bytes_hashed = 0

                    cached_sha_batch = cache.get_hashes_batch([(f.path, f.size, f.mtime) for f in sha_candidates]) if cache else {}

                    sha_by_size: Dict[int, Dict[str, List[PDFFileInfo]]] = {}
                    uncached_sha: List[PDFFileInfo] = []
                    processed_sha = 0

                    for f in sha_candidates:
                        c_entry = cached_sha_batch.get(os.path.abspath(f.path))
                        if c_entry and c_entry[1]:
                            f.sha256 = c_entry[1]
                            f.hash_val = c_entry[1]
                            sha_by_size.setdefault(f.size, {}).setdefault(c_entry[1], []).append(f)
                            processed_sha += 1
                        else:
                            uncached_sha.append(f)

                    if uncached_sha and not self._is_cancelled:
                        def _do_full_sha(f: PDFFileInfo) -> Tuple[PDFFileInfo, str, int]:
                            if self._is_cancelled:
                                return f, "", 0
                            h_val = compute_full_sha256(f.path, cancel_check=lambda: self._is_cancelled)
                            return f, h_val, f.size

                        new_sha_records = []
                        executor = concurrent.futures.ThreadPoolExecutor(max_workers=max_workers)
                        try:
                            future_to_sha = {executor.submit(_do_full_sha, f): f for f in uncached_sha}
                            for future in concurrent.futures.as_completed(future_to_sha):
                                if self._is_cancelled:
                                    for fut in future_to_sha:
                                        fut.cancel()
                                    executor.shutdown(wait=False, cancel_futures=True)
                                    break
                                try:
                                    finfo, sha_val, b_read = future.result()
                                    finfo.sha256 = sha_val
                                    finfo.hash_val = sha_val
                                    bytes_hashed += b_read
                                    if sha_val:
                                        sha_by_size.setdefault(finfo.size, {}).setdefault(sha_val, []).append(finfo)
                                        new_sha_records.append((finfo.path, finfo.size, finfo.mtime, finfo.quick_hash, sha_val))
                                except Exception:
                                    pass
                                processed_sha += 1
                                elapsed_sha = time.time() - sha_start_time
                                speed_mb = (bytes_hashed / (1024 * 1024)) / max(0.01, elapsed_sha)
                                self.stage2_progress.emit(processed_sha, total_sha, finfo.filename, speed_mb)
                        finally:
                            executor.shutdown(wait=False, cancel_futures=True)

                        if cache and new_sha_records and not self._is_cancelled:
                            cache.batch_set(new_sha_records)

                    if self._is_cancelled:
                        self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                        return

                    for sz, sha_map in sha_by_size.items():
                        for sha_val, verified_files in sha_map.items():
                            if len(verified_files) >= 2:
                                duplicate_groups.append(
                                    DuplicateGroup(size=sz, files=verified_files, match_type="100% Exact Hash")
                                )

            if self._is_cancelled:
                self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                return

            # Include 0-byte files or corrupted unreadable files group if requested
            true_empty = [f for f in empty_pdf_infos if f.size == 0]

            if len(true_empty) >= 2 and self.include_empty_files:
                duplicate_groups.append(
                    DuplicateGroup(size=0, files=true_empty, match_type="0-Byte Empty Files")
                )

            # Sort groups by wasted space descending
            duplicate_groups.sort(key=lambda g: g.wasted_bytes, reverse=True)

            if self._is_cancelled:
                self.scan_cancelled.emit(total_target_files, time.time() - start_time)
                return

            self.duplicate_groups = duplicate_groups
            elapsed = time.time() - start_time
            if empty_pdf_infos:
                self.corrupt_files_found.emit(empty_pdf_infos)
            self.scan_finished.emit(duplicate_groups, total_scanned_files, total_target_files, elapsed)

        except Exception as e:
            self.scan_error.emit(str(e))


class CzkawkaScanWorker(FastScanWorker):
    """Qt-compatible facade: Python discovers metadata; Czkawka owns content hashing."""
    cli_progress = Signal(object)

    def __init__(self, *args, **kwargs):
        super().__init__(*args, **kwargs)
        from czkawka_adapter import CzkawkaScanAdapter
        self.adapter = CzkawkaScanAdapter()
        self._discovery = None
        self._scan_future = None

    def cancel(self):
        super().cancel()
        if self._discovery:
            self._discovery.cancel()
        self.adapter.cancel()

    def run(self):
        from czkawka_adapter import CzkawkaScanCancelled
        started = time.monotonic()
        discovered, failures, issues = [], [], []
        try:
            self._discovery = FastScanWorker(
                self.root_paths, scan_mode=SCAN_MODE_FAST, category=self.category,
                custom_extensions=self.target_extensions, min_size_bytes=self.min_size_bytes,
                exclude_dirs=self.exclude_dirs, use_cache=False, include_empty_files=self.include_empty_files
            )
            self._discovery.progress.connect(self.progress.emit)
            self._discovery.scan_finished.connect(lambda *result: discovered.append(result))
            self._discovery.scan_error.connect(failures.append)
            self._discovery.corrupt_files_found.connect(issues.extend)
            self._discovery.run()
            if self._is_cancelled:
                raise CzkawkaScanCancelled("Cancelled during discovery")
            if failures or not discovered:
                raise RuntimeError("; ".join(failures) or "File discovery did not complete")
            names = sorted(name for name in self.exclude_dirs if not os.path.isabs(name))
            patterns = [pattern for name in names for pattern in (f"*/{name}/*", f"*\\{name}\\*")]
            patterns.extend(("*/.*/*", "*\\.*\\*"))
            self._scan_future = self.adapter.scan(
                self.root_paths, excluded_directories=[p for p in self.exclude_dirs if os.path.isabs(p)],
                allowed_extensions=self.target_extensions, minimal_file_size=max(1, self.min_size_bytes),
                thread_number=self.concurrency or 2, excluded_items=patterns, on_progress=self.cli_progress.emit
            )
            if self._is_cancelled:
                self.adapter.cancel()
            groups = self._scan_future.result()
            if self._is_cancelled:
                raise CzkawkaScanCancelled("Cancelled before accepting results")
            groups.extend(g for g in discovered[0][0] if g.match_type == "0-Byte Empty Files")
            self.duplicate_groups = groups
            if issues:
                self.corrupt_files_found.emit(issues)
            self.scan_finished.emit(groups, discovered[0][1], discovered[0][2], time.monotonic() - started)
        except CzkawkaScanCancelled:
            self.scan_cancelled.emit(0, time.monotonic() - started)
        except Exception as error:
            if self._is_cancelled:
                self.scan_cancelled.emit(0, time.monotonic() - started)
            else:
                self.scan_error.emit(str(error))
        finally:
            self.adapter.close()
