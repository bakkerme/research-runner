# DGX qualification — 18 October 2026

## Intended native-agent path

After correcting the execution boundary, the probe passed using host Codex
0.162.1, SDK 0.159.0, the normal host login and persistent CODEX_HOME. No agent
container was launched and no credentials were copied or projected. Containers
remain optional workload tools.

Verified native checks: soft-timeout partial finish, forced deadline with retained
outputs, early blocked finish, native session store, abrupt controller death,
same-thread recall on attempt 2 without resupplying its marker, unchanged deadline,
prior-generation download, authenticated collection and acknowledged live finalize.
The resumed thread was 01a14d34-db18-7181-816f-d14c6275d75e; the finalize probe used
01a14d35-708c-7f02-b596-f7aefd035a08. No processes retained working directories under
the test jobs afterward. The existing live service and containers were unchanged.

Evidence is retained privately at the qualification checkout under
runs/host-002/. runs/host-001/ preserves a preparation failure caused by macOS
metadata sidecars in the transfer; those generated sidecars were removed only
from the isolated source checkout before retrying. Local regression verification
passed **42 tests**, including headless Chrome, with one optional Docker test
skipped.

Native sessions live in the operator's Codex home, not in job snapshots. This is
same-host recovery, not cross-host migration. Normal host credential management
applies; the read-only projection limitation below concerns only the separate
containerized-agent experiment.

## Earlier optional containerized-agent experiment

Passed in an isolated checkout/state on DGX, without restarting the existing
runner on port 8765 or changing its running containers.

Environment: Linux ARM64, rootless Docker 29.9.0, Python 3.12.3 and Codex Python
SDK 0.159.0. The existing host Codex login was projected read-only for the short
model probes. Credentials were not copied into source, packages or images.

Worker image built on DGX:

```text
research-runner-worker:0.2.0
sha256:c82dd31048a7a9a20fee539f9f594c2d92c71f0adb76cbeb89c40d8a44efd857
```

## Verified behavior

| Check | Result |
| --- | --- |
| Docker soft cutoff | Worker finalized with partial/budget_exhausted. |
| Docker forced cutoff | Unresponsive fixture was terminated; state interrupted, error deadline forced; partial artifacts collected and verified. |
| Early blocked finish | Worker exited with finished/blocked without consuming the budget. |
| Abrupt controller death | Original model container removed on controller restart; job reconciled to interrupted. |
| Live session recovery | Attempt 2 resumed thread 01a14d2f-4f75-71e1-86c7-522627526112 under its original deadline and recalled a random marker without receiving it again. |
| Artifact recovery | Prior generation remained downloadable; resumed result and handoff collected with verified hashes. |
| Live graceful finalize | Thread 01a14d2f-e59a-7d31-9d6a-933dd6ec5ef7 acknowledged finalize, then produced finished/partial with user_requested and a handoff. |
| Ownership cleanup | No containers remained with the qualification state's ownership label. |

Docker inspection confirmed rootless user mapping 0:0, 1 CPU, 2 GiB memory,
256-process limit, all capabilities dropped and no-new-privileges. Declared
instruction/envelope files and the auth projection were read-only; workspace,
session and control mounts were writable as designed. Monitoring and collection
used separate read/control credentials against the isolated HTTP service.

## Fixes discovered by testing

The first attempt is retained as failure evidence. Two bugs were corrected:

- Idempotency keys had a global primary key despite operation/job-scoped lookup.
  Successive jobs could collide during automatic finalization. Existing ledgers
  now migrate to a composite scope/key primary key, and deadline keys also include
  the job ID. Regression tests preserve old key records and verify independent jobs.
- Docker orphan reconciliation removed the container but left runtime.json,
  preventing a resumed attempt from launching. The marker is now removed only
  after owned-container cleanup succeeds. A regression test checks both paths.

An opt-in host setting, RESEARCH_RUNNER_CODEX_AUTH_FILE, supplies the read-only
credential projection. Submitted payloads cannot choose the host credential path.
The target worker image did not need rebuilding for these host-launcher/ledger
fixes; its worker protocol was already 0.2.0.
After the fixes, local regression verification passed 39 tests (including
headless Chrome); the optional local Docker test was skipped. DGX's initial host
suite passed 32 tests with 4 opt-in tests skipped, followed by the Docker/live
qualification above.

## Retained evidence and limits

Private evidence remains on DGX under:

```text
/home/dgx/jobs/research-runner-qualification-20261018-3549394/runs/live-001/
/home/dgx/jobs/research-runner-qualification-20261018-3549394/runs/live-002/
```

The successful run has qualification.json, redacted isolation.json, before/after
status records, collection receipts and output generations. The session directories
remain private. Only summary and mount/limit metadata were retrieved locally under
ignored runs/dgx-qualification-20261018/. Empty auth mount-point stubs are not
credential copies and are still rejected by the conservative snapshot filter.

This establishes same-host persistent-session recovery and short authenticated
finalization. It does not establish cross-host snapshot migration, live human
approval round trips, or token refresh during a long unattended job. Codex normally
refreshes credentials during use, but the read-only
projection deliberately cannot persist refreshed credentials; long-run credential
provisioning still needs qualification. See the
[official credential-storage documentation](https://learn.chatgpt.com/docs/auth).

The existing live deployment is unchanged. Use the fixed launcher and migrated
ledger when deploying 0.2.0; do not infer the old service was upgraded by this test.
