"""Spawn-based PDF services with a private pipe for every native worker.

All public operations return concurrent.futures.Future objects. PyMuPDF is
imported and called only on a child process's main thread. Use the usual
``if __name__ == '__main__'`` guard (and freeze_support when packaged).
Call close(), or use the pool as a context manager, to release child processes.
Page numbers are zero-based. PNG bytes never contain Qt/native document handles.
"""
from __future__ import annotations

import atexit
from concurrent.futures import Future
from dataclasses import dataclass
import math
import multiprocessing as mp
import os
from pathlib import Path
import queue
import threading
import time
import uuid
import weakref

_live_pools = weakref.WeakSet()
_registry_lock = threading.Lock()
_exit_registered = False


class PdfWorkerError(RuntimeError):
    def __init__(self, message, *, path="", reason="worker_error", is_damaged=False):
        super().__init__(message)
        self.path, self.reason, self.is_damaged = path, reason, is_damaged


class PdfTaskTimeout(PdfWorkerError):
    """The active task timed out; its worker was replaced."""


@dataclass(frozen=True)
class PdfMetadata:
    path: str
    page_count: int = 0
    title: str = ""
    author: str = ""
    is_encrypted: bool = False
    is_damaged: bool = False
    error: str = ""
    error_kind: str = ""
    format: str = ""
    creation_date: str = ""
    page_width: float = 0.0
    page_height: float = 0.0


def _run_native(kind, path, args):
    if mp.current_process().name == "MainProcess" or threading.current_thread() is not threading.main_thread():
        raise PdfWorkerError("Native PDF operations require a child process main thread")
    if not Path(path).is_file():
        raise PdfWorkerError(f"PDF file not found: {path}", path=path, reason="not_found")
    import fitz

    with fitz.open(path) as doc:
        encrypted = bool(doc.is_encrypted or doc.needs_pass)
        if kind == "metadata":
            metadata = doc.metadata or {}
            repaired = bool(doc.is_repaired)
            width = height = 0.0
            if not encrypted and doc.page_count:
                rect = doc.load_page(0).rect
                width, height = rect.width, rect.height
            return PdfMetadata(path, doc.page_count, (metadata.get("title") or "")[:4096],
                               (metadata.get("author") or "")[:4096], encrypted, repaired,
                               "PDF structure required repair" if repaired else "", "repaired" if repaired else "",
                               metadata.get("format") or "Document", metadata.get("creationDate") or "", width, height)
        if encrypted:
            raise PdfWorkerError("PDF requires a password", path=path, reason="password_required")
        if kind == "render":
            page_num, zoom, *layout = args
            if page_num >= doc.page_count:
                raise PdfWorkerError(f"Page {page_num} is outside document", path=path, reason="invalid_page")
            page = doc.load_page(page_num)
            if layout:
                viewport, fit_mode = layout
                base = viewport[0] / max(1, page.rect.width)
                if fit_mode == "fit_page":
                    base = min(base, viewport[1] / max(1, page.rect.height))
                zoom = max(0.2, min(5.0, base * zoom))
            width, height = math.ceil(page.rect.width * zoom), math.ceil(page.rect.height * zoom)
            if width < 1 or height < 1 or width * height > 16_000_000:
                raise PdfWorkerError("Render exceeds 16 megapixel limit", path=path, reason="resource_limit")
            pix = page.get_pixmap(matrix=fitz.Matrix(zoom, zoom), alpha=False)
            png = pix.tobytes("png")
            if len(png) > 32 * 1024 * 1024:
                raise PdfWorkerError("Rendered PNG exceeds 32 MiB limit", path=path, reason="resource_limit")
            return png
        if kind == "fingerprint":
            import hashlib
            if doc.page_count < 1:
                raise PdfWorkerError("Document has no pages", path=path, reason="document_error")
            hashes = []
            for index in (doc.page_count // 2, doc.page_count - 1):
                page = doc.load_page(index)
                text = " ".join(page.get_text().split())
                if len(text) >= 40:
                    hashes.append("text:" + hashlib.sha256(text.encode("utf-8")).hexdigest())
                else:
                    pix = page.get_pixmap(colorspace=fitz.csGRAY, matrix=fitz.Matrix(
                        96 / max(page.rect.width, 1), 128 / max(page.rect.height, 1)), alpha=False)
                    hashes.append("pixels:" + hashlib.sha256(pix.samples).hexdigest())
            return {"page_count": doc.page_count, "interior": tuple(hashes)}
        if kind == "cover":
            if doc.page_count == 0:
                raise PdfWorkerError("PDF has no pages", path=path, reason="document_error", is_damaged=True)
            from PIL import Image

            page = doc.load_page(0)
            pix = page.get_pixmap(colorspace=fitz.csGRAY,
                                  matrix=fitz.Matrix(9 / max(page.rect.width, 1), 8 / max(page.rect.height, 1)))
            image = Image.frombytes("L", (pix.width, pix.height), pix.samples).resize((9, 8), Image.Resampling.BILINEAR)
            samples = list(image.getdata())
            if max(samples) - min(samples) <= 2:
                return None
            value = 0
            for y in range(8):
                for x in range(8):
                    value = (value << 1) | (samples[y * 9 + x] > samples[y * 9 + x + 1])
            return f"{value:016x}" if value not in (0, 2**64 - 1) else None
        raise PdfWorkerError(f"Unknown PDF operation: {kind}")


def _worker_main(connection):
    # A dying worker can corrupt only its own pipe, never another worker's IPC.
    try:
        connection.send(("ready", os.getpid()))
        while True:
            request = connection.recv()
            if request is None:
                return
            job_id, kind, path, args = request
            try:
                value = _run_native(kind, path, args)
                response = (job_id, True, value)
            except PdfWorkerError as error:
                response = (job_id, False, (str(error), error.reason, error.is_damaged))
            except ImportError as error:
                response = (job_id, False, (str(error), "dependency_error", False))
            except PermissionError as error:
                response = (job_id, False, (str(error), "permission_denied", False))
            except Exception as error:
                response = (job_id, False, (str(error), "document_error", True))
            connection.send(response)
    except (EOFError, BrokenPipeError, OSError):
        return
    finally:
        connection.close()


@dataclass
class _Job:
    id: str
    kind: str
    path: str
    args: tuple
    timeout: float
    future: Future


@dataclass
class _Slot:
    generation: str
    process: object
    connection: object
    reader: threading.Thread
    pid: int = 0
    ready: bool = False
    started_at: float = 0.0
    job: _Job | None = None
    deadline: float = 0.0


def _read_pipe(connection, index, generation, responses, wake):
    try:
        while True:
            response = connection.recv()
            responses.put((index, generation, response))
            wake.set()
    except (EOFError, OSError, ValueError):
        responses.put((index, generation, None))
        wake.set()


class PdfWorkerPool:
    def __init__(self, workers=None, *, task_timeout=15.0, max_pending=64, startup_timeout=10.0):
        workers = workers if workers is not None else min(4, max(2, (os.cpu_count() or 2) // 2))
        if type(workers) is not int or not 2 <= workers <= 4:
            raise ValueError("PDF pool requires 2 to 4 workers")
        if not math.isfinite(task_timeout) or task_timeout <= 0 or type(max_pending) is not int or max_pending < 1:
            raise ValueError("Task timeout and queue limit must be positive")
        if not math.isfinite(startup_timeout) or startup_timeout <= 0:
            raise ValueError("Worker startup timeout must be positive")
        self.workers, self.task_timeout = workers, task_timeout
        self.startup_timeout = startup_timeout
        self._ctx = mp.get_context("spawn")
        self._pending = queue.Queue(maxsize=max_pending)
        self._responses = queue.Queue()
        self._cancellations = queue.Queue()
        self._wake, self._closing = threading.Event(), threading.Event()
        self._lock = threading.Lock()
        self._slots = {}
        self._idle_restarts = {}
        self._damaged = set()
        self._failure = None
        self._thread = threading.Thread(target=self._coordinate, name="PDFCoordinator", daemon=True)
        with _registry_lock:
            _live_pools.add(self)
        self._thread.start()

    @property
    def worker_pids(self):
        with self._lock:
            return tuple(slot.pid for slot in self._slots.values())

    @property
    def active_jobs(self):
        """Diagnostic snapshot: worker PID -> current input path."""
        with self._lock:
            return {slot.pid: slot.job.path for slot in self._slots.values() if slot.job}

    @property
    def damaged_files(self):
        with self._lock:
            return frozenset(self._damaged)

    def _submit(self, kind, path, args=(), *, timeout=None):
        timeout = self.task_timeout if timeout is None else timeout
        if not math.isfinite(timeout) or timeout <= 0:
            raise ValueError("Task timeout must be finite and positive")
        job = _Job(uuid.uuid4().hex, kind, str(Path(path).resolve()), args, timeout, Future())
        with self._lock:
            if self._closing.is_set() or self._failure:
                raise PdfWorkerError(self._failure or "PDF pool is closed", reason="closed")
            try:
                self._pending.put_nowait(job)
            except queue.Full as error:
                raise PdfWorkerError("PDF pending-task limit reached", reason="queue_full") from error
        self._wake.set()
        return job.future

    def extract_pdf_metadata(self, path, *, timeout=None):
        """Return Future[PdfMetadata]; timeout starts on dispatch, not queue entry."""
        return self._submit("metadata", path, timeout=timeout)

    def extract_document_fingerprint(self, path, *, timeout=None):
        return self._submit("fingerprint", path, timeout=timeout)

    def render_pdf_page(self, path, page_num=0, zoom=1.0, *, timeout=None, viewport=None, fit_mode="fit_width"):
        """Return Future[bytes] containing PNG data for a zero-based page."""
        if type(page_num) is not int or page_num < 0 or not math.isfinite(zoom) or not 0 < zoom <= 8:
            raise ValueError("Page must be nonnegative and zoom finite in (0, 8]")
        args = (page_num, zoom)
        if viewport is not None:
            if len(viewport) != 2 or any(type(v) is not int or v <= 0 for v in viewport) or fit_mode not in ("fit_width", "fit_page"):
                raise ValueError("Invalid preview viewport")
            args += (tuple(viewport), fit_mode)
        return self._submit("render", path, args, timeout=timeout)

    def cancel(self, future):
        """Cancel pending work or stop/replace the process executing this Future."""
        if future.done():
            return False
        future.cancel()
        self._cancellations.put(future)
        self._wake.set()
        return True

    def extract_cover_hash(self, path, *, timeout=None):
        """Return Future[str | None]; a blank/featureless cover has no hash."""
        return self._submit("cover", path, timeout=timeout)

    def close(self, wait=True):
        with self._lock:
            self._closing.set()
        self._wake.set()
        if wait and threading.current_thread() is not self._thread:
            self._thread.join(timeout=10)
            if self._thread.is_alive():
                raise PdfWorkerError("PDF coordinator did not finish shutdown", reason="shutdown")

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _spawn(self, index):
        global _exit_registered
        parent, child = self._ctx.Pipe(duplex=True)
        generation = uuid.uuid4().hex
        process = self._ctx.Process(target=_worker_main, args=(child,), name=f"HybridPDF-{index}", daemon=True)
        try:
            process.start()
        except BaseException:
            parent.close()
            child.close()
            raise
        child.close()
        with _registry_lock:
            if not _exit_registered:
                # Register after multiprocessing's own exit handler so pools close first.
                atexit.register(_shutdown_all_pools)
                _exit_registered = True
        reader = threading.Thread(target=_read_pipe, args=(parent, index, generation, self._responses, self._wake), daemon=True)
        slot = _Slot(generation, process, parent, reader, pid=process.pid, started_at=time.monotonic())
        with self._lock:
            self._slots[index] = slot
        reader.start()

    @staticmethod
    def _dispose(slot):
        process = slot.process
        if process.is_alive():
            try:
                slot.connection.send(None)
            except (OSError, ValueError):
                pass
            process.join(timeout=0.2)
        if process.is_alive():
            process.terminate()
            process.join(timeout=0.5)
        if process.is_alive():
            process.kill()
            process.join(timeout=0.5)
        slot.connection.close()
        slot.reader.join(timeout=0.5)
        if not process.is_alive():
            process.close()

    def _finish(self, job, value=None, error=None):
        if job.future.done():
            return
        if error and job.kind == "metadata" and error.is_damaged:
            value = PdfMetadata(job.path, is_damaged=True, error=str(error), error_kind=error.reason)
            error = None
        if (isinstance(value, PdfMetadata) and value.is_damaged) or (error and error.is_damaged):
            with self._lock:
                self._damaged.add(job.path)
        if error:
            job.future.set_exception(error)
        else:
            job.future.set_result(value)

    def _replace(self, index, error):
        with self._lock:
            slot = self._slots.pop(index)
            job, slot.job = slot.job, None
        self._dispose(slot)
        try:
            self._idle_restarts[index] = 0 if job else self._idle_restarts.get(index, 0) + 1
            if self._idle_restarts[index] > 3:
                raise PdfWorkerError("PDF worker repeatedly failed to start", reason="startup")
            self._spawn(index)
        finally:
            if job:
                error.path = job.path
                self._finish(job, error=error)

    def _receive(self, index, generation, response):
        slot = self._slots[index]
        if generation != slot.generation:
            return
        if response is None:
            self._replace(index, PdfWorkerError("PDF worker crashed or closed its pipe", reason="worker_crashed", is_damaged=True))
            return
        if response == ("ready", slot.process.pid):
            slot.ready = True
            return
        job = slot.job
        if job is None or not isinstance(response, tuple) or len(response) != 3 or response[0] != job.id:
            self._replace(index, PdfWorkerError("Invalid PDF worker response", reason="protocol"))
            return
        with self._lock:
            slot.job = None
        self._idle_restarts[index] = 0
        _, ok, value = response
        if ok:
            self._finish(job, value=value)
        else:
            message, reason, damaged = value
            self._finish(job, error=PdfWorkerError(message, path=job.path, reason=reason, is_damaged=damaged))

    def _coordinate(self):
        try:
            for index in range(self.workers):
                self._spawn(index)
            while not self._closing.is_set():
                while not self._cancellations.empty():
                    future = self._cancellations.get_nowait()
                    if future.cancelled():
                        with self._lock:
                            kept = []
                            while not self._pending.empty():
                                job = self._pending.get_nowait()
                                if job.future is not future:
                                    kept.append(job)
                            for job in kept:
                                self._pending.put_nowait(job)
                        continue
                    for index, slot in list(self._slots.items()):
                        if slot.job and slot.job.future is future:
                            self._replace(index, PdfWorkerError("PDF task cancelled", reason="cancelled"))
                            break
                while not self._responses.empty():
                    self._receive(*self._responses.get_nowait())
                for index in range(self.workers):
                    slot = self._slots[index]
                    if not slot.ready and time.monotonic() - slot.started_at >= self.startup_timeout:
                        self._replace(index, PdfWorkerError("PDF worker startup timed out", reason="startup"))
                        slot = self._slots[index]
                    if slot.process.exitcode is not None:
                        self._replace(index, PdfWorkerError("Native PDF process exited", reason="worker_crashed", is_damaged=True))
                        slot = self._slots[index]
                    if slot.job and time.monotonic() >= slot.deadline:
                        self._replace(index, PdfTaskTimeout(f"PDF task exceeded {slot.job.timeout}s", path=slot.job.path, reason="timeout"))
                        slot = self._slots[index]
                    if slot.ready and slot.job is None:
                        try:
                            job = self._pending.get_nowait()
                        except queue.Empty:
                            continue
                        if not job.future.set_running_or_notify_cancel():
                            continue
                        with self._lock:
                            slot.job, slot.deadline = job, time.monotonic() + job.timeout
                        slot.connection.send((job.id, job.kind, job.path, job.args))
                self._wake.wait(0.02)
                self._wake.clear()
        except BaseException as error:
            with self._lock:
                self._failure = f"PDF coordinator failed: {error}"
        finally:
            self._closing.set()
            for slot in list(self._slots.values()):
                job = slot.job
                self._dispose(slot)
                if job:
                    self._finish(job, error=PdfWorkerError(self._failure or "PDF pool closed", reason="closed"))
            with self._lock:
                self._slots.clear()
            while not self._pending.empty():
                self._finish(self._pending.get_nowait(), error=PdfWorkerError(self._failure or "PDF pool closed", reason="closed"))


_default_pool = None
_default_lock = threading.Lock()


def _pool():
    global _default_pool
    with _default_lock:
        if _default_pool is None:
            _default_pool = PdfWorkerPool()
        return _default_pool


def extract_pdf_metadata(path, *, timeout=None):
    return _pool().extract_pdf_metadata(path, timeout=timeout)


def render_pdf_page(path, page_num=0, zoom=1.0, *, timeout=None):
    return _pool().render_pdf_page(path, page_num, zoom, timeout=timeout)


def extract_cover_hash(path, *, timeout=None):
    return _pool().extract_cover_hash(path, timeout=timeout)


def extract_document_fingerprint(path, *, timeout=None):
    return _pool().extract_document_fingerprint(path, timeout=timeout)


def shutdown_pdf_workers():
    global _default_pool
    with _default_lock:
        pool, _default_pool = _default_pool, None
    if pool:
        pool.close()


def _shutdown_all_pools():
    with _registry_lock:
        pools = list(_live_pools)
    for pool in pools:
        pool.close()
