"""Asynchronous, scan-only bridge to the pinned Czkawka 12.0.2 CLI.

``scan`` returns a Future; progress callbacks run on the adapter thread, never
the GUI thread. Marshal callbacks through Qt signals when wiring a GUI later.
Console phases are best-effort observations, not numeric progress guarantees.
"""
from __future__ import annotations

import codecs
from concurrent.futures import Future, ThreadPoolExecutor
from dataclasses import dataclass
import hashlib
import json
import math
import os
from pathlib import Path
import queue
import re
import signal
import subprocess
import tempfile
import threading
import time
from typing import Iterable

from scanner import DuplicateGroup, FileInfo


CLI_SHA256 = "eb7c2009d2dd49cf5202acbd65370caece0a3f819b9515e727cedb1ec088ed1a"


class CzkawkaExecutionError(RuntimeError):
    """The executable, output contract, callback, or scan failed."""


class CzkawkaScanCancelled(CzkawkaExecutionError):
    """Cancellation completed and the CLI process has been reaped."""


@dataclass(frozen=True)
class ScanProgress:
    phase: str
    message: str
    source: str = "adapter"


def _default_cli() -> Path:
    here = Path(__file__).resolve().parent
    relative = Path("tools/czkawka-12.0.2/windows_czkawka_cli.exe")
    for base in (here, here.parent.parent):
        candidate = base / relative
        if candidate.is_file():
            return candidate
    return here / relative


def _inside(path: Path, roots: tuple[Path, ...]) -> bool:
    return any(path == root or root in path.parents for root in roots)


class CzkawkaScanAdapter:
    def __init__(self, cli_path=None, *, data_dir=None, timeout=3600.0, cancel_grace=0.75):
        if not math.isfinite(timeout) or timeout <= 0 or not math.isfinite(cancel_grace) or cancel_grace < 0:
            raise ValueError("Timeout must be positive and cancellation grace nonnegative")
        self.cli_path = Path(cli_path) if cli_path is not None else _default_cli()
        self.data_dir = Path(data_dir) if data_dir else Path(
            os.environ.get("LOCALAPPDATA", str(Path.home()))
        ) / "HybridDuplicateChecker" / "czkawka-12.0.2"
        self.timeout, self.cancel_grace = timeout, cancel_grace
        self._executor = ThreadPoolExecutor(max_workers=1, thread_name_prefix="CzkawkaAdapter")
        self._lock = threading.Lock()
        self._cancel = threading.Event()
        self._process = None
        self._running = False
        self._closed = False

    def scan(self, directories: Iterable[str] | str, *, excluded_directories=(), on_progress=None,
             allowed_extensions=None, minimal_file_size=1, thread_number=2, excluded_items=()) -> Future:
        """Return Future[list[DuplicateGroup]]; only one scan may run per adapter."""
        if isinstance(directories, (str, os.PathLike)):
            directories = [directories]
        roots = tuple(dict.fromkeys(Path(p).resolve() for p in directories))
        if isinstance(excluded_directories, (str, os.PathLike)):
            excluded_directories = [excluded_directories]
        excluded = tuple(Path(p).resolve() for p in excluded_directories)
        if not roots or any(not p.is_dir() for p in roots):
            raise CzkawkaExecutionError("At least one existing scan directory is required")
        if type(minimal_file_size) is not int or minimal_file_size < 1 or type(thread_number) is not int or not 1 <= thread_number <= 32:
            raise CzkawkaExecutionError("Invalid minimum size or thread count")
        extensions = None if allowed_extensions is None else tuple(sorted({str(e).lower().lstrip('.') for e in allowed_extensions}))
        if extensions is not None and any(not re.fullmatch(r"[a-z0-9_+-]+", e) for e in extensions):
            raise CzkawkaExecutionError("Invalid extension filter")
        options = (extensions, minimal_file_size, thread_number, tuple(excluded_items))
        with self._lock:
            if self._closed or self._running:
                raise CzkawkaExecutionError("Adapter is closed or another scan is still running")
            self._cancel.clear()
            self._running = True
            try:
                future = self._executor.submit(self._scan, roots, excluded, on_progress, options)
                future.add_done_callback(self._release_cancelled)
                return future
            except BaseException:
                self._running = False
                raise

    def _release_cancelled(self, future):
        if future.cancelled():
            with self._lock:
                self._running = False

    def cancel(self) -> bool:
        """Request cancellation without blocking the caller."""
        with self._lock:
            if not self._running:
                return False
            self._cancel.set()
            return True

    def close(self, wait=True):
        with self._lock:
            self._closed = True
            self._cancel.set()
        # A callback may close its own adapter without joining its own thread.
        if threading.current_thread().name.startswith("CzkawkaAdapter"):
            wait = False
        self._executor.shutdown(wait=wait)

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    @staticmethod
    def _emit(callback, phase, message, source="adapter"):
        if callback:
            try:
                callback(ScanProgress(phase, message, source))
            except Exception as error:
                raise CzkawkaExecutionError(f"Progress callback failed: {error}") from error

    def _verify_cli(self):
        try:
            with self.cli_path.open("rb") as executable:
                digest = hashlib.file_digest(executable, "sha256").hexdigest()
        except OSError as error:
            raise CzkawkaExecutionError(f"Cannot open Czkawka executable {self.cli_path}: {error}") from error
        if digest != CLI_SHA256:
            raise CzkawkaExecutionError(f"Czkawka 12.0.2 SHA-256 mismatch: {self.cli_path}")

    @staticmethod
    def _read_output(pipe, source, messages, stop):
        decoder = codecs.getincrementaldecoder("utf-8")("replace")
        try:
            while not stop.is_set():
                chunk = pipe.read1(4096)
                if not chunk:
                    break
                decoded = decoder.decode(chunk)
                while not stop.is_set():
                    try:
                        messages.put((source, decoded), timeout=0.05)
                        break
                    except queue.Full:
                        continue
        except (OSError, ValueError) as error:
            if not stop.is_set():
                messages.put(("reader_error", str(error)))

    def _stop_process(self, process):
        if process.poll() is not None:
            return
        try:
            if os.name == "nt":
                process.send_signal(signal.CTRL_BREAK_EVENT)
            else:
                process.send_signal(signal.SIGINT)
        except (OSError, ValueError):
            # Hidden Windows GUI launches may not have an attached console.
            pass
        try:
            process.wait(timeout=self.cancel_grace)
        except subprocess.TimeoutExpired:
            process.terminate()
            try:
                process.wait(timeout=2)
            except subprocess.TimeoutExpired:
                process.kill()
                process.wait(timeout=2)

    def _scan(self, roots, excluded, callback, options):
        try:
            extensions, minimum, threads, patterns = options
            if extensions == ():
                return []
            self._verify_cli()
            self.data_dir.mkdir(parents=True, exist_ok=True)
            for name in ("cache", "config", "jobs"):
                (self.data_dir / name).mkdir(exist_ok=True)
            excluded = tuple(dict.fromkeys((*excluded, *(self.data_dir.joinpath(name).resolve()
                                                        for name in ("cache", "config", "jobs")))))
            env = dict(os.environ, CZKAWKA_CACHE_PATH=str((self.data_dir / "cache").resolve()),
                       CZKAWKA_CONFIG_PATH=str((self.data_dir / "config").resolve()), LANG="en_US.UTF-8", LC_ALL="C")
            with tempfile.TemporaryDirectory(prefix="scan_", dir=self.data_dir / "jobs") as job:
                output = Path(job) / "results.json"
                command = [str(self.cli_path.resolve()), "dup", "--delete-method", "NONE",
                           "--minimal-file-size", str(minimum), "--ignore-error-code-on-found",
                           "--search-method", "HASH", "--hash-type", "BLAKE3", "--disable-cache",
                           "--thread-number", str(threads), "--do-not-print-results", "-C", str(output)]
                if extensions is not None:
                    command.extend(("--allowed-extensions", ",".join(extensions)))
                for pattern in patterns:
                    command.extend(("--excluded-items", pattern))
                for root in roots:
                    command.extend(("--directories", str(root)))
                for root in excluded:
                    command.extend(("--excluded-directories", str(root)))
                self._run(command, env, Path(job), callback)
                if self._cancel.is_set():
                    raise CzkawkaScanCancelled("Scan cancelled; no results were accepted")
                results = self._load_results(output, roots, excluded)
                self._emit(callback, "completed", f"Found {len(results)} duplicate groups")
                return results
        except CzkawkaExecutionError:
            raise
        except Exception as error:
            raise CzkawkaExecutionError(f"Czkawka scan failed: {error}") from error
        finally:
            with self._lock:
                self._process = None
                self._running = False

    def _run(self, command, env, job, callback):
        if self._cancel.is_set():
            raise CzkawkaScanCancelled("Scan cancelled before launch")
        options = {}
        if os.name == "nt":
            options["creationflags"] = subprocess.CREATE_NEW_PROCESS_GROUP | subprocess.CREATE_NO_WINDOW
        process = subprocess.Popen(command, stdout=subprocess.PIPE, stderr=subprocess.PIPE,
                                   stdin=subprocess.DEVNULL, env=env, cwd=job, **options)
        with self._lock:
            self._process = process
        messages, stop = queue.Queue(maxsize=256), threading.Event()
        readers = [threading.Thread(target=self._read_output, args=(pipe, source, messages, stop), daemon=True)
                   for source, pipe in (("stdout", process.stdout), ("stderr", process.stderr))]
        for reader in readers:
            reader.start()
        tails = {"stdout": "", "stderr": ""}
        last_phase = None
        started = time.monotonic()
        try:
            self._emit(callback, "running", f"Czkawka process started (PID {process.pid})")
            while True:
                if self._cancel.is_set():
                    self._stop_process(process)
                    self._emit(callback, "cancelled", "Czkawka process stopped")
                    raise CzkawkaScanCancelled("Scan cancelled; temporary results discarded")
                if time.monotonic() - started > self.timeout:
                    raise CzkawkaExecutionError(f"Scan exceeded timeout ({self.timeout}s)")
                try:
                    source, text = messages.get(timeout=0.025)
                    if source == "reader_error":
                        raise CzkawkaExecutionError(f"Cannot read CLI output: {text}")
                    tails[source] = (tails[source] + text)[-16384:]
                    phase = self.detect_phase(tails[source][-4096:])
                    if phase and phase != last_phase:
                        self._emit(callback, phase, text[-1000:].strip(), source)
                        last_phase = phase
                except queue.Empty:
                    pass
                if process.poll() is not None and not any(r.is_alive() for r in readers) and messages.empty():
                    break
            if process.returncode not in (0, 11):
                raise CzkawkaExecutionError(f"CLI exit code {process.returncode}: {tails['stderr']} {tails['stdout']}")
        finally:
            self._stop_process(process)
            stop.set()
            for reader in readers:
                reader.join(timeout=1)
            process.stdout.close()
            process.stderr.close()

    @staticmethod
    def detect_phase(text):
        text = text.lower()
        matches = [(match.end(), phase) for phase, pattern in (
            ("prehash", r"pre[- ]?hash"),
            ("full_hash", r"full[- ]?hash|calculating[^\r\n]*hash|(?<!pre )(?<!pre-)(?<!pre)\bhashing\b"),
            ("traversal", r"travers|scanning[^\r\n]*director|collecting[^\r\n]*file"),
        ) for match in re.finditer(pattern, text)]
        return max(matches)[1] if matches else None

    @staticmethod
    def _load_results(output, roots, excluded):
        try:
            if output.stat().st_size > 128 * 1024 * 1024:
                raise ValueError("Result JSON exceeds the 128 MiB limit")
            data = json.loads(output.read_text(encoding="utf-8"))
            if not isinstance(data, dict):
                raise ValueError("Expected a size-to-groups JSON object")
            results, seen_paths, seen_ids = [], set(), set()
            for size_text, groups in data.items():
                if not size_text.isdecimal() or int(size_text) < 1 or not isinstance(groups, list):
                    raise ValueError("Invalid size bucket")
                size = int(size_text)
                for entries in groups:
                    if not isinstance(entries, list) or len(entries) < 2:
                        raise ValueError("An exact group requires at least two files")
                    files, group_hash = [], None
                    for entry in entries:
                        if not isinstance(entry, dict) or type(entry.get("size")) is not int or entry["size"] != size:
                            raise ValueError("Inconsistent file size in result")
                        path_text, digest, mtime = entry.get("path"), entry.get("hash"), entry.get("modified_date")
                        if not isinstance(path_text, str) or not Path(path_text).is_absolute():
                            raise ValueError("Result path must be absolute")
                        path = Path(path_text).resolve()
                        if not _inside(path, roots) or _inside(path, excluded):
                            raise ValueError(f"Result is outside scan scope: {path}")
                        if not isinstance(digest, str) or not re.fullmatch(r"[0-9a-f]{64}", digest):
                            raise ValueError("Invalid BLAKE3 digest")
                        if group_hash is not None and digest != group_hash:
                            raise ValueError("Different hashes in exact group")
                        if type(mtime) not in (int, float) or not math.isfinite(mtime) or mtime < 0:
                            raise ValueError("Invalid modification time")
                        stat = path.stat()
                        identity = (stat.st_dev, stat.st_ino) if stat.st_ino else str(path)
                        if path in seen_paths or identity in seen_ids:
                            raise ValueError("Repeated path or physical file in results")
                        if not path.is_file() or stat.st_size != size or int(stat.st_mtime) != mtime:
                            raise ValueError(f"File changed or disappeared during scan: {path}")
                        seen_paths.add(path)
                        seen_ids.add(identity)
                        group_hash = digest
                        files.append(FileInfo(str(path), path.name, size, stat.st_mtime, str(path.parent), hash_val=digest))
                    results.append(DuplicateGroup(size, files, "Exact Hash (BLAKE3)"))
            return results
        except (OSError, ValueError, TypeError, OverflowError) as error:
            raise CzkawkaExecutionError(f"Missing, invalid, or stale JSON results at {output}: {error}") from error
