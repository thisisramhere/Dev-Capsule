"""
Restore Engine
==============
Reads a devcapsule.yaml and rebuilds the captured environment on the
current machine: installs missing VS Code extensions, restores
settings/keybindings/snippets, reconfigures detected AI tools, pulls
required Ollama models, and generates an install script for anything
that needs a package manager / sudo (which DevCapsule never runs
silently on its own).

Every action logs a RestoreStep so callers (CLI or future GUI) can show
a clear before/after report, and everything supports --dry-run.
"""

from __future__ import annotations

import json
import shutil
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from . import utils


@dataclass
class RestoreStep:
    action: str
    target: str
    status: str  # "planned" | "done" | "skipped" | "failed"
    detail: str = ""


@dataclass
class RestoreReport:
    steps: list[RestoreStep] = field(default_factory=list)

    def add(self, action: str, target: str, status: str, detail: str = "") -> None:
        self.steps.append(RestoreStep(action, target, status, detail))

    def summary(self) -> str:
        counts: dict[str, int] = {}
        for s in self.steps:
            counts[s.status] = counts.get(s.status, 0) + 1
        return ", ".join(f"{v} {k}" for k, v in counts.items())


class RestoreEngine:
    def __init__(self, capsule: dict[str, Any], dry_run: bool = True):
        self.capsule = capsule
        self.dry_run = dry_run
        self.report = RestoreReport()

    # -- orchestration ---------------------------------------------------

    def run(self) -> RestoreReport:
        self._restore_vscode_extensions()
        self._restore_vscode_settings()
        self._restore_ai_tools()
        self._restore_ollama_models()
        self._check_runtimes()
        install_script = self._generate_install_script()
        if install_script:
            self.report.add("generate_script", str(install_script), "done",
                             "Run this script to install any missing runtimes/tools.")
        return self.report

    # -- VS Code ----------------------------------------------------------

    def _restore_vscode_extensions(self) -> None:
        vscode_cfg = self.capsule.get("editors", {}).get("vscode")
        if not vscode_cfg:
            return

        wanted = set(vscode_cfg.get("extensions", []))
        code_path = utils.which("code") or utils.which("code-insiders")

        if not code_path:
            self.report.add("install_extensions", "vscode", "skipped",
                             "VS Code CLI ('code') not found on PATH -- install VS Code first.")
            return

        installed_out = utils.run_cmd([code_path, "--list-extensions"]) or ""
        installed = set(l.strip() for l in installed_out.splitlines() if l.strip())
        missing = sorted(wanted - installed)

        if not missing:
            self.report.add("install_extensions", "vscode", "skipped", "All extensions already present.")
            return

        for ext in missing:
            if self.dry_run:
                self.report.add("install_extension", ext, "planned")
                continue
            out = utils.run_cmd([code_path, "--install-extension", ext, "--force"], timeout=60)
            status = "done" if out is not None else "failed"
            self.report.add("install_extension", ext, status)

    def _restore_vscode_settings(self) -> None:
        vscode_cfg = self.capsule.get("editors", {}).get("vscode")
        if not vscode_cfg:
            return

        user_dir = utils.vscode_user_dir()
        if not user_dir:
            self.report.add("restore_settings", "vscode", "skipped",
                             "No local VS Code User directory found -- install/run VS Code once first.")
            return

        self._write_json_with_backup(user_dir / "settings.json", vscode_cfg.get("settings", {}), "settings.json")
        self._write_json_with_backup(user_dir / "keybindings.json", vscode_cfg.get("keybindings", {}), "keybindings.json")

        snippets = vscode_cfg.get("snippets", {})
        if snippets:
            snippets_dir = user_dir / "snippets"
            if not self.dry_run:
                snippets_dir.mkdir(parents=True, exist_ok=True)
            for fname, content in snippets.items():
                target = snippets_dir / fname
                if self.dry_run:
                    self.report.add("restore_snippet", fname, "planned")
                else:
                    target.write_text(json.dumps(content, indent=2))
                    self.report.add("restore_snippet", fname, "done")

    def _write_json_with_backup(self, path: Path, content: dict, label: str) -> None:
        if not content:
            self.report.add("restore_file", label, "skipped", "Nothing captured in capsule.")
            return
        if self.dry_run:
            self.report.add("restore_file", label, "planned",
                             f"Would back up existing {label} (if any) then overwrite.")
            return
        if path.exists():
            backup = path.with_suffix(path.suffix + ".devcapsule-backup")
            shutil.copy2(path, backup)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(content, indent=2))
        self.report.add("restore_file", label, "done")

    # -- AI tools -----------------------------------------------------------

    def _restore_ai_tools(self) -> None:
        ai = self.capsule.get("ai_tools", {})
        if not ai:
            return

        if "github_copilot" in ai:
            self.report.add("reconfigure_ai_tool", "GitHub Copilot", "planned",
                             "Sign in via VS Code's Copilot panel after extension install (auth is device-linked).")

        if "continue" in ai:
            self._restore_continue(ai["continue"])

        if "cline" in ai:
            self.report.add("reconfigure_ai_tool", "Cline", "planned",
                             "Re-enter provider API key in Cline's VS Code sidebar settings.")

        if "opencode" in ai:
            self._restore_opencode(ai["opencode"])

        custom_rules = ai.get("custom_rules", {})
        for rel_path, content in custom_rules.items():
            target = Path.cwd() / rel_path
            if self.dry_run:
                self.report.add("restore_rules_file", rel_path, "planned")
            else:
                target.parent.mkdir(parents=True, exist_ok=True)
                target.write_text(content)
                self.report.add("restore_rules_file", rel_path, "done")

    def _restore_continue(self, cfg: dict[str, Any]) -> None:
        target = utils.home() / ".continue" / "config.json"
        if self.dry_run:
            self.report.add("reconfigure_ai_tool", "Continue", "planned",
                             f"Would write model provider list to {target}")
            return
        target.parent.mkdir(parents=True, exist_ok=True)
        payload = {"models": cfg.get("models", [])}
        if target.exists():
            shutil.copy2(target, target.with_suffix(".json.devcapsule-backup"))
        target.write_text(json.dumps(payload, indent=2))
        self.report.add("reconfigure_ai_tool", "Continue", "done")

    def _restore_opencode(self, cfg: dict[str, Any]) -> None:
        exe = utils.which("opencode")
        if not exe:
            self.report.add("reconfigure_ai_tool", "OpenCode", "skipped", "OpenCode CLI not installed.")
            return
        self.report.add("reconfigure_ai_tool", "OpenCode", "planned" if self.dry_run else "done",
                         f"Providers to reconfigure: {cfg.get('providers', [])}")

    # -- Ollama models ----------------------------------------------------

    def _restore_ollama_models(self) -> None:
        ollama_cfg = self.capsule.get("ai_tools", {}).get("ollama")
        if not ollama_cfg:
            return

        exe = utils.which("ollama")
        if not exe:
            self.report.add("pull_ollama_models", "ollama", "skipped",
                             "Ollama not installed -- install from https://ollama.com first.")
            return

        installed_out = utils.run_cmd(["ollama", "list"]) or ""
        installed = {line.split()[0] for line in installed_out.splitlines()[1:] if line.split()}

        for model in ollama_cfg.get("local_models", []):
            if model in installed:
                self.report.add("pull_ollama_model", model, "skipped", "Already present.")
                continue
            if self.dry_run:
                self.report.add("pull_ollama_model", model, "planned")
            else:
                out = utils.run_cmd(["ollama", "pull", model], timeout=1800)
                self.report.add("pull_ollama_model", model, "done" if out is not None else "failed")

    # -- runtimes / install script -------------------------------------

    def _check_runtimes(self) -> None:
        wanted = self.capsule.get("runtimes", {})
        for name, info in wanted.items():
            current = utils.run_cmd([name, "--version"]) or utils.run_cmd([name, "-version"])
            wanted_version = info.get("version", "") if isinstance(info, dict) else str(info)
            if current is None:
                self.report.add("check_runtime", name, "planned", f"Missing -- need {wanted_version}")
            else:
                self.report.add("check_runtime", name, "skipped", f"Present: {current.splitlines()[0]}")

    def _generate_install_script(self) -> Path | None:
        """
        Emit a shell script listing install commands for anything missing.
        DevCapsule never runs package-manager installs automatically since
        that typically needs sudo / user confirmation.
        """
        missing_runtimes = [s.target for s in self.report.steps
                             if s.action == "check_runtime" and s.status == "planned"]
        missing_tools = []
        wanted_pkg_managers = self.capsule.get("package_managers", [])
        _ = wanted_pkg_managers  # currently informational only

        if not missing_runtimes and not missing_tools:
            return None

        lines = ["#!/usr/bin/env bash", "set -e", "# Generated by DevCapsule restore", ""]
        os_name = utils.os_name()
        for rt in missing_runtimes:
            lines.append(f"echo 'Installing {rt}...'")
            if rt == "python":
                lines.append("# See https://www.python.org/downloads/ or use your OS package manager")
                if os_name == "linux":
                    lines.append("sudo apt-get update && sudo apt-get install -y python3 python3-pip")
                elif os_name == "macos":
                    lines.append("brew install python3")
            elif rt == "node":
                lines.append("curl -fsSL https://fnm.vercel.app/install | bash  # installs fnm (Fast Node Manager)")
                lines.append("fnm install --lts")
            elif rt == "java":
                if os_name == "linux":
                    lines.append("sudo apt-get update && sudo apt-get install -y default-jdk")
                elif os_name == "macos":
                    lines.append("brew install openjdk")
            lines.append("")

        script_path = Path.cwd() / "devcapsule_install_missing.sh"
        if not self.dry_run:
            script_path.write_text("\n".join(lines))
            script_path.chmod(0o755)
        return script_path
