# Local HTTP interoperability and FNV-07 qualification

The [summary](summary.json) separates a standalone HTTP diagnostic, functional qualification, timing-driver validity and deadline outcomes. These synthetic checks used Java 21, Python 3.14 and local H2. No cloud or model calls were used.

## What changed

The receipt fixture already closed each HTTP/1.0 connection after one request but did not explicitly advertise closure. It now sends `Connection: close` by default. A standalone shared-Java-client test removed Fluxnova and its load scheduler: two matched pairs at concurrency two sent 40 calls per arm, alternating arm order. Each legacy arm completed 22/40; each explicit-close arm completed 40/40. The 36 failed legacy calls had no matching handler request. Independent reconciliation preserved every client call and verified the saved evidence seal.

This supports the targeted connection-lifecycle fix in the tested setup. It does not establish a specific JVM bug, other-version behavior or performance improvement. The legacy header mode remains available for reproduction.

## Workflow qualification and retained limits

| Attempt | Independent result | Deadline outcome |
| --- | --- | --- |
| Traced explicit-close 004 | Rejected: 18 correct workflows, two missed arrivals; no delegate failures | Failed qualification retained; not promoted |
| Untraced explicit-close 005 | Passed driver audit: 20 correct workflows, 60 attempts/effects, 120 job records; no retries or missing arrivals | 20/20 observed within two seconds |
| Untraced new-default 007 | Passed the same driver audit: 20 correct workflows, 60 attempts/effects, 120 job records; no retries or missing arrivals | **0/20 observed within two seconds; all 20 deadline misses retained** |

The default-policy six-case functional audit also passed: 19 attempts, eight intended failed attempts, 12 distinct effects, three completed workflows, two expected blocked workflows and one unchanged waiting control. A blocked workflow with an external effect remains classified as blocked.

Driver qualification validates scheduling evidence and correct outcome observations; it is not a claim that the workload meets its deadline. The observed deadline variation needs resource/scheduling investigation on a controlled placement. Historical failures and all diagnostic attempts remain local; the passing runs do not replace them. Raw local identities, credentials, logs and databases are excluded from this sanitized receipt. Hashes link the receipt to retained original seals; they are not a substitute for access to raw evidence for independent review.

See [method, tools and reproduction commands](../../docs/fnv07-timing.md) and the [roadmap](../../ROADMAP.md). PostgreSQL operation, resource headroom, scale-out, capacity and complete cost attribution remain unqualified.
