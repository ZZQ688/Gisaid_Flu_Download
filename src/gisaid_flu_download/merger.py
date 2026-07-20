"""Merge GISAID download batches into consolidated files."""

from __future__ import annotations

import argparse
from pathlib import Path
from typing import Sequence, Union


def merge_fasta_files(
    fasta_files: Sequence[Path], output_path: Path, data_type: str
) -> None:
    """Stream FASTA files into one output without joining adjacent records."""
    print(f"   共找到 {len(fasta_files)} 个 {data_type} 文件，正在合并...")

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
                print(f"   [报错] 处理文件 {fasta_path} 时出错: {exc}")


def _merge_metadata_files(meta_files: Sequence[Path], output_path: Path) -> None:
    try:
        import pandas as pd
    except ImportError as exc:
        raise RuntimeError(
            "合并 Metadata 需要 pandas、xlrd 和 openpyxl，请先安装项目依赖。"
        ) from exc

    frames = []
    for meta_path in meta_files:
        try:
            frames.append(pd.read_excel(meta_path))
        except Exception as exc:  # pandas exposes several optional-engine errors
            print(f"   [报错] 读取文件 {meta_path} 时出错: {exc}")

    if frames:
        pd.concat(frames, ignore_index=True).to_excel(output_path, index=False)
        print(f"   [成功] Meta 合并完成，已保存至: {output_path}")


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

    print(f"\n================ 开始处理 [{name}] ================")
    if not folder_path.is_dir():
        print(f"错误: 目录 {folder_path} 不存在，已跳过。")
        return

    if merge_meta:
        print("-> 正在合并 Meta 数据...")
        meta_files = sorted((folder_path / "meta").glob("*.xls"))
        if meta_files:
            _merge_metadata_files(meta_files, base_path / f"{name}_meta.xlsx")
        else:
            print("   [提示] 未找到任何 Meta 文件。")

    if merge_dna:
        print("-> 正在合并 DNA 数据...")
        dna_files = sorted((folder_path / "DNA").glob("*.fasta"))
        if dna_files:
            output_path = base_path / f"{name}_DNA_merged.fasta"
            merge_fasta_files(dna_files, output_path, "DNA")
            print(f"   [成功] DNA 合并完成，已保存至: {output_path}")
        else:
            print("   [提示] 未找到任何 DNA FASTA 文件。")

    if merge_protein:
        print("-> 正在合并 Protein 数据...")
        protein_files = sorted((folder_path / "protein").glob("*.fasta"))
        if protein_files:
            output_path = base_path / f"{name}_protein_merged.fasta"
            merge_fasta_files(protein_files, output_path, "protein")
            print(f"   [成功] Protein 合并完成，已保存至: {output_path}")
        else:
            print("   [提示] 未找到任何 Protein FASTA 文件。")

    print(f"================ [{name}] 处理完毕 ================\n")


def build_parser() -> argparse.ArgumentParser:
    parser = argparse.ArgumentParser(description="Merge GISAID download batches")
    parser.add_argument("base_directory", type=Path, help="包含毒株目录的输出根目录")
    parser.add_argument("names", nargs="+", help="要合并的毒株目录名")
    parser.add_argument(
        "--types",
        nargs="+",
        choices=("meta", "dna", "protein"),
        default=("meta", "dna", "protein"),
        help="要合并的数据类型（默认全部）",
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
