import importlib.util
import json
import stat
import sys
import types
import unittest
from datetime import date
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock


def _install_dependency_stubs() -> None:
    if importlib.util.find_spec("yaml") is None:
        yaml_module = types.ModuleType("yaml")

        class YAMLError(Exception):
            pass

        def safe_load(stream):
            text = stream.read() if hasattr(stream, "read") else stream
            return json.loads(text)

        def safe_dump(data, stream, **_):
            json.dump(data, stream, ensure_ascii=False, indent=2)
            stream.write("\n")

        yaml_module.YAMLError = YAMLError
        yaml_module.safe_load = safe_load
        yaml_module.safe_dump = safe_dump
        sys.modules["yaml"] = yaml_module

    if importlib.util.find_spec("selenium") is not None:
        return

    selenium_module = types.ModuleType("selenium")
    webdriver_module = types.ModuleType("selenium.webdriver")
    common_module = types.ModuleType("selenium.common")
    exceptions_module = types.ModuleType("selenium.common.exceptions")
    edge_module = types.ModuleType("selenium.webdriver.edge")
    edge_service_module = types.ModuleType("selenium.webdriver.edge.service")
    common_by_module = types.ModuleType("selenium.webdriver.common.by")
    remote_module = types.ModuleType("selenium.webdriver.remote")
    webelement_module = types.ModuleType("selenium.webdriver.remote.webelement")
    support_module = types.ModuleType("selenium.webdriver.support")
    expected_conditions_module = types.ModuleType(
        "selenium.webdriver.support.expected_conditions"
    )
    support_ui_module = types.ModuleType("selenium.webdriver.support.ui")

    for exception_name in (
        "ElementClickInterceptedException",
        "InvalidSessionIdException",
        "NoSuchElementException",
        "NoSuchFrameException",
        "StaleElementReferenceException",
        "TimeoutException",
        "WebDriverException",
    ):
        setattr(exceptions_module, exception_name, type(exception_name, (Exception,), {}))

    class By:
        ID = "id"
        XPATH = "xpath"
        LINK_TEXT = "link text"
        CLASS_NAME = "class name"
        NAME = "name"
        TAG_NAME = "tag name"

    class Placeholder:
        pass

    common_by_module.By = By
    edge_service_module.Service = Placeholder
    webelement_module.WebElement = Placeholder
    support_ui_module.Select = Placeholder
    support_ui_module.WebDriverWait = Placeholder
    webdriver_module.Remote = Placeholder
    webdriver_module.EdgeOptions = Placeholder
    webdriver_module.Edge = Placeholder
    selenium_module.webdriver = webdriver_module
    support_module.expected_conditions = expected_conditions_module

    sys.modules.update(
        {
            "selenium": selenium_module,
            "selenium.webdriver": webdriver_module,
            "selenium.common": common_module,
            "selenium.common.exceptions": exceptions_module,
            "selenium.webdriver.edge": edge_module,
            "selenium.webdriver.edge.service": edge_service_module,
            "selenium.webdriver.common.by": common_by_module,
            "selenium.webdriver.remote": remote_module,
            "selenium.webdriver.remote.webelement": webelement_module,
            "selenium.webdriver.support": support_module,
            "selenium.webdriver.support.expected_conditions": expected_conditions_module,
            "selenium.webdriver.support.ui": support_ui_module,
        }
    )


_install_dependency_stubs()

import yaml  # noqa: E402

from gisaid_flu_download.downloader import (  # noqa: E402
    DownloadConfig,
    GisaidDownloadError,
    GisaidEpiFluDownloader,
    _write_date_ranges,
)


class DownloaderRecoveryTests(unittest.TestCase):
    def _config(self, download_root: Path, **overrides) -> DownloadConfig:
        values = {
            "username": "user",
            "password": "password",
            "download_root": download_root,
            "collection_date": ("2024-01-01", "2024-01-31"),
            "download_dna": True,
            "download_protein": True,
            "download_metadata": True,
            "retry_delay_sec": 0,
        }
        values.update(overrides)
        return DownloadConfig(**values)

    def test_writes_successful_date_ranges_atomically(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            config_path = Path(temporary_directory) / "job.yaml"
            original = {
                "credentials": {"username": "user", "password": "secret"},
                "dates": {"collection_date": ["2024-01-01", "2024-01-31"]},
            }
            config_path.write_text(json.dumps(original), encoding="utf-8")
            config_path.chmod(0o600)

            ranges = [("2024-01-01", "2024-01-15"), ("2024-01-16", "2024-01-31")]
            _write_date_ranges(config_path, ranges)

            updated = yaml.safe_load(config_path.read_text(encoding="utf-8"))
            self.assertEqual(updated["dates"]["date_ranges"], [list(r) for r in ranges])
            self.assertEqual(updated["credentials"], original["credentials"])
            self.assertEqual(stat.S_IMODE(config_path.stat().st_mode), 0o600)
            self.assertEqual(list(config_path.parent.glob("*.tmp")), [])

    def test_skips_existing_outputs_and_retries_empty_file(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            root = Path(temporary_directory)
            downloader = object.__new__(GisaidEpiFluDownloader)
            downloader.cfg = self._config(root)
            downloader.meta_dir = root / "meta"
            downloader.dna_dir = root / "DNA"
            downloader.protein_dir = root / "protein"
            for output_dir in (
                downloader.meta_dir,
                downloader.dna_dir,
                downloader.protein_dir,
            ):
                output_dir.mkdir()

            tag = "2024-01-01-2024-01-31"
            (downloader.meta_dir / f"{tag}.xls").write_bytes(b"metadata")
            empty_dna = downloader.dna_dir / f"{tag}.fasta"
            empty_dna.touch()

            pending = downloader._pending_downloads("2024-01-01", "2024-01-31")

            self.assertEqual(pending, (False, True, True))
            self.assertFalse(empty_dna.exists())

    def test_retries_automatic_date_splitting(self) -> None:
        downloader = object.__new__(GisaidEpiFluDownloader)
        downloader.cfg = self._config(Path("/tmp/download"), step_retries=2)
        expected = [("2024-01-01", "2024-01-31")]
        downloader._auto_split_ranges = Mock(
            side_effect=[GisaidDownloadError("temporary failure"), expected]
        )
        downloader._count_viruses_cached = Mock()

        self.assertEqual(downloader._auto_split_ranges_with_retries(), expected)
        self.assertEqual(downloader._auto_split_ranges.call_count, 2)
        downloader._count_viruses_cached.cache_clear.assert_called_once_with()

    def test_rejects_unsplittable_single_day(self) -> None:
        downloader = object.__new__(GisaidEpiFluDownloader)
        downloader._count_viruses_cached = Mock(return_value=20_001)

        with self.assertRaisesRegex(GisaidDownloadError, "cannot split further"):
            downloader._split_ranges_by_max_strains(
                "2024-01-01", "2024-01-01", 20_000
            )

    def test_splits_only_on_whole_day_boundaries(self) -> None:
        downloader = object.__new__(GisaidEpiFluDownloader)

        def count_days(start: str, end: str) -> int:
            return (date.fromisoformat(end) - date.fromisoformat(start)).days + 1

        downloader._count_viruses_cached = Mock(side_effect=count_days)

        self.assertEqual(
            downloader._split_ranges_by_max_strains(
                "2024-01-01", "2024-01-04", 1
            ),
            [
                ("2024-01-01", "2024-01-01"),
                ("2024-01-02", "2024-01-02"),
                ("2024-01-03", "2024-01-03"),
                ("2024-01-04", "2024-01-04"),
            ],
        )

    def test_rejects_overlapping_configured_ranges(self) -> None:
        config = self._config(
            Path("/tmp/download"),
            date_ranges=(
                ("2024-01-01", "2024-01-15"),
                ("2024-01-15", "2024-01-31"),
            ),
        )

        with self.assertRaisesRegex(ValueError, "must not overlap"):
            config.validate()


if __name__ == "__main__":
    unittest.main()
