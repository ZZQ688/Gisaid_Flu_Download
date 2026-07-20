# Recovery Rules

Read this reference after any failed or interrupted download.

## First response

1. Stop concurrent runs that use the same config or output root.
2. Read the final relevant entries in `<download_root>/gisaid_run.log`; do not expose credentials.
3. Preserve non-empty files in `meta/`, `DNA/`, and `protein/`.
4. Fix the cause, then rerun the exact same YAML. Completed range/type files are skipped automatically.

## Failure classes

| Symptom | Action |
| --- | --- |
| Invalid YAML, missing key, invalid date | Correct the YAML with `configure_yaml.py`; do not retry unchanged input. |
| Login selector timeout | Read `browser-preflight.md` and inspect the returned page once without entering credentials. Distinguish a security challenge from network failure or page changes. |
| Cloudflare, CAPTCHA, or agreement failure | Require an interactive display, set `headless: false`, and enable manual validation. Do not repeatedly submit credentials or attempt to bypass the check. |
| Manual validation configured in a displayless session | Stop before rerunning. Resume from a desktop with an interactive `DISPLAY` or `WAYLAND_DISPLAY`; unattended Xvfb does not let the user complete the check. |
| Edge missing or driver startup failure | Do not substitute another browser silently. Obtain approval before installation, then verify one driver start/quit cycle and the reported Edge version. |
| Selenium timeout, stale element, intercepted click | Allow built-in retries to finish. If the process exits, rerun the same YAML. |
| Automatic date split fails | Let `runtime.step_retries` recalculate the full split. The YAML remains unchanged until one complete split succeeds. Rerun after transient browser failures. |
| One day exceeds `max_strains_per_range` | Increase the limit only if GISAID permits it, narrow filters, or download that day through an approved manual strategy. Blind retries cannot split a single day further. |
| Download timeout or interrupted process | Confirm the process stopped, then rerun the same YAML. Existing non-empty final files are skipped; empty final files are removed and fetched again. |
| Disk full or permission denied | Restore space or permissions without deleting completed outputs, then rerun. |
| Merge fails after downloads | Run `gisaid-merge` again; do not redownload unless an expected range file is missing or empty. |

## Date-range recovery

- When `dates.date_ranges` is empty, use `collection_date` as the source interval.
- Treat a populated `date_ranges` list as a resumable checkpoint.
- To force a full recalculation after changing filters, collection dates, or the strain limit, clear `date_ranges` before running.
- Never accept or write a partial split. The downloader writes ranges atomically only after the full calculation succeeds.

## Login challenge recovery

- `require_manual_validation` pauses at the download dialog, not before the login fields. Do not assume this option alone resolves a pre-login Cloudflare challenge.
- Preserve `date_ranges` when changing only `headless` or `require_manual_validation`.
- If login fails before automatic splitting, report zero ranges written and zero downloaded files after verifying the output directories.
- If a visible user cannot complete the challenge within the current login wait, report the limitation as an application-code blocker instead of repeatedly restarting the job.

## Completion check

Compare every configured range with every enabled type. Require one non-empty expected file for each pair. Then run the merge stage and report any missing range/type explicitly.
