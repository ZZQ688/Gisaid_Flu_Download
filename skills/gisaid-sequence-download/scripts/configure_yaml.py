#!/usr/bin/env python3
"""Validate and atomically update a GISAID download YAML without exposing secrets."""

from __future__ import annotations

import argparse
import copy
import os
import stat
import sys
import tempfile
from datetime import datetime
from pathlib import Path
from typing import Any, Sequence

try:
    import yaml
except ImportError as exc:  # pragma: no cover - environment preflight
    raise SystemExit("PyYAML is required; activate the project environment first.") from exc


SECRET_PREFIX = "credentials"
DATE_FORMAT = "%Y-%m-%d"


def _mapping(raw: dict[str, Any], key: str) -> dict[str, Any]:
    value = raw.get(key)
    if value is None:
        value = {}
        raw[key] = value
    if not isinstance(value, dict):
        raise ValueError(f"{key} must be a mapping")
    return value


def _date_pair(value: Any, label: str) -> tuple[str, str] | None:
    if value in (None, []):
        return None
    if not isinstance(value, (list, tuple)) or len(value) != 2:
        raise ValueError(f"{label} must contain two YYYY-MM-DD values")
    start, end = str(value[0]), str(value[1])
    start_date = datetime.strptime(start, DATE_FORMAT)
    end_date = datetime.strptime(end, DATE_FORMAT)
    if start_date > end_date:
        raise ValueError(f"{label} start date is later than end date")
    return start, end


def validate_config(raw: Any) -> dict[str, Any]:
    if not isinstance(raw, dict):
        raise ValueError("configuration root must be a mapping")

    credentials = _mapping(raw, "credentials")
    runtime = _mapping(raw, "runtime")
    filters = _mapping(raw, "filters")
    dates = _mapping(raw, "dates")
    options = _mapping(raw, "options")

    if not credentials.get("username") or not credentials.get("password"):
        raise ValueError("credentials.username and credentials.password are required")
    if not runtime.get("download_root"):
        raise ValueError("runtime.download_root is required")
    if filters.get("virus_type", "A") not in ("A", "B"):
        raise ValueError("filters.virus_type must be A or B")

    collection_date = _date_pair(dates.get("collection_date"), "dates.collection_date")
    raw_ranges = dates.get("date_ranges") or []
    if not isinstance(raw_ranges, list):
        raise ValueError("dates.date_ranges must be a list")
    previous_end: datetime | None = None
    for index, value in enumerate(raw_ranges):
        date_range = _date_pair(value, f"dates.date_ranges[{index}]")
        assert date_range is not None
        start = datetime.strptime(date_range[0], DATE_FORMAT)
        end = datetime.strptime(date_range[1], DATE_FORMAT)
        if previous_end is not None and start <= previous_end:
            raise ValueError("dates.date_ranges must be ordered and non-overlapping")
        if collection_date is not None:
            collection_start = datetime.strptime(collection_date[0], DATE_FORMAT)
            collection_end = datetime.strptime(collection_date[1], DATE_FORMAT)
            if not collection_start <= start <= end <= collection_end:
                raise ValueError(
                    f"dates.date_ranges[{index}] is outside dates.collection_date"
                )
        previous_end = end
    if collection_date is None and not raw_ranges:
        raise ValueError("collection_date or date_ranges is required")

    max_strains = int(dates.get("max_strains_per_range", 20000))
    if max_strains <= 0:
        raise ValueError("dates.max_strains_per_range must be positive")

    for key, default in (
        ("page_timeout_sec", 40),
        ("download_timeout_sec", 1800),
        ("poll_interval_sec", 5),
        ("step_retries", 3),
    ):
        if int(runtime.get(key, default)) <= 0:
            raise ValueError(f"runtime.{key} must be positive")
    if int(runtime.get("retry_delay_sec", 5)) < 0:
        raise ValueError("runtime.retry_delay_sec cannot be negative")

    enabled = (
        bool(options.get("download_metadata", True)),
        bool(options.get("download_dna", False)),
        bool(options.get("download_protein", True)),
    )
    if not any(enabled):
        raise ValueError("enable at least one download type in options")
    return raw


def load_config(path: Path) -> dict[str, Any]:
    with path.open("r", encoding="utf-8") as config_file:
        return validate_config(yaml.safe_load(config_file) or {})


def _set_dotted_path(raw: dict[str, Any], assignment: str) -> None:
    key, separator, value_text = assignment.partition("=")
    if not separator or not key.strip():
        raise ValueError(f"invalid --set assignment: {assignment!r}")
    key = key.strip()
    parts = key.split(".")
    if parts[0] == SECRET_PREFIX:
        raise ValueError(
            "do not pass credentials on the command line; edit the ignored local YAML privately"
        )

    target: dict[str, Any] = raw
    for part in parts[:-1]:
        child = target.setdefault(part, {})
        if not isinstance(child, dict):
            raise ValueError(f"cannot set {key}: {part} is not a mapping")
        target = child
    target[parts[-1]] = yaml.safe_load(value_text)


def _atomic_write(path: Path, raw: dict[str, Any]) -> None:
    original_mode = stat.S_IMODE(path.stat().st_mode)
    temporary_path: Path | None = None
    try:
        with tempfile.NamedTemporaryFile(
            "w",
            encoding="utf-8",
            dir=path.parent,
            prefix=f".{path.name}.",
            suffix=".tmp",
            delete=False,
        ) as temporary_file:
            temporary_path = Path(temporary_file.name)
            yaml.safe_dump(
                raw,
                temporary_file,
                allow_unicode=True,
                sort_keys=False,
            )
            temporary_file.flush()
            os.fsync(temporary_file.fileno())
        temporary_path.chmod(original_mode)
        os.replace(temporary_path, path)
    finally:
        if temporary_path is not None and temporary_path.exists():
            temporary_path.unlink()


def _redacted(raw: dict[str, Any]) -> dict[str, Any]:
    visible = copy.deepcopy(raw)
    credentials = visible.get("credentials")
    if isinstance(credentials, dict):
        for key in ("username", "password"):
            if key in credentials:
                credentials[key] = "***"
    return visible


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(
        description="Validate and atomically update a GISAID download YAML"
    )
    parser.add_argument("config", type=Path)
    parser.add_argument(
        "--set",
        dest="assignments",
        action="append",
        default=[],
        metavar="KEY=YAML_VALUE",
        help="set a non-secret dotted key; repeat as needed",
    )
    parser.add_argument(
        "--clear-date-ranges",
        action="store_true",
        help="clear dates.date_ranges so the downloader recalculates them",
    )
    parser.add_argument(
        "--show",
        action="store_true",
        help="print the validated configuration with credentials redacted",
    )
    return parser


def main(argv: Sequence[str] | None = None) -> int:
    args = build_parser().parse_args(argv)
    path = args.config.expanduser().resolve()
    try:
        raw = load_config(path)
        for assignment in args.assignments:
            _set_dotted_path(raw, assignment)
        if args.clear_date_ranges:
            _mapping(raw, "dates")["date_ranges"] = []
        validate_config(raw)

        if args.assignments or args.clear_date_ranges:
            _atomic_write(path, raw)
            print(f"Updated and validated: {path}")
        else:
            print(f"Valid configuration: {path}")
        if args.show:
            print(yaml.safe_dump(_redacted(raw), allow_unicode=True, sort_keys=False))
    except (OSError, TypeError, ValueError, yaml.YAMLError) as exc:
        print(f"Configuration error: {exc}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
