"""
Shared utility helpers used across DevCapsule modules.
"""

from __future__ import annotations

import json
import platform
import shutil
import subprocess
from pathlib import Path
from typing import Optional


def run_cmd(cmd: list[str], timeout: int = 15) -> Optional[str]:
    """
    Run a command and return stripped stdout, or None if the command
    doesn't exist / fails / times out. Never raises.
    """
    exe = cmd[0]
    if shutil.which(exe) is None:
        return None
    try:
        result = subprocess.run(
            cmd,
            capture_output=True,
            text=True,
            timeout=timeout,
            check=False,
        )
        output = (result.stdout or "").strip()
        if not output:
            output = (result.stderr or "").strip()
        return output if result.returncode == 0 or output else None
    except (subprocess.TimeoutExpired, OSError):
        return None


def which(exe: str) -> Optional[str]:
    """Return the resolved path of an executable, or None."""
    return shutil.which(exe)


def home() -> Path:
    return Path.home()


def os_name() -> str:
    system = platform.system()
    return {"Darwin": "macos", "Windows": "windows", "Linux": "linux"}.get(system, system.lower())


def vscode_user_dir() -> Optional[Path]:
    """
    Return the VS Code 'User' settings directory for the current OS,
    or None if it doesn't exist on disk.
    """
    system = os_name()
    h = home()
    candidates = []
    if system == "macos":
        candidates.append(h / "Library" / "Application Support" / "Code" / "User")
    elif system == "windows":
        candidates.append(h / "AppData" / "Roaming" / "Code" / "User")
    else:  # linux and others
        candidates.append(h / ".config" / "Code" / "User")

    # Also check VS Code Insiders / server variants (common on remote/dev boxes)
    if system == "macos":
        candidates.append(h / "Library" / "Application Support" / "Code - Insiders" / "User")
    elif system == "windows":
        candidates.append(h / "AppData" / "Roaming" / "Code - Insiders" / "User")
    else:
        candidates.append(h / ".config" / "Code - Insiders" / "User")
        candidates.append(h / ".vscode-server" / "data" / "User")

    for c in candidates:
        if c.exists():
            return c
    return None


def read_json_safe(path: Path) -> dict:
    """Read a JSON(C)-ish file, tolerating // and /* */ comments (VS Code style)."""
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
        text = _strip_jsonc_comments(text)
        return json.loads(text) if text.strip() else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _strip_jsonc_comments(text: str) -> str:
    """Very small JSONC comment stripper (// line comments and /* block */)."""
    out = []
    i, n = 0, len(text)
    in_str = False
    while i < n:
        c = text[i]
        if in_str:
            out.append(c)
            if c == "\\" and i + 1 < n:
                out.append(text[i + 1])
                i += 2
                continue
            if c == '"':
                in_str = False
            i += 1
            continue
        if c == '"':
            in_str = True
            out.append(c)
            i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "/":
            while i < n and text[i] not in "\n\r":
                i += 1
            continue
        if c == "/" and i + 1 < n and text[i + 1] == "*":
            i += 2
            while i + 1 < n and not (text[i] == "*" and text[i + 1] == "/"):
                i += 1
            i += 2
            continue
        out.append(c)
        i += 1
    return "".join(out)


def bytes_to_human(n: int) -> str:
    step = 1024.0
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if n < step:
            return f"{n:.1f}{unit}"
        n /= step
    return f"{n:.1f}PB"
