# Release policy and readiness

The release owner approved Apache 2.0 and a clean companion repository on 28 October 2026 UTC. [LICENSE](../LICENSE) is now applied to this code. The intended destination is `oci-fsi-pursuits/fluxnova-benchmark-kit`. Keep it private until the remaining pattern study and release checks below are complete. Logos, trademarks and third-party dependencies retain their own terms.

## Final documents only

[release/public-files.json](../release/public-files.json) names every included guide, final handout/source and selected evidence directory. The generated `.gitignore` block excludes other documents and presentation revisions by default. Asset hashes bind the approved PDF and unchanged Fluxnova logo. Naming a new file `final` does not authorize its release.

Previous decks, draft briefs, historical notes, internal presentation sources and raw cloud exports remain in the private research archive. Untracking a file leaves its local copy and existing Git history intact. A fresh public companion must not inherit that history. Do not change the original repository's visibility or force-push rewritten research history.

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

1. Complete W02-M2's five declared pairs across all five dependency profiles. Independently verify original inputs, outcomes, effects, timing, controls and every exported hash. Retain deadline misses, failed attempts and incomplete results.
2. Confirm owned resource cleanup and preservation of the original reference environment.
3. Review the saved browser report and update the final pattern register, brief and PDF from the audited result. Do not infer a benefit from qualification alone.
4. Build the clean package, verify its document inventory, scan both snapshot and new Git history, and run tests plus the offline demo on Windows and Linux. Check public destination links and file access.
5. Publish the clean companion only after these checks pass. The original research repository stays private. No upstream message or PR is part of this release.

The preceding source gate (`b253b29`) passed 385 tests and CI on Linux/Python 3.12 and Windows/Python 3.13. The final candidate must repeat the checks appropriate to its changes; historical CI is not a substitute for checking the release source.

## Secret-scan review

The initial private history inventory covered 1,043 blobs across 112 commits. Its 18 Gitleaks matches were verified source-file SHA-256 values, explicit example identifiers and a Terraform enum. Historical exceptions are exact fingerprints. Fresh snapshots permit only the exact safe values in the named files, with all default rules enabled. A negative control verified detection of synthetic token replacements in all five named example files. No production credential was used.

## Upstream route

The [upstream proposal](upstream-proposal.md) starts with the portable harness, evidence contracts and original-input oracle; OCI integrations remain optional. FINOS's [contribution policy](https://github.com/finos/fluxnova-bpm-platform/blob/main/CONTRIBUTING.md) governs CLA, review and acceptance. This independent kit is not an adopted FINOS standard.
