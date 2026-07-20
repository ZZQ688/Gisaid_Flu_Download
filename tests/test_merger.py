import subprocess
import sys
import types
import unittest
from pathlib import Path
from tempfile import TemporaryDirectory
from unittest.mock import Mock, patch

from gisaid_flu_download.merger import merge_folder_data
from gisaid_flu_download.pipeline import (
    PipelineJob,
    load_job,
    run_pipeline,
)


class MergerTests(unittest.TestCase):
    def test_merges_sorted_dna_files_from_uppercase_directory(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            base_path = Path(temporary_directory)
            dna_path = base_path / "H1N1" / "DNA"
            dna_path.mkdir(parents=True)
            (dna_path / "b.fasta").write_text(">b\nBBBB\n", encoding="utf-8")
            (dna_path / "a.fasta").write_text(">a\nAAAA", encoding="utf-8")

            merge_folder_data(
                base_path,
                "H1N1",
                merge_meta=False,
                merge_dna=True,
                merge_protein=False,
            )

            merged_path = base_path / "H1N1_DNA_merged.fasta"
            self.assertEqual(
                merged_path.read_text(encoding="utf-8"),
                ">a\nAAAA\n>b\nBBBB\n",
            )


class PipelineTests(unittest.TestCase):
    def test_loads_merge_options_and_download_root(self) -> None:
        with TemporaryDirectory() as temporary_directory:
            base_path = Path(temporary_directory)
            config_path = base_path / "H1N1.yaml"
            config_path.write_text("placeholder", encoding="utf-8")
            fake_yaml = types.ModuleType("yaml")
            fake_yaml.safe_load = lambda _: {
                "runtime": {"download_root": str(base_path / "data" / "H1N1")},
                "options": {
                    "download_metadata": True,
                    "download_dna": True,
                    "download_protein": False,
                },
            }

            with patch.dict(sys.modules, {"yaml": fake_yaml}):
                job = load_job(config_path)

            self.assertEqual(job.download_root, (base_path / "data" / "H1N1").resolve())
            self.assertTrue(job.merge_meta)
            self.assertTrue(job.merge_dna)
            self.assertFalse(job.merge_protein)

    def test_downloads_before_merging_configured_outputs(self) -> None:
        config_path = Path("/tmp/H1N1.yaml")
        job = PipelineJob(
            config_path=config_path,
            download_root=Path("/tmp/data/H1N1"),
            merge_meta=True,
            merge_dna=True,
            merge_protein=False,
        )
        events = []
        command_runner = Mock(side_effect=lambda *_, **__: events.append("download"))

        with patch(
            "gisaid_flu_download.pipeline.load_job", return_value=job
        ), patch(
            "gisaid_flu_download.pipeline.merge_folder_data",
            side_effect=lambda *_, **__: events.append("merge"),
        ) as merge:
            run_pipeline(config_path, run_command=command_runner)

        command_runner.assert_called_once()
        command_args, command_kwargs = command_runner.call_args
        self.assertEqual(
            command_args[0][1:],
            ["-m", "gisaid_flu_download.downloader", str(config_path)],
        )
        self.assertTrue(command_kwargs["check"])
        self.assertEqual(events, ["download", "merge"])
        merge.assert_called_once_with(
            Path("/tmp/data"),
            "H1N1",
            merge_meta=True,
            merge_dna=True,
            merge_protein=False,
        )

    def test_does_not_merge_after_download_failure(self) -> None:
        job = PipelineJob(
            config_path=Path("/tmp/H1N1.yaml"),
            download_root=Path("/tmp/data/H1N1"),
            merge_meta=True,
            merge_dna=True,
            merge_protein=True,
        )
        command_runner = Mock(
            side_effect=subprocess.CalledProcessError(1, ["gisaid-download"])
        )

        with patch(
            "gisaid_flu_download.pipeline.load_job", return_value=job
        ), patch("gisaid_flu_download.pipeline.merge_folder_data") as merge:
            with self.assertRaises(subprocess.CalledProcessError):
                run_pipeline(job.config_path, run_command=command_runner)

        merge.assert_not_called()


if __name__ == "__main__":
    unittest.main()
