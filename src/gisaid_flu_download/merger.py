"""Merge GISAID download batches into consolidated files."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence, Union


def merge_fasta_files(
    fasta_files: Sequence[Path], output_path: Path, data_type: str
) -> None:
    """Stream FASTA files into one output without joining adjacent records."""
    print(f"   Found {len(fasta_files)} {data_type} file(s), merging...")

    with output_path.open("w", encoding="utf-8") as output_file:
        for fasta_path in fasta_files:
            try:
                last_line = ""
                with fasta_path.open("r", encoding="utf-8") as input_file:
                    for line in input_file:
                        output_file.write(line)
                        last_line = line

                if last_line and not last_line.endswith("\n"):
                    output_file.write("\n")
            except (OSError, UnicodeError) as exc:
                print(f"   [error] Error processing file {fasta_path}: {exc}")


def _merge_metadata_files(meta_files: Sequence[Path], output_path: Path) -> None:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "Merging Metadata requires pandas, xlrd, and openpyxl; install the "
            "project dependencies first."
        ) from exc

    frames = []
    for meta_path in meta_files:
        try:
            frames.append(pd.read_excel(meta_path))
        except Exception as exc:  # pandas exposes several optional-engine errors
            print(f"   [error] Error reading file {meta_path}: {exc}")

    if frames:
        pd.concat(frames, ignore_index=True).to_excel(output_path, index=False)
        print(f"   [success] Meta merge complete, saved to: {output_path}")


def merge_folder_data(
    base_dir: Union[str, Path],
    name: str,
    *,
    merge_meta: bool = True,
    merge_dna: bool = True,
    merge_protein: bool = True,
) -> None:
    """Merge selected artifact types below ``<base_dir>/<name>``."""
    base_path = Path(base_dir).expanduser()
    folder_path = base_path / name

    print(f"\n================ Processing [{name}] ================")
    if not folder_path.is_dir():
        print(f"Error: directory {folder_path} does not exist; skipped.")
        return

    if merge_meta:
        print("-> Merging Meta data...")
        meta_files = sorted((folder_path / "meta").glob("*.xls"))
        if meta_files:
            _merge_metadata_files(meta_files, base_path / f"{name}_meta.xlsx")
        else:
            print("   [info] No Meta files found.")

    if merge_dna:
        print("-> Merging DNA data...")
        dna_files = sorted((folder_path / "DNA").glob("*.fasta"))
        if dna_files:
            output_path = base_path / f"{name}_DNA_merged.fasta"
            merge_fasta_files(dna_files, output_path, "DNA")
            print(f"   [success] DNA merge complete, saved to: {output_path}")
        else:
            print("   [info] No DNA FASTA files found.")

    if merge_protein:
        print("-> Merging Protein data...")
        protein_files = sorted((folder_path / "protein").glob("*.fasta"))
        if protein_files:
            output_path = base_path / f"{name}_protein_merged.fasta"
            merge_fasta_files(protein_files, output_path, "protein")
            print(f"   [success] Protein merge complete, saved to: {output_path}")
        else:
            print("   [info] No Protein FASTA files found.")

    print(f"================ [{name}] done ================\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge GISAID download batches")
    parser.add_argument("base_directory", type=Path, help="Output root directory containing the strain directories")
    parser.add_argument("names", nargs="+", help="Names of the strain directories to merge")
    parser.add_argument(
        "--types",
        nargs="+",
        choices=("meta", "dna", "protein"),
        default=("meta", "dna", "protein"),
        help="Data types to merge (default: all)",
    )
    return parser


def main() -> None:
    args = build_parser().parse_args()
    selected_types = set(args.types)

    for virus_name in args.names:
        merge_folder_data(
            args.base_directory,
            virus_name,
            merge_meta="meta" in selected_types,
            merge_dna="dna" in selected_types,
            merge_protein="protein" in selected_types,
        )


if __name__ == "__main__":
    main()
