r"""
Windows Context Menu Shell Extension Integration.
Registers / unregisters right-click menu options for Windows Explorer:
- Right-click any Folder -> "Scan for Duplicates with Duplicate Checker"
- Right-click Folder Background -> "Scan this folder for Duplicates"
- Right-click any Drive (C:, D:, USB) -> "Scan Drive for Duplicates"

Uses HKEY_CURRENT_USER\Software\Classes so NO Administrator privileges are needed.
"""
from __future__ import annotations

import os
import sys
from typing import Tuple, Optional

if sys.platform == "win32":
    import winreg
else:
    winreg = None


def ensure_app_icon() -> str:
    """Creates a clean app_icon.ico in application directory if not already present."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    ico_path = os.path.join(base_dir, "app_icon.ico")
    if os.path.isfile(ico_path):
        return ico_path

    try:
        from PIL import Image, ImageDraw
        img = Image.new("RGBA", (48, 48), (0, 0, 0, 0))
        draw = ImageDraw.Draw(img)
        # Background file shape (orange accent)
        draw.rounded_rectangle([6, 10, 32, 42], radius=3, fill=(255, 237, 213), outline=(249, 115, 22), width=2)
        # Front file shape (blue accent)
        draw.rounded_rectangle([16, 4, 42, 36], radius=3, fill=(255, 255, 255), outline=(37, 99, 235), width=2)
        # Document lines
        for y in (12, 18, 24, 30):
            draw.line([(22, y), (36, y)], fill=(148, 163, 184), width=2)
        # Yellow lightning bolt
        poly = [(34, 22), (28, 32), (32, 32), (26, 42), (38, 30), (33, 30)]
        draw.polygon(poly, fill=(234, 179, 8))
        img.save(ico_path, format="ICO", sizes=[(48, 48), (32, 32), (16, 16)])
        return ico_path
    except Exception:
        return ""


def get_default_launcher_command() -> str:
    """Returns the optimal command to launch the app with passed arguments."""
    base_dir = os.path.dirname(os.path.abspath(__file__))
    main_py = os.path.join(base_dir, "main.py")
    py_exe = sys.executable

    # Use pythonw.exe if available to avoid opening a blank cmd window
    pyw_exe = os.path.join(os.path.dirname(py_exe), "pythonw.exe")
    exe_to_use = pyw_exe if os.path.isfile(pyw_exe) else py_exe

    return f'"{exe_to_use}" "{main_py}"'


def is_context_menu_installed() -> bool:
    """Returns True if the shell extension registry keys are registered in HKCU."""
    if winreg is None:
        return False
    try:
        with winreg.OpenKey(
            winreg.HKEY_CURRENT_USER,
            r"Software\Classes\Directory\shell\AllFilesDuplicateChecker\command",
            0,
            winreg.KEY_READ
        ) as key:
            val, _ = winreg.QueryValueEx(key, "")
            return bool(val)
    except OSError:
        return False


def install_context_menu() -> Tuple[bool, str]:
    """
    Registers right-click context menu under HKEY_CURRENT_USER.
    Returns (success, message).
    """
    if winreg is None:
        return False, "Context Menu registration is only supported on Windows."

    try:
        launcher_cmd = get_default_launcher_command()
        ico_path = ensure_app_icon()

        menu_configs = [
            (
                r"Software\Classes\Directory\shell\AllFilesDuplicateChecker",
                "Scan for Duplicates with Duplicate Checker",
                f'{launcher_cmd} "%1"'
            ),
            (
                r"Software\Classes\Directory\Background\shell\AllFilesDuplicateChecker",
                "Scan this folder for Duplicates",
                f'{launcher_cmd} "%V"'
            ),
            (
                r"Software\Classes\Drive\shell\AllFilesDuplicateChecker",
                "Scan Drive for Duplicates",
                f'{launcher_cmd} "%1"'
            ),
        ]

        for key_path, label, cmd in menu_configs:
            # Create or open base key
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, key_path) as key:
                winreg.SetValueEx(key, "", 0, winreg.REG_SZ, label)
                if ico_path and os.path.isfile(ico_path):
                    winreg.SetValueEx(key, "Icon", 0, winreg.REG_SZ, ico_path)

            # Create or open command subkey
            with winreg.CreateKey(winreg.HKEY_CURRENT_USER, rf"{key_path}\command") as cmd_key:
                winreg.SetValueEx(cmd_key, "", 0, winreg.REG_SZ, cmd)

        return True, "Windows Context Menu successfully installed! Right-click any folder or drive to scan."
    except Exception as e:
        return False, f"Failed to register context menu: {e}"


def uninstall_context_menu() -> Tuple[bool, str]:
    """
    Removes the registered context menu keys from HKEY_CURRENT_USER.
    Returns (success, message).
    """
    if winreg is None:
        return False, "Context Menu is only supported on Windows."

    base_keys = [
        r"Software\Classes\Directory\shell\AllFilesDuplicateChecker",
        r"Software\Classes\Directory\Background\shell\AllFilesDuplicateChecker",
        r"Software\Classes\Drive\shell\AllFilesDuplicateChecker",
    ]

    errors = []
    for b_key in base_keys:
        try:
            # Delete 'command' subkey first
            try:
                winreg.DeleteKey(winreg.HKEY_CURRENT_USER, rf"{b_key}\command")
            except OSError:
                pass
            # Delete main key
            winreg.DeleteKey(winreg.HKEY_CURRENT_USER, b_key)
        except OSError:
            pass
        except Exception as e:
            errors.append(str(e))

    if errors:
        return False, f"Errors while removing context menu: {'; '.join(errors)}"
    return True, "Windows Context Menu successfully uninstalled."


if __name__ == "__main__":
    if len(sys.argv) > 1:
        flag = sys.argv[1].lower()
        if flag in ("--install", "-i"):
            succ, msg = install_context_menu()
            print(f"[{'SUCCESS' if succ else 'ERROR'}] {msg}")
            sys.exit(0 if succ else 1)
        elif flag in ("--uninstall", "-u"):
            succ, msg = uninstall_context_menu()
            print(f"[{'SUCCESS' if succ else 'ERROR'}] {msg}")
            sys.exit(0 if succ else 1)
        elif flag in ("--status", "-s"):
            installed = is_context_menu_installed()
            print(f"Context menu installed: {installed}")
            sys.exit(0 if installed else 1)

    print("Usage: python shell_extension.py [--install | --uninstall | --status]")
