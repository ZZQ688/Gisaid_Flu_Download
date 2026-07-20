---
name: gisaid-sequence-download
description: Configure, preflight, run, monitor, resume, and troubleshoot authorized GISAID EpiFlu metadata, DNA, and protein sequence downloads driven by YAML. Use for changing GISAID job YAML files, validating Microsoft Edge and interactive-browser readiness, handling Cloudflare or manual verification without bypassing access controls, automatically splitting collection dates, recovering Selenium failures, resuming interrupted jobs while skipping completed files, and merging verified batch outputs.
---

# GISAID Sequence Download

Operate the local `gisaid-flu-download` project as a resumable workflow. Protect credentials and downloaded GISAID data throughout the task.

## Workflow

1. Initialize the Conda shell and activate the project environment before every configuration or download command:

   ```bash
   source "$(conda info --base)/etc/profile.d/conda.sh"
   conda activate gisaid_flu_download
   python --version  # must be Python 3.10.x
   ```

   If the environment is missing, stop and create it with `conda create -n gisaid_flu_download python=3.10 pip -y`, then install the project with `python -m pip install -e '.[dev]'`. Do not silently fall back to the system Python. In non-interactive shells, use `conda run -n gisaid_flu_download ...` for each command.
2. Locate the project by finding `pyproject.toml` with project name `gisaid-flu-download`, or confirm that `gisaid-run` is installed in the activated environment.
3. Select exactly one ignored local YAML for the job. Confirm it is ignored with `git check-ignore`; never display or commit credentials.
4. Read [references/config-schema.md](references/config-schema.md) before changing configuration. Use `scripts/configure_yaml.py` for non-secret edits and validation.
5. Clear `dates.date_ranges` whenever collection dates, virus filters, segments, or `max_strains_per_range` change. Leave it populated when resuming an unchanged job.
6. Read [references/browser-preflight.md](references/browser-preflight.md), then run `scripts/preflight.py` from the project root. Do not launch until Edge, display requirements, `runtime.download_root`, free disk space, output types, and the ignored-config check pass. Avoid concurrent processes sharing the same output root.
7. Run `gisaid-run <config>` for download plus merge, or `gisaid-download <config>` when only the download stage is requested. In non-interactive shells, use `conda run --no-capture-output` so logs remain live. Keep the process attached and monitor `gisaid_run.log` until it exits.
8. When automatic splitting succeeds, let the downloader atomically write the complete ranges to the same YAML, then validate `dates.date_ranges`. Treat the YAML as the checkpoint; never reconstruct or manually copy ranges from log text.
9. After interruption or error, read [references/recovery.md](references/recovery.md), fix the classified cause, and rerun the same YAML. Rely on non-empty range/type outputs as checkpoints; do not delete completed files.
10. Verify one non-empty file per configured range and enabled type before declaring completion. Confirm merged outputs only after the download command succeeds.

## Configuration Commands

Validate without revealing credentials:

```bash
python <skill-dir>/scripts/configure_yaml.py <config.yaml>
```

Apply typed, non-secret updates atomically:

```bash
python <skill-dir>/scripts/configure_yaml.py <config.yaml> \
  --set 'dates.collection_date=["2024-01-01", "2024-12-31"]' \
  --set 'options.download_protein=true' \
  --clear-date-ranges
```

Use `--show` only for a redacted view. Edit credentials privately in the ignored local YAML; never place secrets in shell arguments or assistant output.

Run the deterministic browser and job preflight from the project root:

```bash
python <skill-dir>/scripts/preflight.py <config.yaml>
```

Treat a nonzero preflight exit as a blocker. Fix the reported condition before opening GISAID.

## Execution Rules

- Use a visible browser when validation may require human action. Require a real interactive display; a displayless or unattended virtual display cannot satisfy a human check. Do not attempt to bypass CAPTCHA, Cloudflare, or GISAID access controls.
- Do not silently substitute Chrome for Edge or install browser software without user approval.
- Let built-in step and split retries finish before restarting a job.
- After a login-selector timeout, diagnose the returned page once without submitting credentials. If it is a security challenge, stop automated retries and follow the browser recovery reference.
- Treat a nonzero exit as incomplete. Do not merge after a failed downloader process.
- Resume by rerunning the same config. Skip non-empty expected files, redownload missing files, and replace empty final files.
- Recalculate date ranges only after inputs affecting result counts change.
- Keep GISAID sequences, metadata, logs, and credentials out of Git and external messages.

## Reporting

Report the config path, output root, selected data types, range count, skipped files, newly downloaded files, retry outcome, and final merge status. For blocked runs, also report the exact preflight or browser checkpoint reached and whether any output or YAML checkpoint was written. Redact credentials and avoid reproducing sequence contents.
