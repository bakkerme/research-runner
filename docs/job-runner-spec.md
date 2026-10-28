# Research Runner MVP contract (0.2.0)

The runner executes one job at a time on one host. Codex runs natively by
default. Workloads can use containers for dependency tools independently of agent
execution. Producers supply scope, instructions, inputs and acceptance criteria.
The runner checks protocol and file integrity; it does not establish the
correctness of workload conclusions.

## Supported scope

The runner provides deterministic packages, hash verification, idempotent
submission and launch, persistent events and deadlines, graceful finalization requests, voluntary
complete/partial/blocked outcomes, live checkpoint/artifact generations, bounded
HTTP monitoring, dashboard controls and verified remote collection.

Arbitrary runtime plugins, worker scheduling, external
worker brokers, provider retries, automatic continuation, domain validators,
budget extensions and distributed/concurrent execution are unsupported.
A completed Codex turn must produce closeout files; the MVP does not keep
prompting it.
`GET /api/capabilities` reports supported package versions, features and limits.

## Package

Use `research-package/v2` for persistent run budgets and structured outcomes.
`research-package/v3` is v2 plus optional `tool_images`, `required_checks` and
`provenance`. `research-package/v1` uses a per-attempt timeout and the `completed` state.
V2 includes a top-level `policy` object:

```json
{
  "finalization_seconds": 900,
  "termination_seconds": 30,
  "checkpoint_seconds": 30
}
```

Total wall-time allowance is `resources.time_seconds` (maximum 86400).
Require `0 < termination_seconds < finalization_seconds < time_seconds`.
Checkpoint interval must be 0.1..300 seconds. Policy is frozen at submission.
The other manifest fields are: `format`, `name`, `adapter`, `runtime`, `brief`,
`agents`, `skills`, `inputs`, `resources`, `required_outputs`. Adapters are
`codex` and deterministic `fixture`. The runtime SDK version is pinned. Native
payloads need no image fields. Containerized agents require a pinned `image` and
`image_id` pair. The [example manifest](../sample-mvp/manifest.json) targets native
execution.

Every package file must be declared. Brief, agent instructions, skills and inputs
are hashed in the `package.json` envelope. Plan, provenance inventory, previous
results and workload-specific context can be ordinary declared inputs; the MVP
imposes no domain plan schema or directory convention. Preserve their dependency
paths. Package input paths cannot be `package.json` or under `outputs/`.

Input limits: 512 archive entries, 8 GiB/file, 64 GiB total uncompressed bytes
including the envelope (maximum 1 MiB). Override input byte quotas with
`RESEARCH_RUNNER_INPUT_FILE_BYTES` and `RESEARCH_RUNNER_INPUT_TOTAL_BYTES` on the
producer and server. Both must be positive integers and total must be >= file.
Capabilities report `file_bytes`, `package_bytes` and `archive_bytes` (compressed
upload cap, including headroom for gzip/tar overhead).

Include research objects and dependencies as declared inputs. Assembly, upload,
verification and extraction stream contents with bounded memory; upload processing
runs outside the API event loop so monitoring stays available. Allow disk space
for the temporary upload, sealed archive and extracted inputs. Links, traversal,
duplicates, special files and undeclared payloads are rejected. Inputs exceeding
configured quotas require an explicit quota increase or an explicit scope decision;
undeclared dataset references are unsupported. Declared external assets use
the optional v2 `dependencies` array described in [asset loaders](asset-loaders.md). Preparation must report exclusions rather
than silently drop required dependencies. Credentials never belong in packages.

Operator-registered independent asset loaders resolve, retrieve and verify declared
dependencies on explicit start, before research execution. Their versioned protocol,
optional limits, progress events, registration and validation command are specified
in [the asset loader authoring guide](asset-loaders.md).

V2 and v3 require `outputs/result.json` and `outputs/handoff.md` in `required_outputs`.
List additional required outputs for full completion. Partial/blocked closeout
may omit those additional files, but must retain useful work and explain gaps.
When `required_checks` is non-empty, `outputs/research.json` is also required.
`complete` is rejected unless every listed id appears in that report's
`validation` array with status `passed`. The rejection names the check and the
status that was found, for example `complete requires passed check native-load (status not_run)`.
`partial` and `blocked` are accepted regardless of those statuses. The id
`tool-image-provisioned` is treated as passed when the tool-image receipt shows
every declared image was built or reused, even if the report omits it.
All regular files under `outputs/` are eligible for publication within output
limits of 512 files, 32 MiB/file and 128 MiB per generation. Capabilities report
these separately as `output_file_bytes` and `output_total_bytes`. Do not put
sensitive runtime state there.

## Worker files

Atomically replace JSON files (write a sibling temporary file, then rename).
Temporary files should be outside `outputs/` if they must not be published.

`outputs/checkpoint.json` is optional, but recommended after each meaningful
result and before/after operations with side effects:

```json
{
  "format": "runner-checkpoint/v1",
  "summary": "Work completed and current operation, including uncertainty",
  "next_action": "Exact next step or required input",
  "blockers": []
}
```

`outputs/result.json` is required at normal exit:

```json
{
  "format": "runner-result/v1",
  "outcome": "partial",
  "reason": "budget_exhausted",
  "summary": "Useful results delivered and what remains unfinished",
  "next_action": "How to continue",
  "blockers": []
}
```

Outcome/reason pairs: `complete`/`scope_complete`, `blocked`/`no_actionable_work`
(nonempty blockers required), or `partial` with `budget_exhausted` or
`user_requested`. A blocked item should not stop other independent actionable
work. When no allowed supported path remains, the worker can close out immediately
without filling its allowance. Do not repeat unchanged failures or redefine
success. The worker writes its own result and exits normally; no separate finish
RPC is needed in this MVP.

Write `outputs/handoff.md` describing results, limitations and next steps.
JSON contracts reject unknown fields and exceedances of 64 KiB, 8000-character
summary/next-action values or 100 blockers. JSON Schemas are available under
[schemas](../schemas/result.json), through `research-runner schemas`, and
`GET /api/schemas`. Cross-field policy/outcome constraints are additionally
validated in code. Workload evidence/metadata can use separate output files.

## Deadlines and stopping

The controller records first-start, soft finalization, forced termination and
hard deadlines in SQLite before launch. All wall time after first start counts,
including outages/approval waits; queued time does not. Resume cannot reset the
allowance. Expired jobs require a deliberate new submission with a new key.

At `start + budget - finalization_seconds`, enter `finalizing`, expire pending
approvals, reject steering and write `finalize.json` in the job control directory,
including the reason, attempt and hard deadline. The worker checks between
operations; the Codex adapter also attempts to steer the active turn with wrap-up instructions and declines new
approval requests. The dashboard's Finish up action requests the same transition
with `user_requested`. An acknowledgement means signal delivery, not proven task
completion. Final results must respect that reason or report no actionable work.

The finalization phase is cooperative: the MVP cannot mediate every model tool
invocation. Instructions require bounded operations and prohibit detached/external
work. The controller kills the worker process group (and agent container only
when explicitly using that execution mode) at
`start + budget - termination_seconds` if it has not exited. Cleanup starts within
that reserve; inability to confirm it is reported as failure, never completion.
Unresponsive workers cannot guarantee graceful handoff, so publish checkpoints
throughout the run. Immediate Stop skips cooperative closeout and retains partials.
The v2 worker also carries an independent hard-deadline failsafe, so a controller
outage cannot grant the worker indefinite runtime. It cannot produce a graceful
closeout when the model is unresponsive.

Execution states: `queued`, `running`, `finalizing`, `finished`, `interrupted`,
`failed`. V1 also uses `completed` and `waiting`. `finished` means a protocol-valid
normal closeout, not necessarily complete scope. Outcome claim is independently
`complete`, `partial`, `blocked`, or null for failed, interrupted or v1 jobs.
Validation is explicitly `not_run`; workload checks and review remain external.
A forced deadline produces `interrupted`, with no fabricated outcome.

Restart kills owned orphan workers before marking active attempts interrupted.
Resume creates a new process/turn under the same deadline and session. Original
artifacts remain published. Before a retry, the preceding terminal result is
retained outside the workspace to prevent accidental reuse. No side effect
should be replayed until the checkpoint and actual outputs have been reconciled.

## Monitoring, authorization and artifacts

| API | Purpose |
| --- | --- |
| `GET /api/capabilities`, `/api/schemas` | Discover exact supported interface. |
| `POST /api/jobs` | Submit raw `.rrpkg` bytes; Idempotency-Key required. |
| `POST /api/jobs/{id}/start` | Start/resume with optional `input`; idempotent. |
| `POST /api/jobs/{id}/finalize`, `/stop` | Cooperative wrap-up or immediate stop; idempotent. |
| `POST /api/jobs/{id}/input`, `/approve` | Existing steering/decision controls. |
| `GET /api/jobs/{id}/status` | State, outcome, remaining time, deadlines, pending decisions and current publication. |
| `GET /api/jobs/{id}/event-log?after=SEQ&limit=N` | Ordered replay; events, next_cursor, has_more. Limit 1..1000. `tail=true` selects recent events; `before=SEQ` pages backward with previous_cursor and has_previous. `activity=true` excludes per-file artifact events before pagination; raw replay remains the default. |
| `GET /api/jobs/{id}/events` | SSE; `after` and Last-Event-ID reconnect. |
| `GET /api/jobs/{id}/checkpoint` | Current published checkpoint and generation. |
| `GET /api/jobs/{id}/artifact-manifest?generation=N` | Select immutable publication; defaults to latest. |
| `GET /api/jobs/{id}/artifacts/{name}?generation=N` | Download selected file with X-Content-SHA256. |

Control bodies are JSON (use `{}` where no fields apply). Reuse the same key and
payload after uncertain responses. HTTP 400/422 means invalid input, 404 absent
job/artifact, 409 rejected control or integrity error, 413 oversized upload.
Authenticated mode also uses 401/403. Submission returns job ID, package digest
and monitor URLs; the client persists request identity before sending it.

Set distinct `RESEARCH_RUNNER_READ_TOKEN` and `RESEARCH_RUNNER_CONTROL_TOKEN` on
the server to enforce read/control separation. Configure both or neither.
Clients send Authorization Bearer using `RESEARCH_RUNNER_TOKEN`; dashboard sign-in
uses an HttpOnly SameSite cookie. Without tokens the trusted network has full
control. No public-interface authentication is assumed.
Read credentials cannot mutate jobs. Decisions expose the requested details and
are tied to exact attempt/turn/request identity; stale decisions expire.

A generation contains format, job ID, generation, attempt, created timestamp,
file names/sizes/SHA-256 and optional checkpoint. The host reads bounded regular
files without following symlinks, copies stable bytes to a hash-addressed store,
then commits events and publication together. Periodic publication runs at the
policy interval; unchanged bytes do not create a new generation. An invalid
intermediate publication leaves the preceding generation available. This is a
per-file integrity snapshot, not an atomic semantic snapshot of a changing tree.
Normal closeout is read only after the worker and owned execution have stopped.

Oversized Codex telemetry is abbreviated before entering the bounded worker log
stream. Events carry `runner_log.truncated` and their original serialized size;
large tool output includes a truncation marker. If a payload still exceeds the
limit, only event identity and lifecycle status are logged. This affects monitoring
history only: the agent's tool results and files remain unchanged, and execution
continues. Approval requests and other control messages are never abbreviated.

Status/event activity reports liveness, not semantic progress. Worker checkpoints
are claims; artifact hashes establish delivered bytes. Failed/interrupted runs
attempt a final partial publication without inventing result files. Collection
pins one generation, checks every hash/size, writes into a new directory, and
issues a verification receipt only after all files pass.

## Runtime boundary

Native Codex is the default. It uses the operator's installed CLI (or explicit
RESEARCH_RUNNER_CODEX_BIN), normal CODEX_HOME, credentials and persistent sessions.
The SDK retains its pin. Agent cwd is the independent job workspace; permissions
use workspace_write and the configured approval flow. Native execution provides
runner deadlines/process supervision, not Docker CPU/memory caps or immutable
input mounts. The capability/status API reports that distinction. Session records
remain in the host's Codex home; workload snapshots do not include them. Same-host
resume is supported; cross-host native-session migration is not advertised.

A v3 package may declare `tool_images`. Each entry names a context directory, a
Containerfile inside that context, a `linux/<arch>` platform, the sha256 of each
build input, an optional https `fetch`, a timeout, and a tag. The tag must be
`localhost/rr-tool/<name>:in-<first 16 hex of input_hash>`. `input_hash` is the
sha256 of the canonical JSON object `{containerfile, context, platform, inputs,
fetch}` with sorted keys; `fetch` contributes only its sha256 values, in order,
and is omitted when empty. Pins named `image_id` are rejected inside
`tool_images`. Paths must be relative package inputs, and the Containerfile must
sit inside the context.

On start, after any asset preparation and before the worker, the runner inspects
that tag with the container CLI (`docker`, or `RESEARCH_RUNNER_CONTAINER_CLI`).
`DOCKER_HOST` is the service environment. A present image whose
`research.input_hash` label matches is reused. Otherwise the context is copied
to `jobs/<id>/tool-build/<name>/`, fetches are sha256-checked, and
`docker build --platform <platform> --label research.input_hash=<hash> -t <tag>`
runs as an argument list in its own process group. The build is killed on
timeout, stop, or service restart. Progress is streamed as `tool_image_progress`.
The image architecture must match the platform. Any failure finishes the job as
`failed` with `tool image <name>: <reason>` before a worker or Codex turn starts.
A stop during the build finishes as `interrupted` and kills the process group.
Measured provisioning time is added to the deadline, finalization time and
termination time, so the build does not consume `time_seconds`. This is not a
general budget extension.

The receipt `jobs/<id>/assets/tool-images.json` (`runner-tool-images/v1`) records
each image's tag, id, architecture, input hash, `built` or `reused` action,
seconds and `built_at`. It is exposed read-only at `work/.runner/tool-images.json`
and published as `outputs/tool-images.json`. The [v3 example](../examples/tool-image/)
is a generic image declaration. [sample-mvp](../sample-mvp/manifest.json) remains
a v2 package and does not declare tool images.

Other Docker/Podman dependency tools are still supplied and managed by the
workload. Their image pins, assets, mounts, ownership, timeouts and cleanup
belong in its brief/tooling. The agent execution engine does not automatically
own those tool containers. They must remain bounded and recoverable if the agent
is interrupted. No emulator-specific behavior is built into the runner.

A separate explicit --engine docker mode can containerize the agent itself. That
alternative requires an image pin and separate credential provisioning; its
restrictions are not requirements for ordinary payloads. Native mode needs no
credential projection or Docker installation. Once a job starts, its execution
engine is recorded and cannot silently change on resume. Jobs whose execution
engine is unspecified must resume with their original runtime and session store.

The Codex adapter uses the SDK's streaming/steering interface; see the
[official app-server documentation](https://learn.chatgpt.com/docs/app-server).
Use `tools/qualify_host.py` to verify native recovery and finalization on the
execution host with its configured Codex executable and account.

## Repo producer and structured research extension (0.3)

The installed producer library/CLI, scaffolding, durable local handles and
recoverable collection behavior are documented in [client-workflow.md](client-workflow.md).
They use the existing v2 package and HTTP operations; no new submission endpoint
or server-side automatic continuation is introduced. Server dependencies are
installed with the `server` extra.

`outputs/research.json`, when present at normal v2 or v3 closeout, must satisfy
[`runner-research/v1`](../schemas/research.json). Its claims reference unique
evidence IDs; checks preserve methods, expected/observed results and explicit
passed/failed/inconclusive/not_run statuses. Evidence/deliverable paths must be
published output files; supplied evidence hashes must match. The server validates
structure and artifact linkage, while the workload performs scientific validation.
New scaffolds require this report and tell the agent that `required_checks`, when
listed, gate outcome `complete`. Existing v2 packages without the report remain valid.
Partial publications remain available even if a report is unfinished or invalid.

`features.structured_research` advertises this extension. Existing status
`validation: not_run` continues to describe independent Runner validation, not
whether the researcher performed checks recorded in the research report.
