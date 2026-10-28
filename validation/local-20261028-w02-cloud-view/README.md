# Mixed OCI report and browser automation — 28 October 2026 UTC

The saved W02-M2 view separates correct settlements, expected refusals and safe unknown-check stops. Only timely settlements contribute to payment goodput. It re-runs the original archived auditor against the sealed cloud export and checks that the result exactly matches the retained independent audit before creating HTML and JSON.

The real [first cloud qualification](../oci-20261028-w02-mixed-qualification/README.md) passed all twenty displayed dependency-profile rows, JSON evidence navigation, keyboard disclosure, desktop/laptop overflow checks and a visual review of three screenshots. No network requests, payments, model calls or new workload executions occurred. The viewer explicitly states **Qualification only** and withholds an aggregate improvement claim.

The complete-campaign UI was separately exercised using **test fixtures, not experimental results**: six outcome rows and fifteen pair/outcome rows matched their fixture values, including five visible refusal regressions despite improved settlement goodput. The final check also exercised keyboard disclosure when only the paired table was present. These fixtures establish report behavior, not a measured performance result. Actual campaign screenshots still need review after the live study finishes.

Three new regression tests verify that incomplete/qualification output cannot display a stale aggregate, that a complete result requires five pairs, and that refusal regressions remain visible alongside a settlement gain. The full repository suite passed **385 tests with zero skips**, including real k6 checks; the final keyboard-only adjustment passed the browser integration check.

Retained local artifacts:

- `dist/w02-mixed-cloud-view-20261028-002/report.html`: audited qualification view.
- `dist/w02-mixed-cloud-browser-20261028-001/`: browser evidence and reviewed screenshots.
- `dist/w02-cloud-view-ui-fixture-review-20261028-002/`: final fixture-only browser check; never use its numeric values in research slides.
- `dist/w02-cloud-view-tests-20261028.log`: full test output.

The [builder and checker](../../scripts/render_w02_mixed_cloud.py) and [commands](../../docs/browser-automation.md) reproduce the saved view from retained private evidence. The running isolated trial is configured to generate and browser-check a complete result after independent audit, cleanup and source-preservation verification. Publication and final visual review remain separate.

Research and OCI implementation: Nicholas Cusato. Fluxnova software: FINOS contributors.
