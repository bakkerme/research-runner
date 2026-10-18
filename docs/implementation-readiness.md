# MVP delivery and host readiness

The [MVP contract](job-runner-spec.md) is the integration target. This checklist
separates implemented controller behavior from target-host qualification.

Implemented in 0.2.0:

- V1 compatibility and deterministic v2 packages with a small inline policy.
- Runtime-validated result/checkpoint/policy contracts and exported JSON Schemas.
- Persistent cumulative deadlines, finalization and early blocked/partial finish.
- Attempt fencing, restart reconciliation and retained immutable generations.
- Shared dashboard/HTTP status, bounded event replay and SSE reconnect cursors.
- Optional distinct read/control tokens and approval request details.
- Remote preview/apply submission, durable receipts and verified collection.

Before the first real unattended workload:

- [ ] Build the 0.2.0 worker image on the actual host. Update the workload image
  digest and reassemble. Test resource limits, rootless/ordinary ownership,
  read-only inputs and working directories there.
- [ ] Provision Codex authentication independently of package/workspace/exports.
  Run an authenticated model task and confirm result/handoff contract behavior.
- [ ] Exercise finalize steering, approval expiry, immediate stop and forced
  termination with the real image. Fixture success does not prove model behavior.
- [ ] Exercise controller restart and session continuation, confirming original
  deadlines and published artifacts remain intact.
- [ ] Configure token roles for independent monitors, inspect capability output,
  and verify dashboard plus agent access from intended clients.
- [ ] Run a short scoped workload through submission, monitoring and collection.
  Check content and workload acceptance evidence independently of runner hashes.

Deferred until real workflows justify expansion: dataset import/storage,
external worker brokers, automatic retries/continuation, scheduling, domain
validator execution and distributed/concurrent jobs. Capability negotiation
explicitly reports unsupported features. End a blocked run with a useful handoff
rather than expanding the platform to avoid reporting a missing input.
