"""GUI-independent fresh verification and per-file action outcomes."""
from dataclasses import dataclass, field
import os

from safe_trash import SafeTrashManager


@dataclass(frozen=True)
class ActionFile:
    path: str
    size: int
    mtime: float
    keeper: str = ""
    exact: bool = False


@dataclass
class ActionOutcome:
    successful_paths: list = field(default_factory=list)
    errors: list = field(default_factory=list)


def _signature(path):
    stat = os.stat(path, follow_symlinks=False)
    return stat.st_dev, stat.st_ino, stat.st_size, stat.st_mtime_ns


def _verify(item):
    stat = os.stat(item.path, follow_symlinks=False)
    if not os.path.isfile(item.path) or os.path.islink(item.path) or stat.st_size != item.size or stat.st_mtime != item.mtime:
        raise ValueError("File changed since scanning")
    before = _signature(item.path)
    if item.exact:
        if not item.keeper or not os.path.isfile(item.keeper) or os.path.samefile(item.path, item.keeper):
            raise ValueError("A distinct, existing keeper is required")
        keeper_before = _signature(item.keeper)
        if SafeTrashManager._sha256_file(item.path) != SafeTrashManager._sha256_file(item.keeper):
            raise ValueError("Fresh SHA-256 verification differs from keeper")
        if _signature(item.keeper) != keeper_before:
            raise ValueError("Keeper changed during verification")
    if _signature(item.path) != before:
        raise ValueError("File changed during verification")


def perform_verified_action(kind, files, roots, target=None, release_futures=()):
    outcome = ActionOutcome()
    for future in release_futures:
        try:
            future.result(timeout=20)
        except TimeoutError:
            outcome.errors.append("Preview did not release its file handles; no action was performed")
            return outcome
        except Exception:
            pass  # Cancelled/failed native work has already released its worker handles.
    for item in files:
        try:
            _verify(item)
            kwargs = {"successful_paths": outcome.successful_paths}
            if kind == "quarantine":
                _, errors, _ = SafeTrashManager.safe_delete_files([item.path], roots, **kwargs)
            elif kind == "recycle":
                _, errors = SafeTrashManager.send_to_recycle_bin([item.path], **kwargs)
            elif kind == "archive":
                _, errors = SafeTrashManager.move_to_custom_folder([item.path], target, **kwargs)
            elif kind == "hardlink":
                if not item.exact:
                    raise ValueError("Hardlinks require exact content verification")
                _, errors = SafeTrashManager.replace_with_hardlinks([(item.keeper, item.path)], roots, **kwargs)
            else:
                raise ValueError(f"Unknown action: {kind}")
            outcome.errors.extend(errors)
        except Exception as error:
            outcome.errors.append(f"{item.path}: {error}")
    return outcome
