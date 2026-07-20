# Repository Guidelines

## Project Structure & Module Organization

Application code lives in `src/gisaid_flu_download/`. `downloader.py` drives the Selenium/Edge EpiFlu workflow, `merger.py` combines downloaded batches, and `pipeline.py` runs both stages sequentially. Keep the explicit output names (`meta/`, `DNA/`, and `protein/`) consistent in both stages; do not add a separate settings module for three directory literals. `configs/example.yaml` is the sanitized template, while ignored runtime configurations belong in `configs/local/`. Tests live in `tests/`. Packaging, dependencies, and console commands are declared in `pyproject.toml`.

## Setup, Run, and Development Commands

Use the Conda environment and Microsoft Edge:

```bash
conda create -n gisaid_flu_download python=3.10 pip -y
conda activate gisaid_flu_download
python -m pip install -e '.[dev]'
gisaid-run configs/local/H1N1.yaml
```

The editable install provides `gisaid-run` for the full workflow plus `gisaid-download` and `gisaid-merge` for individual stages. One `gisaid-run` invocation accepts exactly one YAML; invoke it again for another subtype. The pipeline derives merge types and locations from that YAML. Live downloads require an authorized GISAID account and may require a visible browser for manual validation.

## Coding Style & Naming Conventions

Use four-space indentation, UTF-8, and PEP 8-compatible formatting. Follow `snake_case` for functions and variables, `PascalCase` for classes and exceptions, and `UPPER_CASE` for constants. Group standard-library, third-party, and local imports. Preserve type hints, `pathlib.Path`, dataclasses, logging, and focused docstrings. Keep Selenium's internal `"dna"` values lowercase, but filesystem paths must use the uppercase `DNA` directory name.

## Testing Guidelines

Tests are standard-library `unittest` cases named `tests/test_*.py`; `pytest` also collects them. Run:

```bash
python -m pytest -q
python -m unittest discover -s tests -v
python -m compileall -q src tests
```

Add unit coverage for path contracts, date/config validation, file merging, pipeline ordering, and CLI parsing. Mock Selenium, subprocesses, and waits; do not make automated tests depend on GISAID availability. There is currently no coverage threshold. Document any manual Edge test and the sanitized config shape used.

## Security & Configuration

Never commit GISAID credentials, downloaded sequences, metadata, logs, or browser partial downloads. Copy `configs/example.yaml` into the ignored `configs/local/` directory and verify `runtime.download_root` before execution. Only placeholders belong in tracked examples.

## Commit & Pull Request Guidelines

The history is too small to establish a convention. Use short, imperative, scope-specific subjects, for example `Unify DNA output paths`. Keep unrelated changes separate. Pull requests should explain behavior and rationale, identify configuration or output-layout impacts, link relevant issues, and list unit, syntax, and manual browser checks performed.
