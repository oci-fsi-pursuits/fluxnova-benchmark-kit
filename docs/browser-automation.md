# Browser automation for the workload demos

The completed [W02-M2 saved report](results/w02-mixed/report.html) is included in the checkout. Open it without a cloud account using `python scripts/open_report.py --path docs/results/w02-mixed/report.html`.

All ten arms passed independent audit. Browser verification checked six outcome rows, fifteen paired rows and fifty dependency-profile rows, including the refusal-timeliness regression. Keyboard navigation, evidence links, desktop/laptop layout and visual review passed. Opening the page makes no external network requests and starts no workload.

Use Playwright with Chrome to repeat the browser review. It can select a case, switch comparison arms, check displayed outcomes against the original evidence, follow the evidence link, take screenshots and record a walkthrough. This path does not depend on the desktop automation connection.

The current runners open a fresh browser context. They read saved local bundles, block HTTP/HTTPS requests and execute no new payments or model calls. The existing live Fluxnova Tasklist path is a separate demonstration: it needs authenticated engine access, temporary synthetic cases and an explicit cleanup/reconciliation step. A saved-viewer pass is not a new live-Tasklist pass.

## Set up once

From the repository root, use a Python environment with Playwright installed and an installed Chrome browser. The completed Windows rehearsal used Python 3.13 and Playwright 1.64.0. A project-local installation keeps it separate from the benchmark runtime:

```powershell
& "$env:USERPROFILE\oci\Scripts\python.exe" -m pip install --target dist/browser-review-tools playwright==1.64.0
```

For recording, Playwright also needs its matching video encoder. With the same Python environment:

```powershell
$env:PYTHONPATH = (Resolve-Path dist/browser-review-tools).Path
$env:PLAYWRIGHT_BROWSERS_PATH = Join-Path (Get-Location) 'dist/playwright-browser-deps'
& "$env:USERPROFILE\oci\Scripts\python.exe" -m playwright install ffmpeg
```

The current machine already has these dependencies. Other platforms can use their own Python executable. The scripts request the `chrome` channel; they do not attach to your personal Chrome profile.

## Run a visible walkthrough check

```powershell
& "$env:USERPROFILE\oci\Scripts\python.exe" scripts/rehearse_w02_demo.py `
  --bundle dist/w02-demo-20261027-004 `
  --output dist/w02-browser-review-new `
  --playwright-path dist/browser-review-tools `
  --browser-cache dist/playwright-browser-deps `
  --headed --record
```

Use a fresh output directory for each attempt. Omit `--headed` to run without a visible browser. Omit `--record` for the shorter checks only. The sealed input bundle is checked before and after the run and remains unchanged. A recording displays previously saved executions; it does not rerun the benchmark.

W03 uses the same approach:

```powershell
& "$env:USERPROFILE\oci\Scripts\python.exe" scripts/rehearse_w03_demo.py `
  --bundle dist/w03-demo-20261027-001 `
  --output dist/w03-browser-review-new `
  --playwright-path dist/browser-review-tools --headed
```

W01's runner checks all 60 matched pair/burst selections and the plotted positions of all 2,400 measured payments. Its bundle builder requires a complete independently audited campaign; a partial run cannot become a results viewer.

```powershell
& "$env:USERPROFILE\oci\Scripts\python.exe" scripts/rehearse_w01_demo.py `
  --bundle dist/w01-demo-20261027-001 `
  --output dist/w01-browser-review-new `
  --playwright-path dist/browser-review-tools --headed
```

These dated bundle paths refer to retained local artifacts, not files committed to Git. Rebuild or extract the matching bundles first on another machine. See [W01](w01-worker-pacing.md), [W02](w02-saved-demo.md), [W03](w03-review-routing.md) and the [completion register](workload-completion.md) for evidence and current status.

## What a pass means

The offline presenter package (retained in the private research archive) adds a single launch page for the talk, handout, saved viewers and recording. Its browser check verifies all three viewer links, playback, keyboard navigation and layout. The [dependency sensitivity report](w02-dependency-sensitivity.md) has a separate runner that checks all 100 displayed result rows against original evidence.

The [mixed-outcome load report](w02-mixed-load.md) adds an audited view of settlement, refusal and safe-stop counts under continuous arrivals. Its runner checks all four run rows and twenty dependency-profile rows, the JSON evidence link and keyboard disclosure. The 28 October local report passed automation and desktop/laptop visual review, while retaining all deadline misses.

The [mixed OCI report](../validation/local-20261028-w02-cloud-view/README.md) re-runs the auditor retained with the sealed cloud evidence before building an offline viewer. Qualification and incomplete campaigns suppress the aggregate comparison. A complete campaign shows five paired settlement-goodput changes, six outcome rows and fifteen pair/outcome rows, including negative changes. Use the corresponding retained export and audit directories:

```powershell
python scripts/render_w02_mixed_cloud.py `
  --evidence dist/M2/campaign-snapshot --audit dist/M2/campaign-analysis `
  --output dist/M2/saved-view --browser-review dist/M2/browser-review `
  --playwright-path dist/browser-review-tools
```

The same command accepts a qualification export and audit. The 28 October qualification view passed all twenty profile rows, JSON navigation, keyboard controls and visual review. The complete-campaign layout was checked with explicitly labeled test fixtures; those values are not research results. Actual mixed-campaign output still requires its own audit, browser checks and screenshot review.

The runners check the viewer against its sealed evidence, real JSON-link navigation, keyboard operation, horizontal overflow at a common laptop viewport, JavaScript errors and attempted network requests. Review the captured images for readability. Check the actual presentation display separately. Browser UI checks support demo correctness; the workload auditors establish the experimental results.

The current 75-second W02 recording is at `dist/w02-browser-rehearsal-20261027-002/w02-saved-walkthrough.webm`. It includes approved sequential and parallel cases, an unauthorized case and an unknown-check case. Its receipt timelines are local functional observations; the separate OCI report supplies the p95 comparison.

References: [Playwright browser channels](https://playwright.dev/python/docs/browsers) and [video recording](https://playwright.dev/python/docs/videos).
