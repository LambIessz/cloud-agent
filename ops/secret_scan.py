#!/usr/bin/env python3
"""Lightweight secret scan for OpenAI-style keys.

Used by local pre-commit and CI to block accidental commits of real secrets.
"""

from __future__ import annotations

import argparse
import re
import subprocess
import sys
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]
SECRET_PATTERN = re.compile(r"sk-[A-Za-z0-9]{20,}")
REDACT_PATTERN = re.compile(r"sk-[A-Za-z0-9_-]{8,}")


def _tracked_files() -> list[Path]:
    result = subprocess.run(
        ["git", "ls-files", "-z"],
        cwd=ROOT,
        check=True,
        capture_output=True,
        text=False,
    )
    raw = result.stdout.decode("utf-8", errors="replace")
    files = [item for item in raw.split("\0") if item]
    return [ROOT / item for item in files]


def _resolve_paths(raw_paths: list[str]) -> list[Path]:
    if not raw_paths:
        return _tracked_files()

    resolved: list[Path] = []
    for raw in raw_paths:
        path = Path(raw)
        if not path.is_absolute():
            path = ROOT / path
        if path.is_dir():
            resolved.extend(p for p in path.rglob("*") if p.is_file())
        elif path.is_file():
            resolved.append(path)
    return resolved


def _scan_file(path: Path) -> list[tuple[int, str]]:
    try:
        text = path.read_text(encoding="utf-8", errors="ignore")
    except OSError:
        return []

    findings: list[tuple[int, str]] = []
    for lineno, line in enumerate(text.splitlines(), start=1):
        if SECRET_PATTERN.search(line):
            findings.append((lineno, REDACT_PATTERN.sub("sk-<redacted>", line.strip())))
    return findings


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Scan for accidental secret keys")
    parser.add_argument("paths", nargs="*", help="Files or directories to scan. Defaults to tracked files.")
    args = parser.parse_args(argv)

    findings: list[tuple[Path, int, str]] = []
    for path in _resolve_paths(args.paths):
        for lineno, snippet in _scan_file(path):
            try:
                rel = path.relative_to(ROOT)
            except ValueError:
                rel = path
            findings.append((rel, lineno, snippet))

    if findings:
        print("Secret scan failed: real secret-like patterns were found.", file=sys.stderr)
        for rel, lineno, snippet in findings:
            print(f"{rel}:{lineno}: {snippet}", file=sys.stderr)
        return 1

    print("Secret scan passed: no real secret-like patterns found.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
