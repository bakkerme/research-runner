# Research Runner 0.2.0 MVP

A single-host runner for bounded agent jobs. Package a workload and its existing
context, submit it repeatably, monitor through a dashboard or JSON API, and
collect hash-verified outputs. Workloads define their own scope, tools and
acceptance criteria. The runner reports complete, partial and blocked outcomes
separately from execution success.

Start with the [MVP contract](docs/job-runner-spec.md),
[producer-agent handoff](docs/producer-agent-handoff.md), and
[target-host checklist](docs/implementation-readiness.md).

## Install and test

Python 3.11+ is required; Python 3.12 is recommended for the worker image. Local
fixture validation uses Python 3.14. Create an environment with a supported
interpreter rather than relying on an unrelated system Python:

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
.venv/bin/pytest -q
```

Docker is the production isolation mode. Build the worker on the target host
(ARM64 and x86 wheelhouses are not interchangeable):

```sh
.venv/bin/pip download -d wheelhouse 'setuptools>=75' wheel 'openai-codex==0.159.0' 'fastapi==0.116.1' 'uvicorn==0.35.0' 'pydantic==2.14.1'
docker build -t research-runner-worker:0.2.0 .
docker image inspect research-runner-worker:0.2.0 --format '{{.Id}}'
```

The base image and SDK are pinned. Put the returned image ID and image tag into
your package's `runtime` section before assembling. `sample-mvp/` is an editable
v2 fixture template with an unconfigured image ID; real workloads select the
`codex` adapter and supply their own brief, inputs and output declarations.
Legacy `sample/` and `sample-codex/` demonstrate v1; their old image pins require
replacement on a new host. Rebuild the worker for 0.2.0; updating only host Python
code does not upgrade a running image.

## Serve and access

```sh
.venv/bin/research-runner --state ./var serve
```

The service discovers its Tailscale IPv4 address and prints the dashboard URL.
Discovery failure stops startup. For local access:

```sh
.venv/bin/research-runner --state ./var serve --host 127.0.0.1
```

Configure distinct `RESEARCH_RUNNER_READ_TOKEN` and
`RESEARCH_RUNNER_CONTROL_TOKEN` in the server environment for independent human
and agent access. Both or neither must be set. With neither, all callers on the
trusted network have full controls. Do not expose this service publicly.
Dashboard sign-in accepts either token; read-only controls are rejected by the
server. CLI clients use `RESEARCH_RUNNER_TOKEN` privately from their environment.

## Prepare, submit, monitor and collect

Copy `sample-mvp/` to a new workload source folder, select its runtime image pin,
and customize the brief and declared inputs. Every source file must be declared;
keep generated archives outside the source folder. Context, plans, provenance
and existing results are ordinary inputs rather than domain-specific runner
features. Keep credentials and unrelated private data out of packages.

```sh
.venv/bin/research-runner assemble SOURCE job.rrpkg
.venv/bin/research-runner verify job.rrpkg
.venv/bin/research-runner --url http://RUNNER:8765 capabilities
.venv/bin/research-runner --url http://RUNNER:8765 submit job.rrpkg --key submit-001 --receipt receipt.json
.venv/bin/research-runner --url http://RUNNER:8765 submit job.rrpkg --key submit-001 --receipt receipt.json --apply
.venv/bin/research-runner --url http://RUNNER:8765 start JOB_ID --key start-001 --apply
.venv/bin/research-runner --url http://RUNNER:8765 status JOB_ID
.venv/bin/research-runner --url http://RUNNER:8765 events JOB_ID --after 0 --limit 100
.venv/bin/research-runner --url http://RUNNER:8765 collect JOB_ID NEW_DIRECTORY
.venv/bin/research-runner --url http://RUNNER:8765 collect JOB_ID NEW_DIRECTORY --apply
```

Remote mutations preview without `--apply`; collecting pins one publication and
creates a fresh destination. Submission saves request identity before sending;
repeat the same key/receipt after uncertain network responses. Reusing a start
key returns its original result; deliberate resume uses a new key under the
original deadline. `finalize JOB_ID --key KEY --apply` requests wrap-up;
`stop JOB_ID --key KEY --apply` forces an immediate stop. Status/event reads never
steer or resume work. The same controls and publications appear in the dashboard.

Packages are deterministic hashed tar archives: maximum 512 entries, 32 MiB/file,
128 MiB total. V2 adds a small inline time policy, result/handoff protocol and
optional checkpoints. Large dataset imports, scheduling, automatic retries and
automatic continuation are deferred. Capability discovery reports these limits.

## Deadline and result behavior

The total wall allowance starts on first launch and persists across interruptions
and resumes. At the soft cutoff, the runner asks the worker to stop new work and
finalize. Before the hard deadline, it kills remaining owned execution using a
termination reserve. Cooperative shutdown cannot guarantee a responsive model;
periodic partial publications preserve work when forced termination is needed.

A worker can finish early when no allowed path remains. V2 `finished` means a
protocol-valid closeout whose task outcome is `complete`, `partial` or `blocked`.
Workload conclusions remain claims and validation is `not_run`; include your own
checks and review evidence. V1 retains historical `completed` behavior.

Required v2 files are `outputs/result.json` and `outputs/handoff.md`;
`outputs/checkpoint.json` is recommended. See [schemas](schemas/result.json) or
run `research-runner schemas` for exact JSON shapes and the contract for
cross-field constraints. Additional declared outputs are mandatory for complete
scope; partial/blocked closeout may explain their absence.

## Recovery, snapshots and isolation

SQLite state/events share transactions. Attempts fence stale updates; control
keys bind duplicate requests to their payloads. The controller owns one lifetime
state lock and reconciles owned orphan workers before allowing resume. Pending
approvals expire on restart/finalization. Uncertain steering is not replayed.

Do not run local-state CLI commands while the server owns the same state directory;
use HTTP. Offline `submit`, `start`, `stop`, `download`, `snapshot` and `restore`
remain available for administration; legacy local controls apply immediately.
`--engine host` is trusted debugging only, without Docker's isolation/resource
or read-only input enforcement. Production Docker workers receive only job,
session and IPC mounts, no Docker socket or host home. Rootless Docker uses UID
0 mapped to the unprivileged daemon owner; ordinary Docker uses host UID/GID.

Stop all jobs before snapshotting. Snapshots include SQLite, jobs and selected
Codex session JSONL, exclude control inboxes and known credential files, and may
contain private task content. Restore only into a fresh directory. Test live
session continuation on the target host before relying on migration.

```sh
.venv/bin/research-runner --state ./var snapshot JOB_ID snapshot.tar.gz
.venv/bin/research-runner restore snapshot.tar.gz restored-state
```

## Validation boundaries

The automated suite exercises v1 compatibility, v2 normal/blocked/partial finish,
forced deadline preservation, persisted budgets, stale-attempt rejection, JSON
contracts, immutable downloads, read/control access, replay, transport receipts
and collection. See [host readiness](docs/implementation-readiness.md) for the
remaining operational checks.

No live authenticated Codex task or 0.2.0 Docker build has been qualified on this
Mac. Authentication must be provisioned separately from packages, workspace and
exports. Fixture output is deterministic and labelled fixture, not model evidence.
The SDK uses its pinned approval handler bridge; live approval/finalize steering
and restored sessions must be checked on the actual host.

Optional browser tests use headless Chromium/Chrome:

```sh
.venv/bin/pip install -e '.[browser]'
RR_BROWSER_TEST=1 RR_BROWSER_EXECUTABLE=/path/to/chrome .venv/bin/pytest -q tests/test_browser_dashboard.py
```

Docker recovery tests are opt-in with `RR_DOCKER_TEST=1` after building and pinning
the sample package locally. Do not publish local state, wheelhouses, generated
packages, credentials or private workload material to Git.
