"""
SQLite-backed Hash Cache Engine for Fast PDF & File Duplicate Finder.
Caches quick hashes and full SHA-256 hashes by (path, size, mtime).
Caches verified file hashes to reduce work on repeated scans.
"""
from __future__ import annotations

import os
import sqlite3
import threading
import time
from typing import Dict, List, Optional, Tuple


def _norm_path(p: str) -> str:
    """Returns canonical normalized path for robust cross-case Windows file lookups."""
    return os.path.normcase(os.path.abspath(p))


class HashCacheManager:
    _instance: Optional[HashCacheManager] = None
    _lock = threading.Lock()

    def __init__(self, db_path: Optional[str] = None):
        if db_path is None:
            # Store in Local AppData on Windows to keep scanned folders clean
            app_dir = os.path.join(
                os.getenv("LOCALAPPDATA", os.path.expanduser("~")),
                "HybridDuplicateChecker"
            )
            os.makedirs(app_dir, exist_ok=True)
            self.db_path = os.path.join(app_dir, "file_hash_cache.db")
        else:
            self.db_path = db_path

        self._local = threading.local()
        self._init_db()

    def _get_connection(self) -> sqlite3.Connection:
        if not hasattr(self._local, "conn") or self._local.conn is None:
            conn = sqlite3.connect(self.db_path, timeout=30.0)
            conn.execute("PRAGMA journal_mode=WAL;")
            conn.execute("PRAGMA synchronous=NORMAL;")
            self._local.conn = conn
        return self._local.conn

    def _init_db(self):
        with self._lock:
            conn = self._get_connection()
            with conn:
                conn.execute("""
                    CREATE TABLE IF NOT EXISTS file_cache (
                        path TEXT PRIMARY KEY COLLATE NOCASE,
                        size INTEGER NOT NULL,
                        mtime REAL NOT NULL,
                        quick_hash TEXT,
                        sha256 TEXT,
                        cached_at REAL NOT NULL
                    );
                """)
                conn.execute("""
                    CREATE INDEX IF NOT EXISTS idx_lookup 
                    ON file_cache(path COLLATE NOCASE, size, mtime);
                """)
                if conn.execute("PRAGMA user_version").fetchone()[0] < 1:
                    # Older writers could associate stale hashes with new metadata.
                    conn.execute("DELETE FROM file_cache")
                    conn.execute("PRAGMA user_version = 1")

    def get_hashes(self, path: str, size: int, mtime: float) -> Tuple[Optional[str], Optional[str]]:
        """
        Retrieves cached (quick_hash, sha256) if file size and mtime match.
        Returns: (quick_hash, sha256) or (None, None)
        """
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            cur.execute(
                "SELECT quick_hash, sha256 FROM file_cache WHERE path = ? COLLATE NOCASE AND size = ? AND mtime = ?",
                (_norm_path(path), size, mtime)
            )
            row = cur.fetchone()
            if row:
                return row[0], row[1]
        except Exception:
            pass
        return None, None

    def get_hashes_batch(self, items: List[Tuple[str, int, float]]) -> Dict[str, Tuple[Optional[str], Optional[str]]]:
        """
        Batch retrieves cached hashes for multiple files in one roundtrip.
        Significantly reduces SQLite transaction latency on 10,000+ files.
        Returns dict: {path: (quick_hash, sha256)}
        """
        results: Dict[str, Tuple[Optional[str], Optional[str]]] = {}
        if not items:
            return results
        try:
            conn = self._get_connection()
            cur = conn.cursor()
            for i in range(0, len(items), 500):
                chunk = items[i:i + 500]
                placeholders = ",".join(["?"] * len(chunk))
                paths = [_norm_path(p) for p, _, _ in chunk]
                cur.execute(f"SELECT path, size, mtime, quick_hash, sha256 FROM file_cache WHERE path IN ({placeholders})", paths)
                item_map = {_norm_path(p): (s, m) for p, s, m in chunk}
                orig_path_map = {_norm_path(p): os.path.abspath(p) for p, _, _ in chunk}
                for r_path, r_size, r_mtime, qh, sha in cur.fetchall():
                    norm_r = _norm_path(r_path)
                    if norm_r in item_map:
                        exp_size, exp_mtime = item_map[norm_r]
                        if r_size == exp_size and r_mtime == exp_mtime:
                            results[norm_r] = (qh, sha)
                            orig_abs = orig_path_map.get(norm_r)
                            if orig_abs:
                                results[orig_abs] = (qh, sha)
        except Exception:
            pass
        return results

    def set_quick_hash(self, path: str, size: int, mtime: float, quick_hash: str):
        """Updates or sets the quick hash for a file."""
        self.batch_set([(path, size, mtime, quick_hash, None)])

    def set_sha256(self, path: str, size: int, mtime: float, sha256: str, quick_hash: Optional[str] = None):
        """Updates or sets the SHA-256 (and optional quick hash) for a file."""
        self.batch_set([(path, size, mtime, quick_hash, sha256)])

    def batch_set(self, records: List[Tuple[str, int, float, Optional[str], Optional[str]]]):
        """Batch inserts or updates multiple cache records."""
        if not records:
            return
        try:
            conn = self._get_connection()
            now = time.time()
            with conn:
                conn.executemany("""
                    INSERT INTO file_cache (path, size, mtime, quick_hash, sha256, cached_at)
                    VALUES (?, ?, ?, ?, ?, ?)
                    ON CONFLICT(path) DO UPDATE SET
                        size=excluded.size,
                        mtime=excluded.mtime,
                        quick_hash=CASE
                            WHEN file_cache.size = excluded.size AND file_cache.mtime = excluded.mtime
                            THEN COALESCE(excluded.quick_hash, file_cache.quick_hash)
                            ELSE excluded.quick_hash END,
                        sha256=CASE
                            WHEN file_cache.size = excluded.size AND file_cache.mtime = excluded.mtime
                                AND (excluded.quick_hash IS NULL OR file_cache.quick_hash IS NULL
                                     OR excluded.quick_hash = file_cache.quick_hash)
                            THEN COALESCE(excluded.sha256, file_cache.sha256)
                            ELSE excluded.sha256 END,
                        cached_at=excluded.cached_at;
                """, [(_norm_path(p), s, m, qh, sha, now) for p, s, m, qh, sha in records])
        except Exception:
            pass

    def clear_cache(self):
        """Empties the cache table."""
        try:
            conn = self._get_connection()
            with conn:
                conn.execute("DELETE FROM file_cache;")
        except Exception:
            pass

    def close(self):
        """Closes the thread-local database connection."""
        if hasattr(self._local, "conn") and self._local.conn is not None:
            try:
                self._local.conn.close()
            except Exception:
                pass
            self._local.conn = None


# Global singleton instance
_cache_instance: Optional[HashCacheManager] = None

def get_hash_cache() -> HashCacheManager:
    global _cache_instance
    if _cache_instance is None:
        _cache_instance = HashCacheManager()
    return _cache_instance
