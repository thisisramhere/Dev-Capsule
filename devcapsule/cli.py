"""
DevCapsule CLI
==============
Entry point wiring together the Scanner, Capsule Generator, Restore
Engine, Optimizer, and Security Manager behind a simple command set:

    devcapsule scan
    devcapsule export
    devcapsule restore
    devcapsule optimize
    devcapsule vault
"""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

import yaml

from .capsule import CapsuleGenerator
from .optimizer import HardwareOptimizer, HardwareProfile
from .restore import RestoreEngine
from .scanner import EnvironmentScanner
from .security import SecurityManager

GREEN = "\033[92m"
YELLOW = "\033[93m"
RED = "\033[91m"
BOLD = "\033[1m"
RESET = "\033[0m"


def _status_color(status: str) -> str:
    return {"done": GREEN, "planned": YELLOW, "skipped": "\033[90m", "failed": RED}.get(status, "")


def cmd_scan(args: argparse.Namespace) -> int:
    print(f"{BOLD}Scanning local development environment...{RESET}")
    scanner = EnvironmentScanner()
    snapshot = scanner.scan()

    out_path = Path(args.output)
    out_path.write_text(json.dumps(snapshot, indent=2))

    editors = snapshot.get("editors", {})
    vscode = editors.get("vscode")
    print(f"  System:   {snapshot['system']['os']} | {snapshot['system']['ram_gb']}GB RAM | "
          f"{snapshot['system']['cpu_cores']} cores | GPU: {', '.join(snapshot['system']['gpu']) or 'none detected'}")
    if vscode:
        print(f"  VS Code:  {vscode.get('version', 'unknown')} | {len(vscode.get('extensions', []))} extensions")
    else:
        print("  VS Code:  not detected")
    print(f"  Runtimes: {', '.join(snapshot['runtimes'].keys()) or 'none detected'}")
    print(f"  Tools:    {', '.join(snapshot['dev_tools'].keys()) or 'none detected'}")
    print(f"\n{GREEN}Snapshot saved to {out_path}{RESET}")
    return 0


def cmd_export(args: argparse.Namespace) -> int:
    print(f"{BOLD}Building portable dev capsule...{RESET}")
    gen = CapsuleGenerator(project_root=Path(args.project_root))
    out_path = Path(args.output)

    password = None
    if args.no_encrypt:
        encrypt = False
    else:
        encrypt = True
        password = args.password  # may be None -> prompted interactively only if secrets are found

    capsule = gen.export(out_path, encrypt_secrets=encrypt, password=password)

    ai_tools = capsule.get("ai_tools", {})
    security = capsule.get("security", {})

    print(f"  Editors detected:  {', '.join(capsule.get('editors', {}).keys()) or 'none'}")
    print(f"  Runtimes captured: {', '.join(capsule.get('runtimes', {}).keys()) or 'none'}")
    print(f"  AI tools found:    {', '.join(ai_tools.keys()) or 'none'}")
    if security.get("secrets_redacted_count"):
        print(f"  {YELLOW}Secrets redacted: {security['secrets_redacted_count']} "
              f"(saved to {security.get('vault_file', 'N/A')}){RESET}")
    if security.get("dotenv_files_detected"):
        print(f"  {YELLOW}.env files detected nearby (not copied): "
              f"{len(security['dotenv_files_detected'])}{RESET}")
    print(f"\n{GREEN}Capsule written to {out_path}{RESET}")
    return 0


def cmd_restore(args: argparse.Namespace) -> int:
    capsule_path = Path(args.input)
    if not capsule_path.exists():
        print(f"{RED}Capsule file not found: {capsule_path}{RESET}")
        return 1

    capsule = CapsuleGenerator.load(capsule_path)
    dry_run = not args.apply

    if dry_run:
        print(f"{YELLOW}DRY RUN{RESET} -- no changes will be made. Re-run with --apply to execute.\n")
    else:
        print(f"{BOLD}Applying capsule to this machine...{RESET}\n")

    engine = RestoreEngine(capsule, dry_run=dry_run)
    report = engine.run()

    for step in report.steps:
        color = _status_color(step.status)
        detail = f" -- {step.detail}" if step.detail else ""
        print(f"  [{color}{step.status.upper():8s}{RESET}] {step.action:<24s} {step.target}{detail}")

    print(f"\n{BOLD}Summary:{RESET} {report.summary()}")
    if dry_run:
        print("Run again with --apply to perform these actions.")
    return 0


def cmd_optimize(args: argparse.Namespace) -> int:
    capsule = CapsuleGenerator.load(Path(args.capsule))
    old_hw = HardwareProfile.from_system_dict(capsule.get("system", {}))

    if args.new_ram is not None:
        new_hw = HardwareProfile(
            ram_gb=args.new_ram,
            cpu_cores=args.new_cores or 0,
            has_gpu=args.new_gpu,
            gpu_names=["(specified manually)"] if args.new_gpu else [],
        )
    else:
        # Compare against *this* machine
        current = EnvironmentScanner()._scan_system().to_dict()
        new_hw = HardwareProfile.from_system_dict(current)

    ollama_models = capsule.get("ai_tools", {}).get("ollama", {}).get("local_models", [])
    result = HardwareOptimizer().analyze(old_hw, new_hw, configured_ollama_models=ollama_models)

    print(f"{BOLD}Hardware comparison{RESET}")
    print(f"  Old machine tier: {result['old_tier']}")
    print(f"  New machine tier: {result['new_tier']}")
    print(f"  RAM delta:        {result['ram_delta_gb']:+.1f}GB\n")

    for w in result["warnings"]:
        print(f"  {YELLOW}! {w}{RESET}")
    print(f"\n{BOLD}Recommendations:{RESET}")
    for r in result["recommendations"]:
        print(f"  - {r}")
    if result["model_specific_notes"]:
        print(f"\n{BOLD}Model-specific notes:{RESET}")
        for n in result["model_specific_notes"]:
            print(f"  - {n}")
    return 0


def cmd_vault(args: argparse.Namespace) -> int:
    sec = SecurityManager()
    if args.vault_action == "show":
        password = args.password or sec.prompt_password()
        try:
            findings = sec.decrypt_vault(Path(args.vault), password)
        except ValueError as exc:
            print(f"{RED}{exc}{RESET}")
            return 1
        print(f"{BOLD}{len(findings)} secret(s) in vault:{RESET}")
        for f in findings:
            print(f"  - {f['path']}")
        return 0
    print(f"{RED}Unknown vault action.{RESET}")
    return 1


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        prog="devcapsule",
        description="DevCapsule -- AI-Powered Developer Environment Replicator",
    )
    sub = parser.add_subparsers(dest="command", required=True)

    p_scan = sub.add_parser("scan", help="Scan this machine and save a raw environment snapshot.")
    p_scan.add_argument("-o", "--output", default="devcapsule_scan.json", help="Output JSON path.")
    p_scan.set_defaults(func=cmd_scan)

    p_export = sub.add_parser("export", help="Build a portable devcapsule.yaml from this machine.")
    p_export.add_argument("-o", "--output", default="devcapsule.yaml", help="Output capsule path.")
    p_export.add_argument("--project-root", default=".", help="Project directory to check for rules/.env files.")
    p_export.add_argument("--no-encrypt", action="store_true", help="Skip secret encryption (not recommended).")
    p_export.add_argument("--password", default=None, help="Vault password (omit to be prompted securely).")
    p_export.set_defaults(func=cmd_export)

    p_restore = sub.add_parser("restore", help="Rebuild an environment from a devcapsule.yaml.")
    p_restore.add_argument("-i", "--input", default="devcapsule.yaml", help="Capsule file to restore from.")
    p_restore.add_argument("--apply", action="store_true", help="Actually perform the restore (default is dry-run).")
    p_restore.set_defaults(func=cmd_restore)

    p_opt = sub.add_parser("optimize", help="Compare old vs new hardware and recommend AI model configs.")
    p_opt.add_argument("-c", "--capsule", default="devcapsule.yaml", help="Capsule file describing the old machine.")
    p_opt.add_argument("--new-ram", type=float, default=None, help="New machine RAM in GB (omit to use this machine's specs).")
    p_opt.add_argument("--new-cores", type=int, default=None, help="New machine CPU core count.")
    p_opt.add_argument("--new-gpu", action="store_true", help="New machine has a dedicated GPU.")
    p_opt.set_defaults(func=cmd_optimize)

    p_vault = sub.add_parser("vault", help="Inspect an encrypted secrets vault.")
    p_vault.add_argument("vault_action", choices=["show"], help="Vault operation.")
    p_vault.add_argument("--vault", required=True, help="Path to the .vault file.")
    p_vault.add_argument("--password", default=None, help="Vault password (omit to be prompted securely).")
    p_vault.set_defaults(func=cmd_vault)

    return parser


def main(argv: list[str] | None = None) -> int:
    parser = build_parser()
    args = parser.parse_args(argv)
    try:
        return args.func(args)
    except KeyboardInterrupt:
        print("\nAborted.")
        return 130
    except Exception as exc:  # top-level safety net for a CLI tool
        print(f"{RED}Error: {exc}{RESET}", file=sys.stderr)
        return 1


if __name__ == "__main__":
    sys.exit(main())
