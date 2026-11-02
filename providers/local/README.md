# Local hands-on lab

From the checkout root, with Python 3.12+ and Java 21:

```bash
python -m benchlib.cli lab --download-runtime
```

This downloads the official Fluxnova 3.0.1 ZIP if absent, checks its pinned SHA-256 and extracted JARs, starts an isolated engine on `127.0.0.1:18088`, and opens **http://127.0.0.1:8765**. No Python dependencies or cloud credentials are needed. If Java is not on PATH, add `--java PATH_TO_JAVA`; cached `.tools/java21` installations are also discovered.

See [the interactive walkthrough](../../docs/interactive-lab.md). Starter BPMN files in [workflows/](workflows/) are separate from frozen research workloads.

`--no-browser` suppresses automatic browser opening. `--output runs/my-lab` selects a **fresh** output folder. Existing ports and engines are never silently reused. Close with **Close lab** or Ctrl+C. The app removes its own deployments, stops its owned Java process, and retains BPMN/run JSON in `runs/`. Database files and supplied variables remain local data, not public evidence.
