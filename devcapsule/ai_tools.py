"""
AI Coding Environment Detector
===============================
Finds AI coding assistants installed on the machine (Copilot, Continue,
Cline, OpenCode, Ollama, ...) and captures their *configuration metadata*
only. Large model weights are NEVER copied -- only references to which
models are configured, so the restore engine can re-download them later.
"""

from __future__ import annotations

import json
from pathlib import Path
from typing import Any

from . import utils

# Extension IDs we recognize as AI coding assistants living inside VS Code
VSCODE_AI_EXTENSIONS = {
    "github.copilot": "GitHub Copilot",
    "github.copilot-chat": "GitHub Copilot Chat",
    "continue.continue": "Continue",
    "saoudrizwan.claude-dev": "Cline",
    "sourcegraph.cody-ai": "Cody",
    "codeium.codeium": "Codeium",
    "tabnine.tabnine-vscode": "Tabnine",
    "amazonwebservices.aws-toolkit-vscode": "Amazon Q / CodeWhisperer",
}

# Well-known "rules"/instructions files that store custom prompts.
CUSTOM_RULES_FILES = [
    ".continuerules",
    ".continue/rules.md",
    ".cursorrules",
    ".github/copilot-instructions.md",
    "CLAUDE.md",
    ".clinerules",
    "AGENTS.md",
]


class AIEnvironmentDetector:
    def __init__(self, vscode_extensions: list[str] | None = None, project_root: Path | None = None):
        self.vscode_extensions = set(vscode_extensions or [])
        self.project_root = project_root or Path.cwd()

    def detect(self) -> dict[str, Any]:
        result: dict[str, Any] = {}

        copilot = self._detect_copilot()
        if copilot:
            result["github_copilot"] = copilot

        continue_cfg = self._detect_continue()
        if continue_cfg:
            result["continue"] = continue_cfg

        cline_cfg = self._detect_cline()
        if cline_cfg:
            result["cline"] = cline_cfg

        opencode_cfg = self._detect_opencode()
        if opencode_cfg:
            result["opencode"] = opencode_cfg

        ollama_cfg = self._detect_ollama()
        if ollama_cfg:
            result["ollama"] = ollama_cfg

        rules = self._detect_custom_rules()
        if rules:
            result["custom_rules"] = rules

        return result

    # -- individual detectors -----------------------------------------

    def _detect_copilot(self) -> dict[str, Any] | None:
        installed = any(e.startswith("github.copilot") for e in self.vscode_extensions)
        if not installed:
            return None
        return {
            "installed": True,
            "extensions": [e for e in self.vscode_extensions if e.startswith("github.copilot")],
            "note": "Auth is device-linked; user must re-sign-in on the new machine.",
        }

    def _detect_continue(self) -> dict[str, Any] | None:
        installed = "continue.continue" in self.vscode_extensions
        config_path = utils.home() / ".continue" / "config.json"
        yaml_config_path = utils.home() / ".continue" / "config.yaml"

        if not installed and not config_path.exists() and not yaml_config_path.exists():
            return None

        cfg: dict[str, Any] = {"installed": installed}
        if config_path.exists():
            data = utils.read_json_safe(config_path)
            cfg["models"] = self._extract_model_refs(data)
            cfg["config_format"] = "json"
        elif yaml_config_path.exists():
            cfg["config_format"] = "yaml"
            cfg["config_path"] = str(yaml_config_path)

        return cfg

    def _detect_cline(self) -> dict[str, Any] | None:
        installed = "saoudrizwan.claude-dev" in self.vscode_extensions
        if not installed:
            return None
        # Cline stores settings inside VS Code globalStorage; we only note
        # its presence + provider hints from any local rules file.
        return {
            "installed": True,
            "note": "Provider/API settings live in VS Code globalStorage; re-enter API key on restore.",
        }

    def _detect_opencode(self) -> dict[str, Any] | None:
        candidates = [
            utils.home() / ".config" / "opencode" / "config.json",
            utils.home() / ".opencode" / "config.json",
        ]
        exe = utils.which("opencode")
        found_path = next((c for c in candidates if c.exists()), None)

        if not exe and not found_path:
            return None

        cfg: dict[str, Any] = {"installed": True, "cli_available": exe is not None}
        if found_path:
            data = utils.read_json_safe(found_path)
            cfg["config_path"] = str(found_path)
            cfg["models"] = self._extract_model_refs(data)
            cfg["providers"] = list(data.get("providers", {}).keys()) if isinstance(data.get("providers"), dict) else []
        return cfg

    def _detect_ollama(self) -> dict[str, Any] | None:
        exe = utils.which("ollama")
        models_dir = utils.home() / ".ollama" / "models"
        if not exe and not models_dir.exists():
            return None

        cfg: dict[str, Any] = {"installed": True, "cli_available": exe is not None}
        models: list[str] = []
        if exe:
            out = utils.run_cmd(["ollama", "list"])
            if out:
                lines = out.splitlines()[1:]  # skip header
                for line in lines:
                    parts = line.split()
                    if parts:
                        models.append(parts[0])
        cfg["local_models"] = models
        cfg["models_dir"] = str(models_dir) if models_dir.exists() else None
        # Explicitly note we do NOT capture the actual weight files.
        cfg["note"] = "Only model names captured; weights are re-pulled with 'ollama pull' on restore."
        return cfg

    def _extract_model_refs(self, data: dict[str, Any]) -> list[dict[str, Any]]:
        """Best-effort extraction of {provider, model} pairs from a config blob."""
        refs: list[dict[str, Any]] = []
        models = data.get("models")
        if isinstance(models, list):
            for m in models:
                if isinstance(m, dict):
                    refs.append({
                        "provider": m.get("provider", "unknown"),
                        "model": m.get("model", m.get("title", "unknown")),
                    })
        return refs

    def _detect_custom_rules(self) -> dict[str, str]:
        """Scan the current project root (not the whole disk) for known rule files."""
        found: dict[str, str] = {}
        for rel in CUSTOM_RULES_FILES:
            p = self.project_root / rel
            if p.exists() and p.is_file():
                try:
                    text = p.read_text(encoding="utf-8", errors="ignore")
                    found[rel] = text[:4000]  # cap size, this is metadata not a backup tool
                except OSError:
                    continue
        return found
