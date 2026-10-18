# Native-agent MVP readiness

The [MVP contract](job-runner-spec.md) uses **host Codex by default**. Payloads may
use containers for dependency tools independently of that execution choice.

Implemented:

- Deterministic v1/v2 packages; native v2 runtime needs only the SDK pin.
- Host Codex with normal login/config/session storage; explicit executable override.
- Persistent deadlines, early blocked finish, graceful finalization and partial outputs.
- Recorded execution engine, attempt fencing, same-host thread recovery and receipts.
- Dashboard/API monitoring, read/control roles and verified generation collection.

[DGX native qualification](dgx-qualification.md) passed on 18 October 2026 with its
installed Codex 0.162.1 and SDK 0.159.0. The old live service was left untouched.

Before deploying/using a new host:

- [ ] Install runner dependencies and Codex; check the operator's normal login.
  Set RESEARCH_RUNNER_CODEX_BIN if Codex is absent from the service's PATH.
- [ ] Keep CODEX_HOME/user stable across resumes. Native sessions are stored there,
  outside workload snapshots. Do not copy credentials into payloads.
- [ ] Run a short native qualification; verify deadline, controller restart,
  recall, finalize, artifact collection and owned-agent cleanup.
- [ ] Configure monitor/control roles and verify intended clients can connect.
- [ ] For optional dependency containers, let workload tooling preflight engine,
  images, assets, bounded ownership and cleanup. Those containers are not agent
  workers and are not automatically supervised by --engine.
- [ ] Verify workload conclusions independently of runner integrity checks.

Repeatable probe (no Docker or credential file required):

```sh
.venv/bin/python tools/qualify_host.py --codex-bin /path/to/codex --output runs/NEW_QUALIFICATION
.venv/bin/python tools/qualify_host.py --codex-bin /path/to/codex --output runs/NEW_QUALIFICATION --apply
```

Apply owns a fresh private state and loopback controller. It tests fixture
lifecycles plus two short live model jobs, kills only its own controller, verifies
same-thread recall and collects using read-only credentials. Output contains
private session context and must stay outside Git. It does not upgrade a running
service. Native operation has no container CPU/memory/input-mount enforcement;
capabilities state the actual boundary.

An explicit --engine docker probe remains available for the separate optional
containerized-agent mode; that mode additionally requires --image and --auth-file.
Its credential refresh and cross-host migration are not qualified. Neither is a
requirement for native Codex or workload-owned emulator/tool containers.
