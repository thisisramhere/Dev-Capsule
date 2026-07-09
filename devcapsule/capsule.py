"""
Dev Capsule Generator
======================
Combines the scanner output + AI tool detection into a single portable
`devcapsule.yaml` file, after routing anything secret-looking through
the SecurityManager so the capsule itself is always safe to share,
commit, or email.
"""

from __future__ import annotations

import datetime
from pathlib import Path
from typing import Any

import yaml

from .ai_tools import AIEnvironmentDetector
from .scanner import EnvironmentScanner
from .security import SecurityManager

CAPSULE_VERSION = "1.0"


class CapsuleGenerator:
    def __init__(self, project_root: Path | None = None):
        self.project_root = project_root or Path.cwd()
        self.scanner = EnvironmentScanner()
        self.security = SecurityManager()

    def build(self) -> dict[str, Any]:
        """Run scan + AI detection and assemble the raw (pre-scrub) capsule dict."""
        scan = self.scanner.scan()
        vscode_extensions = scan.get("editors", {}).get("vscode", {}).get("extensions", [])
        ai = AIEnvironmentDetector(vscode_extensions=vscode_extensions, project_root=self.project_root).detect()

        capsule = {
            "devcapsule_version": CAPSULE_VERSION,
            "generated_at": datetime.datetime.utcnow().isoformat() + "Z",
            "system": scan["system"],
            "editors": scan["editors"],
            "runtimes": scan["runtimes"],
            "package_managers": scan["package_managers"],
            "dev_tools": scan["dev_tools"],
            "ai_tools": ai,
            "required_installations": self._derive_required_installations(scan, ai),
        }
        return capsule

    def _derive_required_installations(self, scan: dict, ai: dict) -> dict[str, Any]:
        """
        Summarize, in one place, everything a restore on a fresh machine
        would need to install -- used to drive the restore engine and to
        give the user a quick checklist without reading the whole file.
        """
        req: dict[str, Any] = {}

        vscode = scan.get("editors", {}).get("vscode")
        if vscode:
            req["vscode"] = {"required": True}
            req["vscode_extensions"] = vscode.get("extensions", [])

        runtimes = scan.get("runtimes", {})
        req["runtimes"] = {name: info["version"] for name, info in runtimes.items()}

        pkg_managers = scan.get("package_managers", {})
        req["package_managers"] = list(pkg_managers.keys())

        if "ollama" in ai:
            req["ollama_models"] = ai["ollama"].get("local_models", [])

        return req

    def export(self, out_path: Path, encrypt_secrets: bool = True,
               vault_path: Path | None = None, password: str | None = None) -> dict[str, Any]:
        """
        Build the capsule, scrub secrets out of it, optionally encrypt the
        scrubbed secrets into a vault file, and write the plaintext capsule
        YAML to `out_path`. Returns the final (scrubbed) capsule dict.
        """
        raw_capsule = self.build()
        clean_capsule, findings = self.security.scrub(raw_capsule)

        # Also sweep for local .env files near the project and note their existence
        # (contents are never embedded in the capsule; we only flag them).
        dotenv_files = self.security.find_dotenv_files(self.project_root)
        if dotenv_files:
            clean_capsule.setdefault("security", {})["dotenv_files_detected"] = [
                str(p) for p in dotenv_files
            ]

        if findings:
            clean_capsule.setdefault("security", {})["secrets_redacted_count"] = len(findings)
            if encrypt_secrets:
                vault_path = vault_path or out_path.with_suffix(".vault")
                pw = password or self.security.prompt_password(confirm=True)
                self.security.encrypt_vault(findings, pw, vault_path)
                clean_capsule["security"]["vault_file"] = str(vault_path)
            else:
                clean_capsule["security"]["warning"] = (
                    "Secrets were redacted but NOT encrypted anywhere. "
                    "Re-run with encryption enabled to preserve them for restore."
                )

        out_path.write_text(yaml.dump(clean_capsule, sort_keys=False, allow_unicode=True))
        return clean_capsule

    @staticmethod
    def load(path: Path) -> dict[str, Any]:
        text = path.read_text(encoding="utf-8")
        return yaml.safe_load(text) or {}
