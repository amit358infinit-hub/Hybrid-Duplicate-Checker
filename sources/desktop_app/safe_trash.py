"""
Safe Trash & Restore Manager.
Supports:
1. Safe move to '_deleted_pdfs' with full manifest & 1-click restore.
2. Windows Recycle Bin deletion (via send2trash).
3. Move to custom archive folder.
4. Smart Auto-Select sorting rules.
"""
from __future__ import annotations

import json
import hashlib
import os
import re
import shutil
import tempfile
import time
from dataclasses import asdict, dataclass
from typing import Dict, List, Optional, Tuple, Union

try:
    import send2trash
except ImportError:
    send2trash = None


def win_long_path(path: str) -> str:
    """
    Normalizes and adds Windows extended-length path prefix (\\\\?\\) if needed.
    Ensures safe handling of paths exceeding the traditional MAX_PATH (260 chars) limit.
    Correctly handles standard drives (C:\\...), UNC network shares (\\\\server\\share),
    and already-prefixed paths.
    """
    if not isinstance(path, str) or not path:
        return path
    if os.name != "nt":
        return path

    abs_p = os.path.abspath(path)
    if abs_p.startswith("\\\\?\\"):
        return abs_p

    if abs_p.startswith("\\\\"):
        # UNC path: \\server\share -> \\?\UNC\server\share
        return "\\\\?\\UNC\\" + abs_p[2:]
    else:
        # Standard drive: C:\path -> \\?\C:\path
        return "\\\\?\\" + abs_p


@dataclass
class DeletedRecord:
    id: str
    original_path: str
    deleted_path: str
    deleted_time: str
    size: int
    filename: str
    root_dir: str = ""


class SafeTrashManager:
    DELETED_DIR_NAME = "_deleted_files"
    LEGACY_DELETED_DIR_NAME = "_deleted_pdfs"
    MANIFEST_FILE = ".deleted_manifest.json"

    @staticmethod
    def _sha256_file(path: str) -> str:
        digest = hashlib.sha256()
        with open(win_long_path(path), "rb") as f:
            for chunk in iter(lambda: f.read(1024 * 1024), b""):
                digest.update(chunk)
        return digest.hexdigest()

    @classmethod
    def _write_manifest_atomic(cls, manifest_path: str, records: List[DeletedRecord]) -> None:
        """Write a complete manifest beside the old one, then atomically swap it in."""
        trash_dir = os.path.dirname(manifest_path)
        os.makedirs(trash_dir, exist_ok=True)
        fd, temp_path = tempfile.mkstemp(prefix=".manifest_", suffix=".tmp", dir=trash_dir)
        try:
            with os.fdopen(fd, "w", encoding="utf-8") as f:
                json.dump([asdict(r) for r in records], f, indent=2, ensure_ascii=False)
                f.flush()
                os.fsync(f.fileno())
            os.replace(temp_path, manifest_path)
        finally:
            if os.path.exists(temp_path):
                os.remove(temp_path)

    @classmethod
    def get_trash_dir(cls, root_dir: str) -> str:
        # If legacy _deleted_pdfs exists with manifest, keep using it for continuity
        legacy_path = os.path.join(root_dir, cls.LEGACY_DELETED_DIR_NAME)
        if os.path.isdir(legacy_path):
            return legacy_path
        return os.path.join(root_dir, cls.DELETED_DIR_NAME)

    @classmethod
    def get_manifest_path(cls, root_dir: str) -> str:
        return os.path.join(cls.get_trash_dir(root_dir), cls.MANIFEST_FILE)

    @classmethod
    def load_manifest(cls, root_dir: str) -> List[DeletedRecord]:
        manifest_path = cls.get_manifest_path(root_dir)
        if not os.path.isfile(manifest_path):
            return []
        try:
            with open(manifest_path, 'r', encoding='utf-8') as f:
                data = json.load(f)
                return [DeletedRecord(**item) for item in data]
        except Exception:
            return []

    @classmethod
    def save_manifest(cls, root_dir: str, records: List[DeletedRecord]) -> None:
        trash_dir = cls.get_trash_dir(root_dir)
        os.makedirs(trash_dir, exist_ok=True)
        manifest_path = cls.get_manifest_path(root_dir)
        try:
            cls._write_manifest_atomic(manifest_path, records)
        except Exception as e:
            print(f"Error saving manifest: {e}")

    @classmethod
    def get_trash_dir_for_file(cls, fpath: str, root_dirs: List[str]) -> Tuple[str, str]:
        """
        Determines the appropriate trash directory and base folder for a given file.
        Guarantees that file stays on the SAME filesystem/drive to prevent cross-drive
        copying and Windows 'different mount' ValueError.
        Returns: (trash_directory, matched_base_dir)
        """
        fpath_abs = os.path.abspath(fpath)
        file_drive = os.path.splitdrive(fpath_abs)[0].lower()

        # Try to find a matching root_dir on the same drive
        best_root = None
        for r in root_dirs:
            if not r:
                continue
            r_abs = os.path.abspath(r)
            if os.path.splitdrive(r_abs)[0].lower() == file_drive:
                try:
                    rel = os.path.relpath(fpath_abs, r_abs)
                    if not rel.startswith('..'):
                        # Inside this root
                        if best_root is None or len(r_abs) > len(best_root):
                            best_root = r_abs
                except ValueError:
                    continue

        if best_root:
            return cls.get_trash_dir(best_root), best_root

        # Fallback: keep on the file's own directory/drive
        file_dir = os.path.dirname(fpath_abs)
        fallback_trash = os.path.join(file_dir, cls.DELETED_DIR_NAME)
        return fallback_trash, file_dir

    @classmethod
    def find_trash_dir_for_record(cls, record: DeletedRecord, root_dirs: List[str] = None) -> str:
        """Finds the trash folder containing this record's deleted file."""
        if record.root_dir and os.path.isdir(cls.get_trash_dir(record.root_dir)):
            return cls.get_trash_dir(record.root_dir)

        # Look up parent directory hierarchy for '_deleted_files' or legacy '_deleted_pdfs'
        curr = os.path.dirname(os.path.abspath(record.deleted_path))
        while curr:
            bname = os.path.basename(curr).lower()
            if bname in (cls.DELETED_DIR_NAME.lower(), cls.LEGACY_DELETED_DIR_NAME.lower()):
                return curr
            parent = os.path.dirname(curr)
            if parent == curr:
                break
            curr = parent

        if root_dirs:
            for r in root_dirs:
                t = cls.get_trash_dir(r)
                if os.path.isfile(os.path.join(t, cls.MANIFEST_FILE)):
                    return t

        return os.path.dirname(os.path.abspath(record.deleted_path))

    @classmethod
    def safe_delete_files(cls, file_paths: List[str], root_dirs: Union[str, List[str]], *, successful_paths: Optional[List[str]] = None) -> Tuple[int, List[str], List[DeletedRecord]]:
        """
        Moves the list of file paths into their corresponding '_deleted_pdfs/' folder.
        Preserves relative folder paths, guarantees drive-local movement, and writes manifests.
        Returns: (success_count, list_of_errors, list_of_created_records)
        """
        if isinstance(root_dirs, str):
            roots = [root_dirs] if root_dirs else []
        else:
            roots = list(root_dirs) if root_dirs else []

        committed_records: List[DeletedRecord] = []
        errors: List[str] = []
        success_count = 0
        records_by_trash: Dict[str, List[DeletedRecord]] = {}

        timestamp_str = time.strftime("%Y%m%d_%H%M%S")

        for fpath in file_paths:
            if not os.path.isfile(fpath):
                errors.append(f"File not found: {fpath}")
                continue

            try:
                stat = os.stat(fpath)
                fsize = stat.st_size
                fname = os.path.basename(fpath)

                trash_dir, base_dir = cls.get_trash_dir_for_file(fpath, roots)
                os.makedirs(trash_dir, exist_ok=True)

                # Compute relative path from base to preserve folder structure
                try:
                    rel_dir = os.path.relpath(os.path.dirname(os.path.abspath(fpath)), base_dir)
                    if rel_dir == '.' or rel_dir.startswith('..'):
                        dest_subfolder = trash_dir
                    else:
                        dest_subfolder = os.path.join(trash_dir, rel_dir)
                except Exception:
                    dest_subfolder = trash_dir

                os.makedirs(dest_subfolder, exist_ok=True)
                dest_file_path = os.path.join(dest_subfolder, fname)

                # Handle collision if file already exists in trash
                if os.path.exists(dest_file_path):
                    base, ext = os.path.splitext(fname)
                    counter = 1
                    while os.path.exists(dest_file_path):
                        dest_file_path = os.path.join(
                            dest_subfolder, f"{base}_{timestamp_str}_{counter}{ext}"
                        )
                        counter += 1

                # Safely move file within the same filesystem
                shutil.move(fpath, dest_file_path)

                rec = DeletedRecord(
                    id=f"{time.time():.6f}_{success_count}",
                    original_path=os.path.abspath(fpath),
                    deleted_path=os.path.abspath(dest_file_path),
                    deleted_time=time.strftime("%Y-%m-%d %H:%M:%S"),
                    size=fsize,
                    filename=fname,
                    root_dir=base_dir
                )
                records_by_trash.setdefault(trash_dir, []).append(rec)
                success_count += 1

            except Exception as e:
                errors.append(f"Failed to move {os.path.basename(fpath)}: {str(e)}")

        # Batch save manifests for all affected trash directories
        for trash_dir, recs in records_by_trash.items():
            manifest_path = os.path.join(trash_dir, cls.MANIFEST_FILE)
            existing = []
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    if not isinstance(data, list):
                        raise ValueError("Manifest root must be a JSON list")
                    existing = [DeletedRecord(**item) for item in data]
                except Exception as e:
                    errors.append(f"Existing restore manifest is unreadable in {trash_dir}: {e}")
                    existing = None

            if existing is None:
                write_error = "The existing manifest was left untouched because it could not be read."
            else:
                try:
                    cls._write_manifest_atomic(manifest_path, existing + recs)
                    committed_records.extend(recs)
                    continue
                except Exception as e:
                    write_error = f"Error saving restore manifest in {trash_dir}: {e}"

            # Do not report a successful move when no restore record was
            # committed. Roll the new files back to their original locations.
            errors.append(write_error)
            for rec in reversed(recs):
                try:
                    if os.path.exists(rec.original_path):
                        raise FileExistsError(f"Original path already exists: {rec.original_path}")
                    os.makedirs(os.path.dirname(rec.original_path), exist_ok=True)
                    shutil.move(rec.deleted_path, rec.original_path)
                    success_count -= 1
                except Exception as e:
                    errors.append(
                        f"Could not roll back {rec.filename}; it remains at {rec.deleted_path}: {e}"
                    )

        if successful_paths is not None:
            successful_paths.extend(rec.original_path for rec in committed_records)
        return len(committed_records), errors, committed_records

    @classmethod
    def send_to_recycle_bin(cls, file_paths: List[str], *, successful_paths: Optional[List[str]] = None) -> Tuple[int, List[str]]:
        """Safely sends files to the Windows Recycle Bin."""
        if send2trash is None:
            return 0, ["send2trash module is not available on this system."]

        success_count = 0
        errors: List[str] = []

        for fpath in file_paths:
            if not os.path.isfile(fpath):
                errors.append(f"File not found: {fpath}")
                continue
            try:
                send2trash.send2trash(os.path.normpath(fpath))
                success_count += 1
                if successful_paths is not None:
                    successful_paths.append(os.path.abspath(fpath))
            except Exception as e:
                errors.append(f"Failed to send to recycle bin {os.path.basename(fpath)}: {str(e)}")

        return success_count, errors

    @classmethod
    def move_to_custom_folder(cls, file_paths: List[str], target_folder: str, *, successful_paths: Optional[List[str]] = None) -> Tuple[int, List[str]]:
        """Moves duplicate files to a custom user-chosen archive directory."""
        if not os.path.isdir(target_folder):
            try:
                os.makedirs(target_folder, exist_ok=True)
            except Exception as e:
                return 0, [f"Could not create target directory: {str(e)}"]

        success_count = 0
        errors: List[str] = []
        timestamp_str = time.strftime("%Y%m%d_%H%M%S")

        for fpath in file_paths:
            if not os.path.isfile(fpath):
                errors.append(f"File not found: {fpath}")
                continue
            try:
                fname = os.path.basename(fpath)
                dest = os.path.join(target_folder, fname)
                if os.path.exists(dest):
                    base, ext = os.path.splitext(fname)
                    counter = 1
                    while os.path.exists(dest):
                        dest = os.path.join(target_folder, f"{base}_{timestamp_str}_{counter}{ext}")
                        counter += 1
                shutil.move(fpath, dest)
                success_count += 1
                if successful_paths is not None:
                    successful_paths.append(os.path.abspath(fpath))
            except Exception as e:
                errors.append(f"Failed to move {os.path.basename(fpath)}: {str(e)}")

        return success_count, errors

    @classmethod
    def restore_record(cls, record: Union[DeletedRecord, str], root_dir: Union[str, List[str]] = "") -> Tuple[bool, str]:
        """Restores a single deleted file back to its original location."""
        if isinstance(record, str):
            record_id = record
            roots = [root_dir] if isinstance(root_dir, str) else (root_dir or [])
            found_rec = None
            for r in roots:
                if not r or not os.path.isdir(r):
                    continue
                for rec in cls.load_manifest(r):
                    if rec.id == record_id:
                        found_rec = rec
                        break
                if found_rec:
                    break
            if not found_rec:
                return False, f"Record with ID '{record_id}' not found in manifests"
            record = found_rec

        if not os.path.isfile(record.deleted_path):
            return False, f"File no longer exists in deleted folder: {record.deleted_path}"

        orig_dir = os.path.dirname(record.original_path)
        try:
            os.makedirs(orig_dir, exist_ok=True)

            target_path = record.original_path
            # If a file already exists at original location, append suffix
            if os.path.exists(target_path):
                base, ext = os.path.splitext(target_path)
                target_path = f"{base}_restored_{int(time.time())}{ext}"

            shutil.move(record.deleted_path, target_path)

            # Determine trash directory and update manifest
            trash_dir = cls.find_trash_dir_for_record(record, [root_dir] if root_dir else None)
            manifest_path = os.path.join(trash_dir, cls.MANIFEST_FILE)
            if os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    updated = [DeletedRecord(**r) for r in data if r.get("id") != record.id]
                    cls._write_manifest_atomic(manifest_path, updated)
                except Exception:
                    pass

            return True, target_path
        except Exception as e:
            return False, str(e)

    @classmethod
    def restore_multiple(cls, records: List[DeletedRecord], root_dirs: Union[str, List[str]] = None) -> Tuple[int, List[str]]:
        """
        O(N) batch restore of multiple records with single-pass manifest writing.
        Eliminates O(N^2) file I/O bottleneck.
        """
        if isinstance(root_dirs, str):
            roots = [root_dirs] if root_dirs else []
        else:
            roots = list(root_dirs) if root_dirs else []

        success = 0
        errors = []

        # Group records by their trash directory to batch update manifests
        records_by_trash: Dict[str, List[DeletedRecord]] = {}
        for rec in records:
            t_dir = cls.find_trash_dir_for_record(rec, roots)
            records_by_trash.setdefault(t_dir, []).append(rec)

        for trash_dir, rec_list in records_by_trash.items():
            manifest_path = os.path.join(trash_dir, cls.MANIFEST_FILE)
            restored_ids = set()

            for rec in rec_list:
                if not os.path.isfile(rec.deleted_path):
                    errors.append(f"{rec.filename}: File no longer exists in trash: {rec.deleted_path}")
                    continue

                orig_dir = os.path.dirname(rec.original_path)
                try:
                    os.makedirs(orig_dir, exist_ok=True)
                    target_path = rec.original_path
                    if os.path.exists(target_path):
                        base, ext = os.path.splitext(target_path)
                        target_path = f"{base}_restored_{int(time.time())}{ext}"

                    shutil.move(rec.deleted_path, target_path)
                    restored_ids.add(rec.id)
                    success += 1
                except Exception as e:
                    errors.append(f"{rec.filename}: {str(e)}")

            # Single batch rewrite of this trash dir's manifest
            if restored_ids and os.path.isfile(manifest_path):
                try:
                    with open(manifest_path, 'r', encoding='utf-8') as f:
                        data = json.load(f)
                    remaining = [DeletedRecord(**item) for item in data if item.get("id") not in restored_ids]
                    cls._write_manifest_atomic(manifest_path, remaining)
                except Exception as e:
                    errors.append(f"Failed to update manifest in {trash_dir}: {e}")

        return success, errors

    @classmethod
    def sort_files_by_rule(cls, files: list, rule: str, preferred_folder: str = "") -> list:
        """
        Sorts files so that the recommended file to KEEP is at index 0.
        All remaining files (index 1..) will be marked for deletion.
        """
        result = list(files)
        if not result:
            return result

        if rule == "newest":
            result.sort(key=lambda f: f.mtime, reverse=True)
        elif rule == "oldest":
            result.sort(key=lambda f: f.mtime)
        elif rule == "shortest_path":
            result.sort(key=lambda f: len(f.path))
        elif rule == "cleanest_name":
            # Prefers names WITHOUT copy markers like (1), _copy, - copy, and shorter name
            def clean_score(f):
                name = f.filename.lower()
                has_copy_marker = 1 if re.search(r'[\(\[\{]?(?:copy|\d+)[\)\]\}]?', name) else 0
                return (has_copy_marker, len(f.filename))
            result.sort(key=clean_score)
        elif rule == "largest_file":
            result.sort(key=lambda f: f.size, reverse=True)
        elif rule == "smallest_file":
            result.sort(key=lambda f: f.size)
        elif rule in ("master_folder", "preferred_folder") and preferred_folder:
            pref = os.path.normpath(preferred_folder).lower()
            def master_score(f):
                f_norm = os.path.normpath(f.path).lower()
                is_master = 0 if (f_norm.startswith(pref + os.sep) or f_norm == pref or os.path.normpath(f.folder).lower().startswith(pref)) else 1
                return (is_master, len(f.path))
            result.sort(key=master_score)
        elif rule == "longest_path":
            result.sort(key=lambda f: len(f.path), reverse=True)
        elif rule in ("first", "first_found", "keep_first"):
            # Keeps the first file discovered in original scan order
            return result

        return result

    @classmethod
    def write_audit_log(cls, root_dir: Union[str, List[str]], action: str, details: Union[str, List[str]]) -> None:
        """Appends timestamped operations into audit_log.txt inside _deleted_files."""
        if isinstance(root_dir, list):
            primary = root_dir[0] if root_dir else "."
        else:
            primary = root_dir if root_dir else "."

        trash_dir = cls.get_trash_dir(primary)
        try:
            os.makedirs(trash_dir, exist_ok=True)
            log_path = os.path.join(trash_dir, "audit_log.txt")
            ts = time.strftime("%Y-%m-%d %H:%M:%S")
            lines = []
            if isinstance(details, list):
                for d in details:
                    lines.append(f"[{ts}] [{action}] {d}\n")
            else:
                lines.append(f"[{ts}] [{action}] {details}\n")

            with open(log_path, 'a', encoding='utf-8') as f:
                f.writelines(lines)
        except Exception:
            pass

    @classmethod
    def replace_with_hardlinks(cls, pairs: List[Tuple[str, str]], root_dir: Union[str, List[str]] = "", *, successful_paths: Optional[List[str]] = None) -> Tuple[int, List[str]]:
        """
        Replaces duplicate files with NTFS Hardlinks to the original file.
        pairs: List of (source_original_path, target_duplicate_path).
        Returns: (success_count, list_of_errors)
        """
        success_count = 0
        errors = []
        log_entries = []

        for src, tgt in pairs:
            src_abs = os.path.abspath(src)
            tgt_abs = os.path.abspath(tgt)

            if src_abs == tgt_abs:
                continue

            if not os.path.isfile(win_long_path(src_abs)):
                errors.append(f"Source file not found: {src}")
                continue
            if not os.path.isfile(win_long_path(tgt_abs)):
                errors.append(f"Target file not found: {tgt}")
                continue

            # Check cross-drive boundary (NTFS hardlinks only work within the same volume/drive)
            src_drive, _ = os.path.splitdrive(src_abs)
            tgt_drive, _ = os.path.splitdrive(tgt_abs)
            if src_drive.lower() != tgt_drive.lower():
                errors.append(f"Cannot hardlink across different drives ({src_drive} vs {tgt_drive}): {tgt}")
                continue

            # Check if files already point to the same inode
            try:
                src_stat = os.stat(win_long_path(src_abs))
                tgt_stat = os.stat(win_long_path(tgt_abs))
                if src_stat.st_size != tgt_stat.st_size:
                    errors.append(f"Safety check failed: File sizes differ ({src_stat.st_size} vs {tgt_stat.st_size} bytes) for {os.path.basename(tgt)}")
                    continue
                if src_stat.st_ino != 0 and (src_stat.st_dev, src_stat.st_ino) == (tgt_stat.st_dev, tgt_stat.st_ino):
                    success_count += 1
                    if successful_paths is not None:
                        successful_paths.append(tgt_abs)
                    continue
            except Exception:
                pass

            try:
                if cls._sha256_file(src_abs) != cls._sha256_file(tgt_abs):
                    errors.append(
                        f"Safety check failed: SHA-256 differs for {os.path.basename(tgt)}; target was left unchanged."
                    )
                    continue
            except Exception as e:
                errors.append(f"Could not verify SHA-256 for {os.path.basename(tgt)}: {e}")
                continue

            # Atomic hardlink replace using temp backup
            temp_backup = tgt_abs + f".hl_tmp_{int(time.time()*1000)}"
            try:
                os.rename(win_long_path(tgt_abs), win_long_path(temp_backup))
                try:
                    os.link(win_long_path(src_abs), win_long_path(tgt_abs))
                    os.remove(win_long_path(temp_backup))
                    success_count += 1
                    if successful_paths is not None:
                        successful_paths.append(tgt_abs)
                    log_entries.append(f"Hardlinked '{tgt_abs}' <== '{src_abs}'")
                except Exception as e:
                    if os.path.exists(win_long_path(temp_backup)):
                        os.rename(win_long_path(temp_backup), win_long_path(tgt_abs))
                    errors.append(f"Hardlink failed for {os.path.basename(tgt)}: {str(e)}")
            except Exception as e:
                errors.append(f"Failed to stage file for hardlink {os.path.basename(tgt)}: {str(e)}")

        if log_entries and root_dir:
            cls.write_audit_log(root_dir, "HARDLINK", log_entries)

        return success_count, errors

    @classmethod
    def clean_empty_folders(cls, root_dirs: Union[str, List[str]], exclude_dirs: Optional[Set[str]] = None) -> Tuple[int, List[str]]:
        """
        Recursively removes empty directories within root_dirs (bottom-up).
        Does not delete root_dirs themselves or excluded/system folders.
        Returns: (removed_count, list_of_removed_dirs)
        """
        if isinstance(root_dirs, str):
            roots = [os.path.abspath(root_dirs)] if root_dirs else []
        else:
            roots = [os.path.abspath(r) for r in root_dirs if r]

        excludes = {cls.DELETED_DIR_NAME.lower(), "$recycle.bin", ".git", "__pycache__"}
        if exclude_dirs:
            excludes.update(d.lower() for d in exclude_dirs)

        removed = []
        for r in roots:
            if not os.path.isdir(win_long_path(r)):
                continue

            for dirpath, dirnames, filenames in os.walk(win_long_path(r), topdown=False):
                if os.path.abspath(dirpath).lower() == r.lower():
                    continue

                base_name = os.path.basename(dirpath).lower()
                if base_name in excludes:
                    continue

                try:
                    items = os.listdir(win_long_path(dirpath))
                    if not items:
                        os.rmdir(win_long_path(dirpath))
                        removed.append(dirpath)
                except Exception:
                    pass

        if removed and roots:
            cls.write_audit_log(roots[0], "CLEAN_EMPTY", f"Removed {len(removed)} empty directories.")

        return len(removed), removed
