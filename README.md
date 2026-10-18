# Research Runner 0.2.0 MVP

A single-host runner for bounded agent jobs. **Codex runs on the host by default**,
using the operator's normal login and session store. A payload supplies its brief,
existing context, tools and acceptance criteria. Containers are optional workload
tools—for example, an emulator with isolated dependencies—not a prerequisite for
running the agent.

The runner provides repeatable packages, a human dashboard and agent HTTP API,
checkpoints, deadline wrap-up, complete/partial/blocked outcomes and hash-verified
collection. The [package and API contract](docs/job-runner-spec.md) defines the
interface for workload producers and monitoring clients.

## Install and serve

Use Python 3.11+ (3.12 recommended) and an installed, authenticated Codex CLI.

```sh
python3.12 -m venv .venv
.venv/bin/pip install -e '.[test]'
codex login status
.venv/bin/research-runner --state ./var serve
```

The SDK is pinned to 0.159.0. The native adapter uses Codex found on PATH, or
`RESEARCH_RUNNER_CODEX_BIN` when explicitly configured by the operator. If neither
is available, it falls back to the SDK's bundled executable. Native authentication,
config and sessions use the normal CODEX_HOME (default ~/.codex); no credentials
are copied or mounted. Keep the same user/home/runtime when resuming a job.

The service discovers its Tailscale IPv4 address and prints the dashboard URL.
For localhost use `serve --host 127.0.0.1`. Discovery failure stops startup.
Set distinct `RESEARCH_RUNNER_READ_TOKEN` and `RESEARCH_RUNNER_CONTROL_TOKEN`
server variables for independent monitors. Set both or neither; without tokens
all callers on the trusted network have controls. Do not expose it publicly.
Dashboard sign-in accepts either role. Remote CLI clients use
`RESEARCH_RUNNER_TOKEN` privately from their environment. Server control tokens
are stripped from the worker environment.

## Prepare and run a workload

Copy `sample-mvp/` into a fresh source directory and customize the brief, agent
instructions, declared inputs and outputs. Its native runtime declaration needs
only the SDK pin; no Docker image is required. Select `codex` for real work;
`fixture` produces deterministic lifecycle-test output. Every source file must be
declared. Keep archives outside the source folder and credentials out of payloads.
Plans, provenance, previous results and workload tools are ordinary inputs.

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

Remote mutations preview without `--apply`; collection pins a generation and
creates a fresh destination. Submission persists request identity before sending.
Repeat its key/receipt after uncertain responses. A repeated start key returns
its original result; a deliberate resume uses a new key under the same deadline.
`finalize JOB_ID --key KEY --apply` requests wrap-up; `stop` forces immediate stop.
Monitoring never implicitly steers or resumes work.

Packages allow 512 archive entries, 32 MiB/file and 128 MiB total, with deterministic
hashes and strict extraction. Large dataset storage, scheduling, automatic retries
and automatic continuation are unsupported. Capability discovery reports the
limits of the running service.

## Lifecycles and dependency tools

V2 policies allocate total wall time, finalization reserve and termination reserve.
The allowance starts on first launch and persists across interruptions. At the soft
cutoff, the worker is asked to finalize; at the forced cutoff remaining owned agent
processes are killed. Periodic publications preserve work if graceful exit fails.

Agents can finish early when no allowed path remains. Required closeout files are
`outputs/result.json` and `outputs/handoff.md`; `outputs/checkpoint.json` is
recommended. See [schemas](schemas/result.json) or `research-runner schemas`.
`finished` means protocol-valid closeout, with a separate complete/partial/blocked
claim. Validation is `not_run`; workload-specific checks/review are payload work.
V1 jobs use the `completed` execution state.

Workload tooling may invoke Docker/Podman for dependencies using normal host
permissions. It must preflight its engine, pin its tool images, record ownership,
mount originals appropriately, bound operations to remaining time and clean up
its own containers. The MVP does not supervise arbitrary dependency containers
through the agent execution engine. Agent process deadlines are not a substitute
for tool-level timeout/cleanup. Container requirements belong in the workload
brief/tools, not the agent runtime declaration.

Native Codex uses its workspace sandbox and configured approvals. Host execution
does not provide container-level CPU/memory caps or enforce immutable input mounts;
resource requests are advisory except for the runner's deadline/process supervision.
Capabilities identify the actual execution engine and enforcement boundary.

## Recovery and snapshots

The controller owns the state lock, persists events/state transactionally and
reconciles owned orphan agent processes before resume. Attempts fence stale
updates; operation-scoped keys prevent duplicate actions. Pending approvals expire
on restart/finalization. Uncertain steering is not replayed. A started job cannot
silently switch between native and containerized execution.

Use the HTTP client while the service owns a state directory. Local-state commands
remain available offline for administration. Snapshots require all jobs stopped
and include the ledger, workload files and published artifacts. **Native Codex
sessions remain in the operator's CODEX_HOME and are not bundled.** Same-host
recovery uses the stored thread ID; cross-host session migration is not advertised.
Do not export the host's whole Codex home or credentials to make a snapshot portable.

## Tests and host verification

```sh
.venv/bin/pytest -q
.venv/bin/python tools/qualify_host.py --codex-bin /path/to/codex --output runs/NEW_QUALIFICATION
.venv/bin/python tools/qualify_host.py --codex-bin /path/to/codex --output runs/NEW_QUALIFICATION --apply
```

The probe defaults to native execution, needs no Docker, owns a fresh private state
and tests deadlines, blocked finish, real model session recall after controller death,
finalize steering and collection. Run it with the same user, Codex executable
and CODEX_HOME as the service. Its output contains private session context and
belongs outside Git. The probe stops only its own controller and agent processes.
Optional browser checks use `RR_BROWSER_TEST=1` and
`RR_BROWSER_EXECUTABLE=/path/to/chrome` after installing `.[browser]`.

## Optional containerized agent

`--engine docker` is an explicit alternative, separate from workload tool containers.
It requires a built worker image and runtime image/tag digest in the package.
The Dockerfile and v1 samples remain available for that mode. Build on the target
architecture using an appropriate wheelhouse; rootless mapping and read-only inputs
apply there. The optional `RESEARCH_RUNNER_CODEX_AUTH_FILE` setting projects an
existing auth cache read-only, so the agent cannot persist refreshed credentials
to that file.
Native execution does not use that setting. No Docker socket is mounted into
containerized agents. Do not infer support for nested workload containers.

Do not commit private state, credentials, generated packages, test outputs or
workload assets.
