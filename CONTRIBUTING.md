# Contributing

Contribute a workload, oracle, adapter, reproducibility improvement or evidence-backed comparison. [CITATION.cff](CITATION.cff) records research attribution. This repository is independent of the official FINOS Fluxnova repositories.

Use Python 3.12+ from the checkout root:

```bash
python -m unittest discover -s tests -v
python -m benchlib.cli demo
```

Set `BENCH_TEST_K6_BINARY` to a verified k6 executable for optional real-k6 tests. Record skips honestly. Add meaningful tests for correctness/control changes; check links and render artifacts for documentation changes.

Follow the [protocol](docs/benchmark-protocol.md). Include original synthetic inputs, expected outcomes, negative cases and an independent oracle. Performance submissions name source, environment, controls, every attempted run and limits. Keep failures and regressions visible.

Use focused pull requests describing the problem, resulting behavior and validation. Exclude credentials, Terraform state, customer data, raw model prompts and unreviewed internal presentations. Use ignored local configuration and example placeholders. See [SECURITY.md](SECURITY.md).

## License and upstream

The code uses [Apache 2.0](LICENSE). Contributions must be compatible with that license and submitted by someone with the necessary rights. Logos, trademarks and third-party assets retain their own terms. Public visibility remains gated on the [pattern and sanitization checks](docs/public-release.md).

FINOS contributions follow the [official Fluxnova policy](https://github.com/finos/fluxnova-bpm-platform/blob/main/CONTRIBUTING.md), including its CLA and review requirements. The [upstream proposal](docs/upstream-proposal.md) is preparation, not an accepted transfer or PR.
