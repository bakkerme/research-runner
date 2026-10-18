# MVP validation — 18 October 2026

This is the initial validation record. See the later
[DGX native qualification](dgx-qualification.md) for the corrected host-agent
architecture and live recovery/finalization results.

Local environment: macOS, Python 3.14.6, pinned Codex Python SDK 0.159.0,
Playwright 1.64.0 and installed Google Chrome in headless mode.

Full local verification:

```sh
RR_BROWSER_TEST=1 RR_BROWSER_EXECUTABLE='/Applications/Google Chrome.app/Contents/MacOS/Google Chrome' .venv/bin/pytest -q
```

Result: **35 passed, 1 skipped**. The skipped test requires Docker and a locally
built/pinned image. Tests needed localhost socket and process-inspection access;
the restricted sandbox denies those operations. Running with that access passed.
There are upstream FastAPI/Starlette deprecation warnings on Python 3.14.

Coverage includes:

- Existing deterministic packages, tamper/path rejection, state/event replay,
  control idempotency, approvals, snapshots and restore.
- V2 complete/blocked/partial closeout, soft deadline finalization, forced
  termination retaining published work, and a worker deadline without a controller.
- Real controller-process death, orphan reconciliation and resumed execution
  preserving the original deadline and published artifact generations.
- Runtime JSON validation, exported schema/code consistency, missing complete
  deliverables, historical SQLite migration and stale-attempt rejection.
- Immutable generation downloads, symlink rejection, lost-response submission
  receipts and collection integrity failures.
- Actual authenticated HTTP submit/start/status/replay/collect through the
  standard-library client; read credentials cannot control jobs.
- Headless Chrome: v1 dashboard flow on localhost and a nonsecure hostname,
  plus v2 sign-in, live partial publication, Finish up, outcome display,
  artifact download and replay after reload.

Additional checks: Python compilation, JavaScript syntax, documentation links,
JSON Schemas and remote-submission preview without mutation. Generated packages,
state, credentials, dependencies and local test outputs are excluded from Git.

Not established here: Docker 0.2.0 image build, target Linux/ARM64 execution,
live authenticated model output, SDK approval/finalize steering or restored
Codex sessions. No credentials were copied and no real workload was launched.
Those checks remain in the [host checklist](implementation-readiness.md).
