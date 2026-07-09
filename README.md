# DevCapsule — AI-Powered Developer Environment Replicator

DevCapsule scans a developer's machine, packages the full setup — editor,
extensions, settings, runtimes, package managers, dev tools, and AI coding
assistants — into a single portable `devcapsule.yaml`, and restores that
exact setup on a new machine with one command.

It goes further than editor-settings sync tools by capturing **AI coding
environments** (GitHub Copilot, Continue, Cline, OpenCode, Ollama) and by
including a **hardware-aware optimization layer** that adjusts model
recommendations when you move to a machine with less RAM or no GPU.

## Why

Standard sync tools (Settings Sync, dotfiles repos) cover editor prefs and
maybe extensions. They don't touch runtime versions, local AI model
configuration, or agent rules — so a "synced" new machine still needs hours
of manual setup. DevCapsule captures the whole picture in one file.

## Install

```bash
pip install -e .
# or without installing the CLI entry point:
pip install -r requirements.txt
```

Requires Python 3.9+. Optional external tools it can detect/use if present:
`code` (VS Code CLI), `ollama`, `opencode`, `node`, `java`, `git`, `docker`.

## Commands

### `devcapsule scan`
Read-only inspection of the current machine. Writes a raw JSON snapshot
(system info, editors, runtimes, package managers, dev tools).

```bash
devcapsule scan -o devcapsule_scan.json
```

### `devcapsule export`
Runs a scan + AI-tool detection, strips out anything that looks like a
secret (API keys, tokens, hex/JWT-shaped strings, anything with "key" /
"token" / "secret" in its own field name), and writes a clean, shareable
`devcapsule.yaml`. Detected secrets are encrypted into a separate
`.vault` file protected by a password — never written to disk in
plaintext.

```bash
devcapsule export -o devcapsule.yaml
# skip the vault entirely (not recommended):
devcapsule export --no-encrypt
```

`.env` files near the project are detected and flagged in the capsule
(`security.dotenv_files_detected`) but their contents are never read into
the capsule — they need to be copied manually or via your existing secrets
manager.

### `devcapsule restore`
Reads a capsule and rebuilds the environment: installs missing VS Code
extensions, restores `settings.json` / `keybindings.json` / snippets
(existing files are backed up with a `.devcapsule-backup` suffix first),
reconfigures detected AI tools, pulls missing Ollama models, and checks
runtime versions. Anything that needs a system package manager (Python,
Node, Java) is written to a generated `devcapsule_install_missing.sh`
script rather than run automatically, since that usually needs `sudo` /
explicit user consent.

```bash
devcapsule restore -i devcapsule.yaml          # dry run — shows the plan
devcapsule restore -i devcapsule.yaml --apply  # actually performs it
```

### `devcapsule optimize`
Compares the hardware the capsule was captured on against either a
manually specified spec or the machine you're currently running on, and
recommends local-model tier changes / cloud fallbacks.

```bash
# compare capsule's original machine against THIS machine
devcapsule optimize -c devcapsule.yaml

# or specify the new machine's specs manually
devcapsule optimize -c devcapsule.yaml --new-ram 8 --new-cores 4
```

### `devcapsule vault show`
Inspect (decrypt) which secret paths are stored in a vault file, without
printing the actual secret values.

```bash
devcapsule vault show --vault devcapsule.vault
```

## Capsule format

`devcapsule.yaml` is a plain, readable file:

```yaml
devcapsule_version: "1.0"
generated_at: "2026-07-09T04:14:27Z"
system:
  os: linux
  ram_gb: 32.0
  cpu_cores: 16
  gpu: ["NVIDIA RTX 4090"]
editors:
  vscode:
    version: "1.90.0"
    extensions: [...]
    settings: {...}
    keybindings: {...}
    snippets: {...}
    theme: "Default Dark+"
runtimes:
  python: {version: "3.12.3", path: "/usr/bin/python3"}
  node: {version: "22.22.2", path: "/usr/bin/node"}
package_managers: {pip: "...", npm: "...", ...}
dev_tools: {git: "...", docker: "...", ...}
ai_tools:
  github_copilot: {installed: true, extensions: [...]}
  continue: {installed: true, models: [...]}
  ollama: {local_models: ["llama3:8b"], note: "weights re-pulled on restore"}
  custom_rules: {"CLAUDE.md": "...", ".cursorrules": "..."}
required_installations: {...}   # quick checklist for restore
security:
  secrets_redacted_count: 2
  vault_file: "devcapsule.vault"
  dotenv_files_detected: ["/path/to/.env"]
```

Ollama/local model **weights are never copied** — only model names. The
restore engine re-pulls them with `ollama pull <model>` on the new
machine.

## Architecture

```
devcapsule/
  scanner.py    Scanner Engine    -- read-only machine inspection
  ai_tools.py   AI Detector       -- Copilot / Continue / Cline / OpenCode / Ollama
  security.py   Security Manager  -- secret detection + Fernet/PBKDF2 vault
  capsule.py    Capsule Generator -- assembles + scrubs devcapsule.yaml
  restore.py    Restore Engine    -- applies a capsule back to a machine
  optimizer.py  AI Optimizer      -- old-vs-new hardware model recommendations
  cli.py        CLI               -- argparse wiring for all commands
```

All destructive/installing actions in `restore.py` default to **dry-run**
and require `--apply`; anything needing `sudo` is emitted as a shell
script for the user to review, never executed silently.

## MVP scope (implemented)

- [x] VS Code detection: extensions, settings, keybindings, snippets, theme
- [x] Runtime detection: Python, Node.js, Java (+ path/version)
- [x] Package manager + dev tool detection
- [x] AI tool detection: Copilot, Continue, Cline, OpenCode, Ollama, custom rule files
- [x] Capsule generation (`devcapsule.yaml`) with secret scrubbing
- [x] Encrypted vault for secrets (PBKDF2 + Fernet/AES)
- [x] Restore engine (dry-run + `--apply`) for extensions/settings/AI config/models
- [x] Hardware-aware optimization recommendations
- [x] Automated tests for security + optimizer logic

## Roadmap beyond MVP

- Additional editors (JetBrains, Neovim, Sublime)
- Cross-platform install script execution with per-OS package manager detection
- React + Tauri desktop UI wrapping the same Python core
- Team/org capsule templates (shared baseline + personal overlay)
- Live LLM-backed optimizer (currently a transparent, deterministic rule table)

## Tests

```bash
python -m unittest tests.test_core -v
```
