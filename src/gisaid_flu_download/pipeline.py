"""Run configured GISAID downloads and merge each result in one command."""

from __future__ import annotations

import argparse
import subprocess
import sys
from dataclasses import dataclass
from pathlib import Path
from typing import Callable, Optional, Sequence

from .merger import merge_folder_data


@dataclass(frozen=True)
class PipelineJob:
    """Resolved paths and output selections for one YAML configuration."""

    config_path: Path
    download_root: Path
    merge_meta: bool
    merge_dna: bool
    merge_protein: bool


def load_job(config_path: Path) -> PipelineJob:
    """Read one config without starting a browser."""
    try:
        import yaml
    except ImportError as exc:
        raise RuntimeError(
            "Reading YAML config requires PyYAML; install the project "
            "dependencies first."
        ) from exc

    resolved_config = config_path.expanduser().resolve()
    if not resolved_config.is_file():
        raise FileNotFoundError(f"Config file not found: {resolved_config}")

    with resolved_config.open("r", encoding="utf-8") as config_file:
        raw = yaml.safe_load(config_file) or {}

    if not isinstance(raw, dict):
        raise ValueError(f"Config top level must be a mapping: {resolved_config}")

    runtime = raw.get("runtime") or {}
    options = raw.get("options") or {}
    if not isinstance(runtime, dict) or not isinstance(options, dict):
        raise ValueError(f"runtime and options must be mappings: {resolved_config}")

    download_root_value = runtime.get("download_root")
    if not download_root_value:
        raise ValueError(f"Config is missing runtime.download_root: {resolved_config}")

    return PipelineJob(
        config_path=resolved_config,
        download_root=Path(download_root_value).expanduser().resolve(),
        merge_meta=bool(options.get("download_metadata", True)),
        merge_dna=bool(options.get("download_dna", False)),
        merge_protein=bool(options.get("download_protein", True)),
    )


def run_pipeline(
    config_file: Path,
    *,
    run_command: Callable[..., object] = subprocess.run,
) -> None:
    """Download and then merge one configuration."""
    job = load_job(config_file)
    print(f"\n===== Processing config: {job.config_path} =====")
    run_command(
        [
            sys.executable,
            "-m",
            "gisaid_flu_download.downloader",
            str(job.config_path),
        ],
        check=True,
    )

    merge_folder_data(
        job.download_root.parent,
        job.download_root.name,
        merge_meta=job.merge_meta,
        merge_dna=job.merge_dna,
        merge_protein=job.merge_protein,
    )
    print(f"===== Config processing complete: {job.config_path} =====")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Download and merge one GISAID configuration"
    )
    parser.add_argument(
        "config",
        type=Path,
        help="One YAML config file for this run",
    )
    return parser


def main(argv: Optional[Sequence[str]] = None) -> int:
    args = build_parser().parse_args(argv)
    try:
        run_pipeline(args.config)
    except (FileNotFoundError, RuntimeError, ValueError, subprocess.CalledProcessError) as exc:
        print(f"Pipeline failed: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
