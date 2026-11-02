# OSFF New York 2026 presentation

**Benchmarking Fluxnova for Financial and Agentic Payment Workflows**

Nicholas Cusato, Senior Cloud Engineer, Oracle Cloud Infrastructure, Financial Services and Insurance.

Use repeatable tests to evaluate performance changes while preserving correct, authorized outcomes. The talk connects a customer modernization question to the benchmark architecture, the full research map, three workflow examples, the findings and a reusable method.

- [Editable PowerPoint](../presentations/fluxnova-osff-ny-2026.pptx)
- [PDF handout](../presentations/fluxnova-osff-ny-2026.pdf)
- [15-minute agenda and speaker guide](talk-15-minutes.md)
- [Illustrated catalogue and complete study register](benchmark-guide.md)
- [Source and reproduction guide](getting-started.md)
- [Suggest a workload or give feedback](https://github.com/oci-fsi-pursuits/fluxnova-benchmark-kit/issues/new?template=benchmark-feedback.yml)

The deck contains 13 main slides and 14 hidden reference slides. The planned talk is 13 minutes, including a 75-second saved workflow walkthrough, with 2 minutes for transitions and questions. The PDF includes the main talk. Detailed evidence remains in the catalogue and the editable deck's appendix.

## What the presentation establishes

The results describe declared synthetic workloads and environments. Each study has its own baseline and scope. The W02 parallel-check comparison reduced observed p95 at its fixed load. Other experiments found tradeoffs, small mixed differences, correct bounded outcomes or incomplete qualification. The talk preserves those distinctions.

The E-Business Suite to Fusion story is motivation, not a migration result. Live-agent benefit, customer traffic, maximum capacity, cross-cloud superiority and complete allocated cost per correct outcome are not established by these studies. Protocol 0.2 is a community proposal, not an adopted FINOS standard.

## Reproduce the idea

Start from the repository root:

```bash
python -m benchlib.cli catalog
python -m benchlib.cli explain W02
```

These commands inspect the saved register. `python -m benchlib.cli demo` creates labeled sample reporting data; it does not execute Fluxnova. Follow the [supported run paths](benchmark-standard.md) for real functional execution, the [saved demo guide](w02-saved-demo.md) for the walkthrough, and the [OCI guide](oci.md) for isolated infrastructure. There is no turnkey provisioning switch for every cloud or on-premises environment.

## Credits

- Research, benchmark implementation, interpretation and presentation: **Nicholas Cusato**. Quote on slide 4: Nicholas Cusato, *Performance-Testing POV for FINOS Fluxnova on OCI*, version 1.1 (2026), Executive position.
- Fluxnova software: **FINOS contributors**. Workflow illustration provenance and original logo terms: [assets guide](assets/README.md).
- Conference artwork: the supplied **OSFF New York 2026 PowerPoint template**. Conference artwork and trademarks retain their own terms; the code license does not relicense them.
- Slide 2 portrait: supplied by Nicholas Cusato for this presentation.
- Slide 3 illustrative photograph: **Edmond Dantès / Pexels**, [Colleagues Looking on Laptop Together](https://www.pexels.com/photo/colleagues-looking-on-laptop-together-4339794/), used under the [Pexels License](https://www.pexels.com/license/). The people pictured do not depict the customer or endorse the project.
- Architecture diagrams use Oracle OCI icon artwork. Oracle marks retain their own terms. The diagrams describe research configurations, not a product certification.

Only reviewed final presentation files are published. Drafts, private deployment addresses, raw cloud exports and historical research materials are excluded by the [release inventory](../release/public-files.json) and [release policy](public-release.md).
