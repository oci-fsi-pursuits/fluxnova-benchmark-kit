# PAY-05: OCI FX quote workflow qualification

The same 22 synthetic FX scenarios passed in both sequential and parallel BPMN models on Fluxnova 3.0.1 and **OCI managed PostgreSQL 17.6**. This closes the OCI functional gate for corpus revision 2. Performance timing remains open.

- **44/44 expected outcomes:** 34 synthetic payments and ten expected no-payment outcomes.
- **352 repeated operations:** original receipts returned without additional payment effects.
- **Two cross-process replay probes:** both blocked. These are additional to the 44 scenario executions.

Both models used three worker slots, the same immutable payment inputs and the same quote selector. The dedicated concurrency probe used 1,000 ms per quote in both arms. Earlier local 180 ms overlap failures remain retained separately; the longer probe demonstrates overlap capability and is not a speedup measurement.

## Environment and independent verification

An isolated engine and collector shared the existing controller. A fresh, separate single-node PostgreSQL database used 2 OCPUs and 32 GB memory. Images and execution source were pinned. JDBC certificate/hostname verification and encrypted database sessions passed. Five prohibited destinations, including the original application/database and external access, were blocked from the isolated network.

The downloaded archive passed its hash and ZIP checks. The independent auditor verified 276 sealed files, original fixture bindings, completed workflow histories, quote receipts, exact ledger effects and replay records. It did not call the production quote selector to decide correctness. All 444 repository tests passed with zero skips and real k6 enabled; the k6 tests validate the existing harness, not a PAY-05 offered-load generator.

## Cleanup and preservation

The temporary database is **deleted**, the owned containers/network/forwarder are removed, and no cloud test load remains running. Original services and empty queues were preserved. The full reference snapshot was not byte-identical: its metering table gained 12 periodic metrics while the reference engine stayed running. A separate read-only check verified all prior 17,232 metric rows unchanged, zero workflow starts and 15 job-acquisition attempts. The other 48 tables and schema/access metadata matched. The strict-comparison difference and both snapshots remain retained.

Raw evidence stays private on the controller and workstation. This directory contains aggregate results and integrity receipts only. The reference VMs and original managed database remain allocated and billable.

## What remains

Qualify the PAY-05 offered-load generator, telemetry and complete database/ledger reset. Then declare the matched quote-delay and expiry populations, repeat order, duration, deadlines and budget before comparing performance. Live agents, real FX providers, signed-intent integration, capacity, HA and complete cost per outcome are separate gates.

See the [aggregate evidence](summary.json), [workflow explanation](../../docs/pay05-fx-quotes.md), [local attempts](../local-20261031-pay05/README.md), [fixed corpus](../../workloads/pay05/cases.json) and [independent auditor](../../scripts/audit_pay05_functional.py).
