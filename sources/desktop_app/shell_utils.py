"""Helpers for opening filesystem locations in the Windows shell."""
from __future__ import annotations

import os
import subprocess


def open_in_file_explorer(path: str) -> None:
    """Open *path* in File Explorer, selecting it when it is a file.

    Explorer expects its ``/select,`` switch outside the quotes and the path
    itself inside quotes. Passing the combined switch and path as one quoted
    argument can open the folder without selecting the file.
    """
    if not path:
        raise ValueError("No file path was provided.")

    normalized = os.path.normpath(os.path.abspath(path))
    # Shell applications expect ordinary DOS/UNC paths, not Win32 extended
    # length prefixes (\\?\) used by filesystem APIs.
    if normalized.startswith("\\\\?\\UNC\\"):
        normalized = "\\\\" + normalized[8:]
    elif normalized.startswith("\\\\?\\"):
        normalized = normalized[4:]

    if not os.path.isdir(path):
        # Keep /select, outside the quoted path. Windows paths cannot contain
        # a double quote, so quoting the path this way is unambiguous.
        subprocess.Popen(f'explorer.exe /select,"{normalized}"')
    else:
        folder = normalized if os.path.isdir(path) else os.path.dirname(normalized)
        if not folder:
            folder = normalized
        subprocess.Popen(["explorer.exe", folder])
