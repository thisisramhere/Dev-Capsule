"""
Security Manager
=================
Finds likely secrets (API keys, tokens, env vars) inside scanned data,
strips them out of the plaintext capsule, and encrypts them into a
separate, password-protected vault file. Nothing sensitive is ever
written to disk in plaintext by DevCapsule.
"""

from __future__ import annotations

import base64
import getpass
import json
import os
import re
from pathlib import Path
from typing import Any

from cryptography.fernet import Fernet, InvalidToken
from cryptography.hazmat.primitives import hashes
from cryptography.hazmat.primitives.kdf.pbkdf2 import PBKDF2HMAC

# Patterns for common credential shapes. Kept conservative to minimize
# false positives while catching the most common providers.
SECRET_PATTERNS: dict[str, re.Pattern] = {
    "openai_api_key": re.compile(r"sk-[A-Za-z0-9]{20,}"),
    "anthropic_api_key": re.compile(r"sk-ant-[A-Za-z0-9\-_]{20,}"),
    "github_token": re.compile(r"gh[pousr]_[A-Za-z0-9]{20,}"),
    "aws_access_key": re.compile(r"AKIA[0-9A-Z]{16}"),
    "generic_bearer": re.compile(r"Bearer\s+[A-Za-z0-9\-._~+/]{20,}=*"),
    "slack_token": re.compile(r"xox[baprs]-[A-Za-z0-9-]{10,}"),
    "google_api_key": re.compile(r"AIza[0-9A-Za-z\-_]{35}"),
    "generic_hex_secret": re.compile(r"\b[A-Fa-f0-9]{32,64}\b"),
    "jwt": re.compile(r"eyJ[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+\.[A-Za-z0-9_-]+"),
}

# Keys whose *values* should always be treated as sensitive, regardless
# of whether the value matches a pattern above (e.g. short custom tokens).
SENSITIVE_KEY_HINTS = (
    "key", "token", "secret", "password", "passwd", "credential", "apikey", "auth",
)

SALT_FILE_MAGIC = b"DEVCAPSULE_VAULT_V1"


class SecurityManager:
    """Detects and encrypts secrets found in scanned configuration data."""

    def __init__(self):
        self._findings: list[dict[str, str]] = []

    # -- detection -----------------------------------------------------

    def scan_env_vars(self) -> dict[str, str]:
        """Return env vars whose *names* look sensitive (values are redacted)."""
        risky = {}
        for k, v in os.environ.items():
            if any(hint in k.lower() for hint in SENSITIVE_KEY_HINTS):
                risky[k] = self._redact(v)
        return risky

    def find_dotenv_files(self, root: Path) -> list[Path]:
        """Locate .env-style files near the project root (non-recursive into node_modules etc.)."""
        found = []
        skip_dirs = {"node_modules", ".git", "venv", ".venv", "__pycache__", "dist", "build"}
        for dirpath, dirnames, filenames in os.walk(root):
            dirnames[:] = [d for d in dirnames if d not in skip_dirs]
            for f in filenames:
                if f == ".env" or f.startswith(".env."):
                    found.append(Path(dirpath) / f)
        return found

    def scrub(self, data: Any, _path: str = "") -> tuple[Any, list[dict[str, str]]]:
        """
        Recursively walk a dict/list structure, replacing any secret-looking
        string values with a redaction placeholder. Returns (clean_data, findings).
        Findings hold the original key path + real value, meant to go straight
        into the encrypted vault -- never into the plaintext capsule.
        """
        findings: list[dict[str, str]] = []
        cleaned = self._scrub_recursive(data, _path, findings)
        return cleaned, findings

    def _scrub_recursive(self, node: Any, path: str, findings: list) -> Any:
        if isinstance(node, dict):
            new_dict = {}
            for k, v in node.items():
                new_path = f"{path}.{k}" if path else str(k)
                if isinstance(v, str) and self._looks_sensitive(k, v):
                    findings.append({"path": new_path, "value": v})
                    new_dict[k] = "***REDACTED***"
                else:
                    new_dict[k] = self._scrub_recursive(v, new_path, findings)
            return new_dict
        if isinstance(node, list):
            return [self._scrub_recursive(v, f"{path}[{i}]", findings) for i, v in enumerate(node)]
        if isinstance(node, str) and self._looks_sensitive(path.rsplit(".", 1)[-1], node):
            findings.append({"path": path, "value": node})
            return "***REDACTED***"
        return node

    def _looks_sensitive(self, key: str, value: str) -> bool:
        if not isinstance(value, str) or len(value) < 8:
            return False
        key_lower = str(key).lower()
        if any(hint in key_lower for hint in SENSITIVE_KEY_HINTS):
            return True
        for pattern in SECRET_PATTERNS.values():
            if pattern.search(value):
                return True
        return False

    def _redact(self, value: str) -> str:
        if len(value) <= 6:
            return "***"
        return f"{value[:3]}...{value[-2:]}"

    # -- encryption (vault) ---------------------------------------------

    @staticmethod
    def _derive_key(password: str, salt: bytes) -> bytes:
        kdf = PBKDF2HMAC(
            algorithm=hashes.SHA256(),
            length=32,
            salt=salt,
            iterations=390_000,
        )
        return base64.urlsafe_b64encode(kdf.derive(password.encode("utf-8")))

    def encrypt_vault(self, findings: list[dict[str, str]], password: str, out_path: Path) -> None:
        """Encrypt all discovered secrets into a single vault file."""
        salt = os.urandom(16)
        key = self._derive_key(password, salt)
        fernet = Fernet(key)
        payload = json.dumps(findings).encode("utf-8")
        token = fernet.encrypt(payload)

        out_path.write_bytes(SALT_FILE_MAGIC + b"\n" + salt.hex().encode() + b"\n" + token)

    def decrypt_vault(self, vault_path: Path, password: str) -> list[dict[str, str]]:
        raw = vault_path.read_bytes()
        lines = raw.split(b"\n", 2)
        if len(lines) != 3 or lines[0] != SALT_FILE_MAGIC:
            raise ValueError("Not a valid DevCapsule vault file.")
        salt = bytes.fromhex(lines[1].decode())
        token = lines[2]
        key = self._derive_key(password, salt)
        fernet = Fernet(key)
        try:
            payload = fernet.decrypt(token)
        except InvalidToken as exc:
            raise ValueError("Incorrect password or corrupted vault file.") from exc
        return json.loads(payload.decode("utf-8"))

    @staticmethod
    def prompt_password(confirm: bool = False) -> str:
        pw = getpass.getpass("Vault password (used to encrypt secrets): ")
        if confirm:
            pw2 = getpass.getpass("Confirm password: ")
            if pw != pw2:
                raise ValueError("Passwords did not match.")
        return pw
