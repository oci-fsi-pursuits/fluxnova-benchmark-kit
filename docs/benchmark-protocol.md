# Proposed reusable benchmark protocol

Version 0.1 is a contribution proposal, not a FINOS standard, product certification or production sizing guarantee. Ask how one declared change affects time to a correct, authorized outcome under a stated workload and environment.

## Declare the experiment

Before execution retain the hypothesis, baseline/treatment, BPMN/input hashes, population and expected outcomes, arrival schedule, warmup/measurement/drain windows, deadlines, repeats and pair order, resources/topology, software digests, reset/durability rules, telemetry and stop conditions.

Current repeated studies use five pairs. Two seconds is their declared test deadline, not a universal payment SLA. Do not change populations or acceptance rules after seeing results.

## Verify outcomes independently

Bind the oracle to original immutable inputs. Verify decisions, workflow history and exact business effects. Exercise unauthorized/tampered requests, duplicates/replays and relevant unknown/failure paths. HTTP success or process completion alone is insufficient.

For mixed traffic retain separate expected/observed settlements, refusals and safe stops. Only correct settlements contribute to settlement goodput. Retain late, dropped, unresolved and incorrect outcomes.

## Establish validity

Qualify generator dispatch/headroom, complete telemetry, starting-state resets and post-drain reconciliation. Declare whether deadline misses are a gate or the measured outcome. Correctness, missing evidence and generator failures cannot become a positive performance claim.

Match controls except the treatment. Retain every attempted run and interruption reason; do not rerun selectively for favorable results. Incomplete or invalid series cannot produce the planned aggregate.

## Publish an auditable submission

Include protocol/source identifiers, sanitized environment and artifact manifests, raw-evidence availability, independent audit, outcome counts, all paired changes, reset/telemetry checks, limitations and cleanup. Distinguish fixtures, local functional checks and cloud measurements.

Define the observation boundary, including completion polling. Per-run percentile medians are not pooled percentiles; paired ranges are not confidence intervals. Report regressions and distinguish cost estimates from complete attributed bills.

Use the [measurement contract](measurement-contract.md), JSON schemas under `schemas/` and [W02 mixed protocol](w02-mixed-cloud-protocol.md). Preserve original evidence and explain protocol revisions.
