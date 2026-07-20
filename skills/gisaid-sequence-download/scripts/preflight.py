#!/usr/bin/env python3
"""Check a GISAID job without exposing credentials or opening the website."""

from __future__ import annotations

import argparse
import os
import re
import shutil
import subprocess
import sys
from pathlib import Path
from typing import Any, Sequence

from configure_yaml import load_config


PROJECT_NAME = "gisaid-flu-download"
EDGE_COMMANDS = ("microsoft-edge", "microsoft-edge-stable", "msedge")


def _find_project_root(config_path: Path) -> Path:
    candidates: list[Path] = []
    for start in (Path.cwd().resolve(), config_path.parent):
        candidates.extend((start, *start.parents))

    seen: set[Path] = set()
    for candidate in candidates:
        if candidate in seen:
            continue
        seen.add(candidate)
        pyproject = candidate / "pyproject.toml"
        if not pyproject.is_file():
            continue
        text = pyproject.read_text(encoding="utf-8")
        if re.search(
            rf"(?m)^name\s*=\s*['\"]{re.escape(PROJECT_NAME)}['\"]\s*$",
            text,
        ):
            return candidate
    raise ValueError(f"cannot locate the {PROJECT_NAME} project root")


def _is_ignored(project_root: Path, config_path: Path) -> bool:
    result = subprocess.run(
        ["git", "-C", str(project_root), "check-ignore", "--quiet", str(config_path)],
        check=False,
        capture_output=True,
        text=True,
    )
    if result.returncode not in (0, 1):
        detail = result.stderr.strip() or "git check-ignore failed"
        raise ValueError(detail)
    return result.returncode == 0


def _edge_details() -> tuple[str | None, str | None]:
    edge_path = next((shutil.which(name) for name in EDGE_COMMANDS if shutil.which(name)), None)
    if edge_path is None:
        return None, None
    result = subprocess.run(
        [edge_path, "--version"],
        check=False,
        capture_output=True,
        text=True,
        timeout=15,
    )
    version = (result.stdout or result.stderr).strip() or None
    return edge_path, version


def _existing_ancestor(path: Path) -> Path:
    candidate = path
    while not candidate.exists() and candidate != candidate.parent:
        candidate = candidate.parent
    return candidate


def _checkpoint_counts(output_root: Path, enabled_types: list[str]) -> tuple[int, int]:
    directory_names = {"metadata": "meta", "DNA": "DNA", "protein": "protein"}
    nonempty = 0
    empty = 0
    for output_type in enabled_types:
        output_dir = output_root / directory_names[output_type]
        if not output_dir.is_dir():
            continue
        for path in output_dir.iterdir():
            if not path.is_file():
                continue
            if path.stat().st_size > 0:
                nonempty += 1
            else:
                empty += 1
    return nonempty, empty


def _enabled_types(options: dict[str, Any]) -> list[str]:
    enabled: list[str] = []
    if bool(options.get("download_metadata", True)):
        enabled.append("metadata")
    if bool(options.get("download_dna", False)):
        enabled.append("DNA")
    if bool(options.get("download_protein", True)):
        enabled.append("protein")
    return enabled


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Preflight a GISAID YAML without opening GISAID"
    )
    parser.add_argument("config", type=Path)
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    config_path = args.config.expanduser().resolve()
    errors: list[str] = []

    try:
        raw = load_config(config_path)
        project_root = _find_project_root(config_path)
    except (OSError, TypeError, ValueError) as exc:
        print(f"Preflight error: {exc}", file=sys.stderr)
        return 1

    if sys.version_info[:2] != (3, 10):
        errors.append(
            f"Python 3.10 is required; found {sys.version_info.major}.{sys.version_info.minor}"
        )
    if Path.cwd().resolve() != project_root:
        errors.append(f"run from the project root: {project_root}")
    try:
        if not _is_ignored(project_root, config_path):
            errors.append("config is not ignored by Git")
    except ValueError as exc:
        errors.append(str(exc))

    runtime = raw.get("runtime", {})
    dates = raw.get("dates", {})
    options = raw.get("options", {})
    output_root = Path(str(runtime["download_root"])).expanduser().resolve()
    enabled_types = _enabled_types(options)
    headless = bool(runtime.get("headless", False))
    manual_validation = bool(options.get("require_manual_validation", False))
    display_available = bool(os.environ.get("DISPLAY") or os.environ.get("WAYLAND_DISPLAY"))

    edge_path, edge_version = _edge_details()
    if edge_path is None:
        errors.append("Microsoft Edge is not available on PATH")
    if headless and manual_validation:
        errors.append("manual validation requires runtime.headless=false")
    if not headless and not display_available:
        errors.append("a non-headless run requires interactive DISPLAY or WAYLAND_DISPLAY")

    disk_root = _existing_ancestor(output_root)
    free_gib = shutil.disk_usage(disk_root).free / (1024**3)
    nonempty, empty = _checkpoint_counts(output_root, enabled_types)
    raw_ranges = dates.get("date_ranges") or []
    range_state = str(len(raw_ranges)) if raw_ranges else "automatic (not checkpointed)"

    print(f"config: {config_path}")
    print(f"output_root: {output_root}")
    print(f"enabled_types: {', '.join(enabled_types)}")
    print(f"date_ranges: {range_state}")
    print(f"checkpoint_files: {nonempty} non-empty, {empty} empty")
    print(f"free_disk_gib: {free_gib:.1f}")
    print(f"headless: {str(headless).lower()}")
    print(f"manual_validation: {str(manual_validation).lower()}")
    print(f"interactive_display: {str(display_available).lower()}")
    print(f"edge: {edge_path or 'missing'}")
    print(f"edge_version: {edge_version or 'unknown'}")

    if errors:
        for error in errors:
            print(f"BLOCKED: {error}", file=sys.stderr)
        return 1
    print("Preflight OK")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
