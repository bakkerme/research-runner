# Research Runner 0.5.0

A single-host runner for bounded agent jobs. **Codex runs on the host by default**,
using the operator's normal login and session store. A payload supplies its brief,
existing context, tools and acceptance criteria. Containers are optional workload
tools—for example, an emulator with isolated dependencies—not a prerequisite for
running the agent.

The runner provides repeatable packages, a human dashboard and agent HTTP API,
checkpoints, deadline wrap-up, complete/partial/blocked outcomes and hash-verified
collection. The [package and API contract](docs/job-runner-spec.md) defines the
interface for workload producers and monitoring clients.

## Use from another repo

Start with the [agent integration quickstart](docs/agent-quickstart.md). A project
agent can install a pinned wheel from [GitHub Releases](https://github.com/bakkerme/research-runner/releases)
or download a tested CI artifact, then work entirely in its own repo. No Runner
clone, local service or server dependencies are needed. The wheel includes the
CLI, library, maintained docs, schemas and bundled agent skill.

The simplest project dependency is a versioned release wheel URL in
`requirements-research.txt`; see the quickstart for the exact URL and the
unreleased-CI download alternative. After installation:

```sh
research-runner docs
research-runner init --url http://RUNNER:8765 --name experiment
# Edit research/experiment/inputs/question.md and supply required inputs.
research-runner package experiment
research-runner submit experiment --apply
research-runner start experiment --apply
research-runner monitor experiment --timeout 60
research-runner pull experiment --apply
research-runner results experiment
```

The library owns repo-local job identities, receipts, event cursors and recoverable
hash-verified collections. New research definitions return structured claims,
evidence, validation checks, reproduction steps and limitations. See the
[producer workflow and library API](docs/client-workflow.md) for installation,
named experiments, dashboard adoption, recovery and result contracts.

## Install and serve

Install [uv](https://docs.astral.sh/uv/getting-started/installation/) and an
authenticated Codex CLI under the account that will run the service. Use Python
3.11+ (3.12 recommended); uv can provision the requested Python version.
Install a pinned, tested release wheel with the `server` extra (no source checkout
or test dependencies needed):

```sh
uv tool install --python 3.12 \
  'research-runner[server] @ https://github.com/bakkerme/research-runner/releases/download/v0.5.0/research_runner-0.5.0-py3-none-any.whl'
uv tool update-shell
# Open a new shell if uv added its executable directory to PATH.
codex login status
research-runner --state "$HOME/.local/share/research-runner" serve --host 127.0.0.1
```

[uv tool install](https://docs.astral.sh/uv/guides/tools/) manages a persistent
isolated environment and exposes the executable on PATH; activation is unnecessary.
Runner is distributed through GitHub releases, not PyPI. For an unreleased change,
use a successful CI artifact as described in the [quickstart](docs/agent-quickstart.md),
verify its `SHA256SUMS`, and install the downloaded wheel instead:

```sh
uv tool install --python 3.12 '/absolute/path/research_runner-0.5.0-py3-none-any.whl[server]'
```

Keep state outside the installation so reinstalling Runner preserves jobs.
For an existing deployment, pass its original absolute `--state` directory;
changing it selects a separate job store. To run continuously on a Linux host, follow
the [user systemd service setup](docs/linux-service.md).

The SDK is pinned to 0.159.0. The native adapter uses Codex found on PATH, or
`RESEARCH_RUNNER_CODEX_BIN` when explicitly configured by the operator. If neither
is available, it falls back to the SDK's bundled executable. Native authentication,
config and sessions use the normal CODEX_HOME (default ~/.codex); no credentials
are copied or mounted. Keep the same user/home/runtime when resuming a job.

The service binds to `127.0.0.1` by default and prints the dashboard URL.
The explicit bind in the command above also works with older release wheels
that defaulted to Tailscale discovery.
For remote access, configure `serve --host SERVER_BIND_ADDRESS` with an address
assigned to the host. Tailscale is optional: `serve --host tailscale` discovers
its IPv4 address; discovery failure stops startup.
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
research-runner assemble SOURCE job.rrpkg
research-runner verify job.rrpkg
research-runner --url http://RUNNER:8765 capabilities
research-runner --url http://RUNNER:8765 submit job.rrpkg --key submit-001 --receipt receipt.json
research-runner --url http://RUNNER:8765 submit job.rrpkg --key submit-001 --receipt receipt.json --apply
research-runner --url http://RUNNER:8765 start JOB_ID --key start-001 --apply
research-runner --url http://RUNNER:8765 status JOB_ID
research-runner --url http://RUNNER:8765 events JOB_ID --after 0 --limit 100
research-runner --url http://RUNNER:8765 collect JOB_ID NEW_DIRECTORY
research-runner --url http://RUNNER:8765 collect JOB_ID NEW_DIRECTORY --apply
```

Remote mutations preview without `--apply`; collection pins a generation and
creates a fresh destination. Submission persists request identity before sending.
Repeat its key/receipt after uncertain responses. A repeated start key returns
its original result; a deliberate resume uses a new key under the same deadline.
`finalize JOB_ID --key KEY --apply` requests wrap-up; `stop` forces immediate stop.
Monitoring never implicitly steers or resumes work.

Packages allow 512 archive entries, 8 GiB per input file and 64 GiB total
uncompressed, with deterministic hashes and strict extraction. External dataset
references, scheduling, automatic retries and automatic continuation are unsupported. Capability discovery reports the
limits of the running service.

Include the objects of research and their required dependencies in the package.
Assembly, verification, upload and extraction stream file contents; input size does
not determine RAM use. The operator can set `RESEARCH_RUNNER_INPUT_FILE_BYTES`
and `RESEARCH_RUNNER_INPUT_TOTAL_BYTES` (positive integer bytes, total >= file) on
both producer and server to change input quotas. `/api/capabilities` advertises the
server quotas. Allow disk space for the upload, sealed archive and extracted inputs.
Output publication remains limited to 32 MiB per file and 128 MiB per generation.

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

`research-package/v3` packages may declare `tool_images`. The runner builds a
missing image, or reuses one whose `research.input_hash` label matches, before
the worker starts. The tag is `localhost/rr-tool/<name>:in-<input hash>`. Fetch
URLs must be https and are checked against the declared sha256. A build, hash,
timeout or architecture failure ends the job before any Codex turn. Provisioning
time is added back to the deadline so it is not taken from `time_seconds`. The
receipt is published as `outputs/tool-images.json`. `required_checks` names ids
in `outputs/research.json`; outcome `complete` is accepted only when each one
passed. `partial` and `blocked` still publish. v1 and v2 packages are unchanged.
See [the contract](docs/job-runner-spec.md) and [examples/tool-image](examples/tool-image/).

Workload tooling may still invoke Docker/Podman for other dependencies using
normal host permissions. It must preflight its engine, record ownership, mount
originals appropriately, bound operations to remaining time and clean up its own
containers. The runner does not supervise arbitrary dependency containers through
the agent execution engine. Agent process deadlines are not a substitute for
tool-level timeout/cleanup. Container requirements for those other tools belong
in the workload brief, not the agent runtime declaration.

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

The dashboard uses a compact, full-window workspace with Activity, Files, Findings,
and Summary tabs. Session sign-in, package submission, and job Actions open on demand;
the jobs sidebar can be hidden. Activity lists up to 100 events per page, with a
separate reader for full messages and command output. Long commands and raw event
payloads expand on demand. Consecutive streaming chunks share one row. Search and
type filters apply to the current page; Older events and Latest events navigate
history. Per-file publication records are excluded before activity pagination so
large generations do not bury messages and tools; raw history remains available
through the API (omit `activity=true`). The Files tab searches published paths,
previews text files up to 1 MB, and retains per-file downloads. Enable Follow live
to follow new activity; scrolling back pauses it. Appearance supports light, dark,
or your system theme and remembers your choice in this browser.

The state lock records the owning PID and dashboard URL. A lock error means a
controller still owns that directory, even if a separate foreground invocation
has exited. Use that dashboard or the HTTP client. For a user-systemd deployment,
inspect or restart the existing controller with:

```sh
systemctl --user status research-runner.service
systemctl --user restart research-runner.service
```

Restarting interrupts active work; inspect status first. Keep the lock file in
place: the operating system releases the lock when its owning process exits.
The server allows five seconds for open HTTP streams to close during shutdown.

Use the HTTP client while the service owns a state directory. Local-state commands
remain available offline for administration. Snapshots require all jobs stopped
and include the ledger, workload files and published artifacts. **Native Codex
sessions remain in the operator's CODEX_HOME and are not bundled.** Same-host
recovery uses the stored thread ID; cross-host session migration is not advertised.
Do not export the host's whole Codex home or credentials to make a snapshot portable.

## Tests and host verification

For development, run these commands from the source checkout. This environment
is separate from the wheel installed with `uv tool install`:

```sh
uv venv --python 3.12
uv pip install --python .venv/bin/python -e '.[server,test]'
.venv/bin/pytest -q
.venv/bin/python tools/qualify_host.py --codex-bin /path/to/codex --output runs/NEW_QUALIFICATION
.venv/bin/python tools/qualify_host.py --codex-bin /path/to/codex --output runs/NEW_QUALIFICATION --apply
```

Enable the [Git pre-push portability check](docs/portability.md) in each checkout:

```sh
git config --local core.hooksPath .githooks
python3 tools/check_portability.py
```

Preserve any existing hooks before changing `core.hooksPath`. The check flags
personal paths, network addresses, private hostnames and accidentally tracked
state in outgoing commits. CI also checks the committed source tree.

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

## Distribution and releases

The Tests workflow builds an sdist, builds the wheel from that sdist, and uploads
`research-runner-COMMIT_SHA` containing the wheel, source archive, separate docs ZIP
and `SHA256SUMS`. Linux, macOS and Windows test that same installed wheel outside
the checkout. The Linux suite also runs the complete installed CLI against a
disposable HTTP service. Only a successful workflow should be consumed.

Pushing a `vVERSION` tag matching `pyproject.toml` publishes the exact tested assets
as a GitHub release after all checks pass. This does not publish to PyPI. Local
maintainers can reproduce the bundle with `python -m build` followed by
`python tools/package_distribution.py --dist dist` in a clean output directory.
The wheel provides `research-runner docs` and `docs --output DIRECTORY`, so its
consumer documentation remains available without visiting or cloning this repo.

## External asset dependencies and independent loaders

Payloads may declare v2 `dependencies` using `asset_type`, `asset_id` and
`destination`. Server operators register independently installed loaders; payloads
cannot install or select executable code. For private loader development in a
separate project, read `docs/asset-loaders.md` and `schemas/asset-loader.json`,
and use `examples/asset-loader/loader.py` as a standalone reference.
These files ship with the wheel and `research-runner docs --output DIRECTORY`.
Validate registration with `research-runner validate-loaders --config FILE`;
start the server with `serve --loader-config FILE`. Both timeout_seconds and
max_bytes are optional. Preparation streams progress and respects job stop/deadline.
