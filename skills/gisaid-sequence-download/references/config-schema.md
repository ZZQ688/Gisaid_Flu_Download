# Configuration Contract

Read this reference before creating or changing a download YAML.

## Required sections

- `credentials.username`, `credentials.password`: Keep real values only in an ignored local YAML. Never print, quote, commit, or pass them as command arguments.
- `runtime.download_root`: Use one dedicated absolute or verified relative output directory per job.
- `filters.virus_type`: Set `A` or `B`. For A, set `h_types` and `n_types`; for B, set `b_lineages`.
- `filters.locations`: List of `Location` visible-text values to multi-select; leave empty to skip the filter.
- `filters.tpe_submissions`: Boolean (`true`/`false`); check the "TPE submissions" filter. Default `false`.
- `dates.collection_date`: Set `[start, end]` in `YYYY-MM-DD` format.
- `dates.date_ranges`: Leave empty to calculate ranges automatically. A successful calculation atomically replaces this list in the same YAML.
- `dates.max_strains_per_range`: Keep below the current GISAID batch limit; the project default is 20,000 and the example uses 18,000.
- `options.download_metadata`, `download_dna`, `download_protein`: Enable at least one.

## Reliability controls

- `runtime.page_timeout_sec`: Browser page timeout; default `40`.
- `runtime.download_timeout_sec`: Maximum wait for one browser download; default `1800`.
- `runtime.poll_interval_sec`: Download polling interval; default `5`.
- `runtime.step_retries`: Retry count for Selenium steps and automatic date splitting; default `3`.
- `runtime.retry_delay_sec`: Delay between retries; default `5`.
- `runtime.headless`: Set `false` when login verification or CAPTCHA may appear. A non-headless run requires an interactive display.
- `options.require_manual_validation`: Set `true` when a visible manual verification step is required during the download dialog. This option does not extend the login-field wait.
- `options.replace_spaces_with_underscores`: Check "Replace spaces with underscores in FASTA header" in the download dialog. Default `true`.
- `options.trim_fasta_values`: Check "Remove spaces before and after values in FASTA header" in the download dialog. Default `true`.

## Safe edits

Use `scripts/configure_yaml.py` for non-secret changes. Quote list values as YAML or JSON:

```bash
python <skill-dir>/scripts/configure_yaml.py configs/local/H1N1.yaml \
  --set 'dates.collection_date=["2024-01-01", "2024-12-31"]' \
  --set 'filters.h_types=["1"]' \
  --set 'options.download_dna=true' \
  --clear-date-ranges
```

Run the script with `--show` only when a redacted configuration view is useful. The script rejects credential changes on the command line.

## Output and resume contract

For range `START-END`, enabled outputs are:

- `meta/START-END.xls`
- `DNA/START-END.fasta`
- `protein/START-END.fasta`

A non-empty expected file is complete and is skipped on rerun. A missing or empty expected file is downloaded again. Do not rename range files until the job and merge finish, and do not run two processes against the same output root.
