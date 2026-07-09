"""
Scanner Engine
==============
Detects the developer's current machine setup: editors, extensions,
settings, runtimes, package managers, and general dev tools.

This module is intentionally read-only: it never modifies the host
machine, it only inspects it.
"""

from __future__ import annotations

import platform
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import utils

try:
    import psutil
except ImportError:  # pragma: no cover - optional dependency
    psutil = None


# ---------------------------------------------------------------------------
# Data containers
# ---------------------------------------------------------------------------

@dataclass
class SystemInfo:
    os: str
    os_version: str
    architecture: str
    hostname: str
    cpu: str
    cpu_cores: int
    ram_gb: float
    gpu: list[str] = field(default_factory=list)

    def to_dict(self) -> dict[str, Any]:
        return {
            "os": self.os,
            "os_version": self.os_version,
            "architecture": self.architecture,
            "hostname": self.hostname,
            "cpu": self.cpu,
            "cpu_cores": self.cpu_cores,
            "ram_gb": self.ram_gb,
            "gpu": self.gpu,
        }


# ---------------------------------------------------------------------------
# Scanner
# ---------------------------------------------------------------------------

class EnvironmentScanner:
    """Scans the host machine and produces a structured snapshot dict."""

    def scan(self) -> dict[str, Any]:
        return {
            "system": self._scan_system().to_dict(),
            "editors": self._scan_editors(),
            "runtimes": self._scan_runtimes(),
            "package_managers": self._scan_package_managers(),
            "dev_tools": self._scan_dev_tools(),
        }

    # -- system -------------------------------------------------------

    def _scan_system(self) -> SystemInfo:
        cpu_cores = 0
        ram_gb = 0.0
        gpu: list[str] = []

        if psutil is not None:
            cpu_cores = psutil.cpu_count(logical=True) or 0
            ram_gb = round(psutil.virtual_memory().total / (1024 ** 3), 1)
        else:
            import os as _os
            cpu_cores = _os.cpu_count() or 0

        gpu = self._detect_gpu()

        return SystemInfo(
            os=utils.os_name(),
            os_version=platform.version(),
            architecture=platform.machine(),
            hostname=platform.node(),
            cpu=platform.processor() or platform.uname().processor or "unknown",
            cpu_cores=cpu_cores,
            ram_gb=ram_gb,
            gpu=gpu,
        )

    def _detect_gpu(self) -> list[str]:
        gpus: list[str] = []
        system = utils.os_name()

        if system == "linux":
            out = utils.run_cmd(["nvidia-smi", "--query-gpu=name", "--format=csv,noheader"])
            if out:
                gpus.extend(line.strip() for line in out.splitlines() if line.strip())
            if not gpus:
                out = utils.run_cmd(["lspci"])
                if out:
                    for line in out.splitlines():
                        if "VGA" in line or "3D controller" in line:
                            gpus.append(line.split(":", 2)[-1].strip())
        elif system == "macos":
            out = utils.run_cmd(["system_profiler", "SPDisplaysDataType"])
            if out:
                for line in out.splitlines():
                    if "Chipset Model" in line:
                        gpus.append(line.split(":", 1)[-1].strip())
        elif system == "windows":
            out = utils.run_cmd(
                ["wmic", "path", "win32_VideoController", "get", "name"]
            )
            if out:
                lines = [l.strip() for l in out.splitlines() if l.strip() and l.strip() != "Name"]
                gpus.extend(lines)

        return gpus

    # -- editors --------------------------------------------------------

    def _scan_editors(self) -> dict[str, Any]:
        editors: dict[str, Any] = {}
        vscode = self._scan_vscode()
        if vscode:
            editors["vscode"] = vscode
        return editors

    def _scan_vscode(self) -> dict[str, Any] | None:
        code_path = utils.which("code") or utils.which("code-insiders")
        user_dir = utils.vscode_user_dir()

        if not code_path and not user_dir:
            return None  # VS Code not detected on this machine

        info: dict[str, Any] = {
            "installed": True,
            "cli_available": code_path is not None,
            "version": self._vscode_version(code_path),
            "extensions": self._vscode_extensions(code_path),
        }

        if user_dir:
            info["settings"] = utils.read_json_safe(user_dir / "settings.json")
            info["keybindings"] = utils.read_json_safe(user_dir / "keybindings.json")
            info["snippets"] = self._vscode_snippets(user_dir)
            info["user_dir"] = str(user_dir)
        else:
            info["settings"] = {}
            info["keybindings"] = {}
            info["snippets"] = {}

        info["theme"] = info["settings"].get("workbench.colorTheme", "Default")
        return info

    def _vscode_version(self, code_path: str | None) -> str:
        if not code_path:
            return "unknown"
        out = utils.run_cmd([code_path, "--version"])
        if out:
            return out.splitlines()[0].strip()
        return "unknown"

    def _vscode_extensions(self, code_path: str | None) -> list[str]:
        if not code_path:
            return []
        out = utils.run_cmd([code_path, "--list-extensions"])
        if not out:
            return []
        return sorted(line.strip() for line in out.splitlines() if line.strip())

    def _vscode_snippets(self, user_dir: Path) -> dict[str, Any]:
        snippets_dir = user_dir / "snippets"
        result: dict[str, Any] = {}
        if snippets_dir.exists():
            for f in snippets_dir.glob("*.json"):
                result[f.name] = utils.read_json_safe(f)
        return result

    # -- runtimes ---------------------------------------------------------

    def _scan_runtimes(self) -> dict[str, Any]:
        runtimes: dict[str, Any] = {}

        py_version = utils.run_cmd(["python3", "--version"]) or utils.run_cmd(["python", "--version"])
        if py_version:
            runtimes["python"] = {
                "version": py_version.replace("Python ", "").strip(),
                "path": utils.which("python3") or utils.which("python"),
            }

        node_version = utils.run_cmd(["node", "--version"])
        if node_version:
            runtimes["node"] = {
                "version": node_version.lstrip("v"),
                "path": utils.which("node"),
            }

        java_version = utils.run_cmd(["java", "-version"])
        if java_version:
            # java -version prints to stderr, first line has the version string
            first_line = java_version.splitlines()[0] if java_version else ""
            runtimes["java"] = {
                "version": first_line,
                "path": utils.which("java"),
            }

        return runtimes

    # -- package managers ---------------------------------------------------

    def _scan_package_managers(self) -> dict[str, Any]:
        managers = {}
        candidates = {
            "pip": ["pip3", "--version"],
            "npm": ["npm", "--version"],
            "yarn": ["yarn", "--version"],
            "pnpm": ["pnpm", "--version"],
            "conda": ["conda", "--version"],
            "brew": ["brew", "--version"],
            "apt": ["apt", "--version"],
            "cargo": ["cargo", "--version"],
            "gem": ["gem", "--version"],
        }
        for name, cmd in candidates.items():
            out = utils.run_cmd(cmd)
            if out:
                managers[name] = out.splitlines()[0].strip()
        return managers

    # -- dev tools ------------------------------------------------------

    def _scan_dev_tools(self) -> dict[str, Any]:
        tools = {}
        candidates = {
            "git": ["git", "--version"],
            "docker": ["docker", "--version"],
            "kubectl": ["kubectl", "version", "--client", "--short"],
            "terraform": ["terraform", "--version"],
            "aws": ["aws", "--version"],
            "gh": ["gh", "--version"],
            "make": ["make", "--version"],
        }
        for name, cmd in candidates.items():
            out = utils.run_cmd(cmd)
            if out:
                tools[name] = out.splitlines()[0].strip()
        return tools
