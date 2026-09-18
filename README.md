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

You need Python, Microsoft Edge, and a GISAID account with EpiFlu access. Conda is one supported way to create the environment:

```bash
conda create -n gisaid_flu_download python=3.10 pip -y
conda activate gisaid_flu_download
python -m pip install -e '.[dev]'
```

Conda is optional. To use the Python standard-library `venv` instead, install Python 3.10 or later and run:

Linux/macOS:

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

Windows PowerShell:

```powershell
py -3 -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
python -m pip install -e '.[dev]'
```

### Microsoft Edge WebDriver

Install [Microsoft Edge](https://www.microsoft.com/edge) on the machine running this project. The browser and Microsoft Edge WebDriver (`msedgedriver`, or `msedgedriver.exe` on Windows) are separate components.

#### Automatic driver setup

[Selenium Manager](https://www.selenium.dev/documentation/selenium_manager/) is included in Selenium 4.6 and later. The project declares `selenium>=4.6`, so after the installation command above you can run `gisaid-run` directly. When no driver is supplied, Selenium Manager can discover, download, and cache a compatible EdgeDriver. Initial driver resolution and download require access to Microsoft's download services; a proxy or firewall can prevent this. No driver path is needed in the YAML for automatic setup.

#### Manual driver setup

If automatic setup fails or the machine cannot reach the driver download service:

1. Open `edge://settings/help` in Edge and note the four-part browser version.
2. Download [Microsoft Edge WebDriver](https://developer.microsoft.com/microsoft-edge/tools/webdriver/) for your operating system and architecture. The **first three parts** of the driver and browser version numbers must match, as specified in [Microsoft's instructions](https://learn.microsoft.com/en-us/microsoft-edge/webdriver/). For example, `128.0.2739.79` and `128.0.2739.84` are compatible. Choose a matching build, which may differ from the latest driver offered.
3. Extract the archive. On Linux/macOS, ensure the executable has execute permission (`chmod +x /absolute/path/to/msedgedriver`). Add the **directory containing the executable** to `PATH`, then reopen the terminal or IDE used to launch the project.
4. In that same terminal, run `msedgedriver --version` and compare it with Edge's version. Use `command -v msedgedriver` on Linux/macOS or `where.exe msedgedriver` on Windows to check which executable is found. Update stale drivers after Edge updates.

Alternatively, with Python Selenium 4.26 or later, set `SE_EDGEDRIVER` to the **full executable path**. This bypasses Selenium Manager and does not require adding the driver to `PATH`:

Linux/macOS:

```bash
export SE_EDGEDRIVER="/absolute/path/to/msedgedriver"
gisaid-run configs/local/H1N1.yaml
```

Windows PowerShell:

```powershell
$env:SE_EDGEDRIVER = "C:\tools\edgedriver\msedgedriver.exe"
gisaid-run configs/local/H1N1.yaml
```

Replace the example path with your extracted executable's location and launch the project from the same terminal. The project currently has no YAML field for the driver path; use `PATH` or `SE_EDGEDRIVER`. An explicit `SE_EDGEDRIVER` takes precedence over automatic discovery, so update it when moving or replacing the driver.

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
