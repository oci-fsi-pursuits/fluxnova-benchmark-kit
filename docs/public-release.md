# Release policy and readiness

The release owner approved Apache 2.0 and a clean companion repository on 28 October 2026 UTC. [LICENSE](../LICENSE) applies to this code. The destination is `oci-fsi-pursuits/fluxnova-benchmark-kit`. Publication requires matching test and sanitization receipts for its commit. Logos, trademarks and third-party dependencies retain their own terms.

## Final documents only

[release/public-files.json](../release/public-files.json) names every included guide, final handout/source and selected evidence directory. The generated `.gitignore` block excludes other documents and presentation revisions by default. Asset hashes bind the approved presentation, PDFs, diagrams and unchanged Fluxnova logo. Naming a new file `final` does not authorize its release.

Previous decks, draft briefs, historical notes, internal presentation sources and raw cloud exports remain in the private research archive. The [final conference deck](presentation.md) is a separately reviewed publication copy, including its hidden slides, notes, metadata, embedded workbook and links. Its architecture drawings omit deployed addresses. Untracking a file leaves its local copy and existing Git history intact. A fresh public companion must not inherit that history. Do not change the original repository's visibility or force-push rewritten research history.

To update the inventory and regenerate ignore rules:

```bash
python scripts/build_public_candidate.py --sync-ignore
```

Review and commit the selected sources, then export into a new directory:

```bash
python scripts/build_public_candidate.py --output dist/public-candidate-NEW
```

The export refuses dirty sources, unreviewed binaries, changed final assets, private workstation paths, deployed OCI identifiers and internal classification markings. It replaces private registry addresses in guides with explicit placeholders. It includes code, the final-document inventory and only selected `README.md`/`summary.json` evidence files. Gitleaks supplements these content checks; neither scanner proves that every possible sensitive item has been found.

## Release gates

1. Verify the current catalogue and evidence hashes. Preserve each study's inputs, controls, failures, deadline misses and qualification limits. Do not infer a performance benefit from a functional check.
2. Review final artifacts visually and inspect notes, metadata, embedded data and hyperlinks. Publish only inventory-listed files. Retain original research evidence privately.
3. Build from a clean source commit, verify every exported byte, scan the snapshot and the companion's complete Git history, and run tests, catalogue checks and the offline demo on Windows and Linux.
4. Sync both repositories without rewriting history. Require successful CI for the exact published companion commit, not an earlier green run. Keep local receipts for the source commit, exported hashes and remote commit.
5. The owner changes visibility only for `oci-fsi-pursuits/fluxnova-benchmark-kit`. The original `fluxnova-benchmarking` research repository stays private. Check anonymous access to the README, final presentation and QR destinations after the change. No upstream message or PR is part of this release.

The [mixed-pattern series](../validation/oci-20261028-w02-mixed-comparison/README.md) completed all ten arms, with 6,000 correct measured outcomes and all 1,648 deadline misses retained. Independent audit verified 527 exported files. Owned cleanup, original-reference preservation, browser checks and visual review passed. Settlement timeliness improved and refusal timeliness worsened; both findings are included in the final brief. The released source must also pass [its own CI](https://github.com/oci-fsi-pursuits/fluxnova-benchmark-kit/actions/workflows/ci.yml); historical CI is not a substitute.

The source exporter reads committed file bytes and normalizes rewritten Markdown to LF. Its hashes therefore describe the committed release across Windows and Linux checkouts, not incidental workstation line endings. Deployed HTML forms and telemetry requirements remain application resources, separate from excluded document drafts.

## Secret-scan review

The initial private history inventory covered 1,043 blobs across 112 commits. Its 18 Gitleaks matches were verified source-file SHA-256 values, explicit example identifiers and a Terraform enum. Historical exceptions are exact fingerprints retained only in the private research checkout; the clean export inherits none of them. Fresh snapshots permit only the exact safe values in the named files, with all default rules enabled. A negative control verified detection of synthetic token replacements in all five named example files. No production credential was used.

## Upstream route

The [upstream proposal](upstream-proposal.md) starts with the portable harness, evidence contracts and original-input oracle; OCI integrations remain optional. FINOS's [contribution policy](https://github.com/finos/fluxnova-bpm-platform/blob/main/CONTRIBUTING.md) governs CLA, review and acceptance. This independent kit is not an adopted FINOS standard.
