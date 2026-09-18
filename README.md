# GISAID Flu Download

Automate GISAID EpiFlu with Selenium and Microsoft Edge: filter records by virus type, host, subtype or lineage, and sampling date, download Metadata, DNA, and Protein in batches, and merge the date batches. Using this project requires complying with GISAID's access and data use terms.

## Project structure

```text
.
├── configs/
│   ├── example.yaml          # Sanitized config template safe to commit
│   └── local/                # Local configs, ignored by Git
├── src/gisaid_flu_download/
│   ├── downloader.py         # EpiFlu download workflow
│   ├── merger.py             # Metadata/FASTA merge tooling
│   └── pipeline.py           # Download-then-merge pipeline
└── pyproject.toml            # Dependencies, packaging, and entry points
```

## Installation

You need Conda, Microsoft Edge, and a GISAID account with EpiFlu access. On first use, create the environment and install the project:

```bash
conda create -n gisaid_flu_download python=3.10 pip -y
conda activate gisaid_flu_download
python -m pip install -e '.[dev]'
```

Selenium normally manages a compatible EdgeDriver automatically, so the Edge browser is the only thing you must install separately.

### Microsoft Edge WebDriver

If Selenium Manager cannot download the driver for you (for example on an offline or restricted machine), install the matching Microsoft Edge WebDriver manually from the official page:

https://developer.microsoft.com/en-us/microsoft-edge/tools/webdriver/

Download the driver whose version matches your installed Edge. Find your Edge version at `edge://version/`, then place the `msedgedriver` executable on your `PATH` (or pass it to Selenium via `EdgeService(executable_path=...)`). The browser itself can be installed from:

https://www.microsoft.com/edge

## Configuration

Copy the sanitized template and edit only the local copy:

```bash
cp configs/example.yaml configs/local/H1N1.yaml
```

Fill in your account and confirm `runtime.download_root`. Real credentials live only in `configs/local/` and must never be committed.

Key configuration sections:

- `credentials`: GISAID username and password; never commit.
- `runtime.download_root`: Output directory for this job; relative paths are resolved against the directory where the command is launched.
- `filters`: Virus type, H/N subtypes, B lineage, host, submitting lab, location, TPE submissions, and segments.
- `dates.collection_date`: The full date range to download.
- `dates.date_ranges`: When non-empty, these ranges are used directly; when empty they are auto-split by `max_strains_per_range` and atomically written back to the current YAML on success.
- `options`: Controls Metadata, DNA, Protein, manual validation, and the FASTA header options.
- `runtime.step_retries` and `runtime.retry_delay_sec`: Retry count and delay for Selenium steps and automatic date splitting.

For the first run, or when a CAPTCHA is expected, set `headless: false` and `require_manual_validation: true`.

## One-shot download and merge

Prefer `gisaid-run`. Each invocation handles exactly one YAML: it reads the config, completes the download, and automatically merges the corresponding types according to that YAML's `options.download_*` settings.

```bash
gisaid-run configs/local/H1N1.yaml
```

The same config can be rerun safely. The program checks the enabled-type target files per date range: a non-empty file counts as complete and is skipped, while missing or empty files are downloaded again. So after an interruption, just rerun the same command to resume; do not run concurrent processes against the same output directory.

You can also run the same pipeline via `python -m gisaid_flu_download <config>`. To run other subtypes, invoke it again for each. To rerun a single stage separately, use:

```bash
gisaid-download configs/local/H1N1.yaml
gisaid-merge ./data H1N1 --types meta dna protein
```

The download directory layout is fixed:

```text
<download_root>/
├── meta/<start>-<end>.xls
├── DNA/<start>-<end>.fasta
├── protein/<start>-<end>.fasta
└── gisaid_run.log
```

Merged results are written into the input root as `<name>_meta.xlsx`, `<name>_DNA_merged.fasta`, and `<name>_protein_merged.fasta`. If an older version produced a lowercase `dna/`, rename it once to `DNA/` before merging.

## Security and troubleshooting

Do not commit account credentials, downloaded data, browser temporary files, or logs. If credentials were ever committed or shared, rotate them immediately. If the browser fails to start, check the Edge and driver versions; if the CAPTCHA cannot be completed, disable headless mode; if the merger cannot find data, check the input root, file extensions, and the exact casing of `meta/`, `DNA/`, and `protein/`.

If a download fails, simply retry it — the failure is often a transient network issue or a GISAID rate/access limit, and rerunning the same config resumes from the files already downloaded. If it keeps failing after several retries, please submit an issue describing the config shape (without credentials), the error output, and the step that failed.

If automatic date-range splitting fails repeatedly, the range likely contains too many sequences and GISAID is rate-limiting the repeated refreshes. Shorten `dates.collection_date` (or manually split the ranges in `dates.date_ranges`) into smaller intervals and retry.
