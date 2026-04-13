#!/usr/bin/env python3
from __future__ import annotations

import argparse
import re
import subprocess
from pathlib import Path

DISCORD_WEBHOOK_RE = re.compile(
    r"https://discord(?:app)?\.com/api/webhooks/\d{5,}/[A-Za-z0-9_-]{20,}"
)
ASSIGNMENT_RE = re.compile(
    r"(?i)\b(api[_-]?key|token|secret|password|webhook_url)\b\s*=\s*[\"']([^\"']+)[\"']"
)

SKIP_DIRS = {".git", ".venv", "build", "__pycache__", ".mypy_cache", ".pytest_cache", ".ruff_cache"}
SKIP_TOP_LEVEL = {"tests"}


def _is_safe_placeholder(value: str) -> bool:
    v = value.strip()
    low = v.lower()
    if "..." in v:
        return True
    if low.startswith("https://example."):
        return True
    if low.startswith("http://example."):
        return True
    if low in {"changeme", "replace_me", "dummy", "placeholder"}:
        return True
    if (v.startswith("${") and v.endswith("}")) or (v.startswith("$(") and v.endswith(")")):
        return True
    return False


def _tracked_files(root: Path) -> list[Path]:
    try:
        cp = subprocess.run(
            ["git", "ls-files"],
            cwd=root,
            check=True,
            capture_output=True,
            text=True,
        )
    except Exception:
        return _walk_files(root)
    files: list[Path] = []
    for line in cp.stdout.splitlines():
        p = root / line.strip()
        if p.is_file():
            rel_parts = p.relative_to(root).parts
            if rel_parts and rel_parts[0] in SKIP_TOP_LEVEL:
                continue
            files.append(p)
    return files


def _walk_files(root: Path) -> list[Path]:
    files: list[Path] = []
    for p in root.rglob("*"):
        if not p.is_file():
            continue
        if any(part in SKIP_DIRS for part in p.parts):
            continue
        try:
            rel_parts = p.relative_to(root).parts
        except ValueError:
            rel_parts = p.parts
        if rel_parts and rel_parts[0] in SKIP_TOP_LEVEL:
            continue
        files.append(p)
    return files


def _scan_text(path: Path, text: str) -> list[str]:
    issues: list[str] = []
    for idx, line in enumerate(text.splitlines(), start=1):
        if "secret-scan: allow" in line:
            continue

        if DISCORD_WEBHOOK_RE.search(line):
            issues.append(f"{path}:{idx}: discord webhook URL appears to contain a real secret")
            continue

        m = ASSIGNMENT_RE.search(line)
        if not m:
            continue
        key = m.group(1).lower()
        value = m.group(2)
        if _is_safe_placeholder(value):
            continue
        if key == "webhook_url" and value.startswith("https://"):
            issues.append(
                f"{path}:{idx}: webhook_url must use placeholder/example URL in public layer"
            )
            continue
        if key in {"api_key", "token", "secret", "password"}:
            issues.append(f"{path}:{idx}: possible secret literal in '{key}' assignment")
    return issues


def scan_paths(paths: list[Path]) -> list[str]:
    issues: list[str] = []
    for path in paths:
        try:
            data = path.read_bytes()
        except OSError:
            continue
        if b"\x00" in data:
            continue
        text = data.decode("utf-8", errors="ignore")
        issues.extend(_scan_text(path, text))
    return issues


def _resolve_paths(input_paths: list[str], root: Path) -> list[Path]:
    if input_paths:
        resolved: list[Path] = []
        for raw in input_paths:
            p = Path(raw)
            if not p.is_absolute():
                p = (root / p).resolve()
            if p.is_dir():
                resolved.extend([f for f in _walk_files(p)])
            elif p.is_file():
                resolved.append(p)
        return resolved
    return _tracked_files(root)


def main() -> int:
    parser = argparse.ArgumentParser(
        description="Fail if public-layer files include likely secrets"
    )
    parser.add_argument(
        "--paths",
        nargs="*",
        default=[],
        help="Optional file/dir paths to scan (default: all tracked files)",
    )
    args = parser.parse_args()

    root = Path.cwd()
    paths = _resolve_paths(args.paths, root)
    issues = scan_paths(paths)
    if issues:
        print("[secret-scan] potential secrets found:")
        for issue in issues:
            print(issue)
        return 1

    print(f"[secret-scan] ok: scanned {len(paths)} files")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
