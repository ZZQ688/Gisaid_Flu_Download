# Browser Preflight and Interactive Checks

Read this reference before every browser launch and whenever login selectors time out.

## Preflight

1. Run from the `gisaid-flu-download` project root in the Python 3.10 Conda environment.
2. Run `scripts/preflight.py <config.yaml>`. It reports only non-secret job state.
3. Require Microsoft Edge. Do not silently substitute Chrome for an Edge-only downloader.
4. If Edge is missing, obtain user approval before downloading or installing it. Never request, accept, or enter a sudo password. A user-approved local extraction is acceptable only after verifying the package name, architecture, maintainer, and executable version.
5. A Selenium smoke test may download a matching driver and access the network. Obtain the required approval, start and quit the driver once, and report `browserName` and `browserVersion` without visiting GISAID.
6. Do not start two jobs that share a config or output root.

## Display modes

| Configuration | Required environment | Action |
| --- | --- | --- |
| `headless: true`, manual validation false | No display required | Suitable only when the login page opens without a human challenge. |
| `headless: false`, manual validation true | Interactive `DISPLAY` or `WAYLAND_DISPLAY` | Keep the browser visible and the user ready to act. |
| `headless: true`, manual validation true | Invalid combination | Change to a visible browser before launching. |
| `headless: false`, no display | Blocked | Stop before launching; do not use an unattended virtual display for a human challenge. |

`options.require_manual_validation` currently pauses after opening a download dialog. It does not extend the hard-coded login-field wait. A Cloudflare check before login must therefore be completed promptly in the visible browser. If the available login wait is inadequate, report a code-level blocker; do not patch application behavior unless the user asks for that change.

## Login-page diagnosis

When `_SEL_LOGIN_USER` or another login selector times out:

1. Let the current process finish its built-in retries, then stop rerunning the unchanged job.
2. Open the login URL once without entering credentials. Capture only the current URL, title, a short visible-text excerpt, and optionally a temporary screenshot.
3. Classify pages containing `Cloudflare`, `security verification`, `正在进行安全验证`, or `Just a moment` as human-verification failures.
4. Do not add automation-evasion flags, solve CAPTCHA programmatically, reuse challenge tokens, or otherwise bypass access controls.
5. Set `runtime.headless=false` and `options.require_manual_validation=true` with `configure_yaml.py`. Do not clear `dates.date_ranges` because browser mode does not affect result counts.
6. Resume the exact same YAML only from an interactive desktop session. Preserve all non-empty files and the existing range checkpoint.

If the returned page is an HTTP, DNS, proxy, certificate, or maintenance error rather than a challenge, fix that classified cause and rerun once. Do not treat it as a credential failure.

## Attached execution

Use live output in non-interactive shells:

```bash
conda run --no-capture-output -n gisaid_flu_download \
  gisaid-run <config.yaml>
```

The log may not exist during initial driver startup. Poll without treating an absent log as failure, keep the process session attached, and wait for its final exit code before deciding whether merge ran.
