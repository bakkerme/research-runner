# Research from a consuming repo

Research Runner 0.3 provides a producer library and CLI in the same install.
Producers need Python 3.11+ and Pydantic. The server's Codex SDK, FastAPI,
Uvicorn, Unix process supervision and container tools are optional server concerns.
The client works without a Runner source checkout, local Codex login or service.

## Install and scaffold

Use the [agent quickstart](agent-quickstart.md) to install a pinned GitHub release
wheel or download a successful CI run artifact without cloning Runner. A local
wheel can also be installed directly:

```sh
python -m pip install /path/to/research_runner-0.4.0-py3-none-any.whl
research-runner docs
research-runner init --url http://RUNNER:8765 --name experiment
```

The wheel includes the full maintained docs, schemas and skill. `init` exports
them into ignored `.research-runner/docs/`; `docs --output DIRECTORY` can export
them independently before a project is initialized. `docs` alone prints the agent
quickstart. The GitHub CI bundle also contains a separate documentation ZIP.
For developing the service, use `python -m pip install -e '.[server,test]'`.

Initialization creates:

```text
research-runner.toml                      # URL and configuration version
research/experiment/manifest.json             # scope name, runtime, resources, outputs
research/experiment/brief.md
research/experiment/AGENTS.md                 # remote research instructions
research/experiment/inputs/question.md        # question and acceptance criteria
research/experiment/inputs/contracts.json     # exact bundled result schemas
.agents/skills/research-runner/SKILL.md    # agent workflow skill
.gitignore                               # adds /.research-runner/
```

Existing configuration, definitions and modified skills are never overwritten.
`init --no-skill` skips the skill. `install-skill` installs it later. `new NAME`
adds another definition. Commands discover the configuration from the current
directory or its parents; `--repo PATH` selects another consuming repo.
Keep the definitions/configuration/skill in version control. Keep generated job
state, packages, receipts and results in the ignored `.research-runner/` directory.
Set `RESEARCH_RUNNER_TOKEN` in the environment when authentication is enabled;
credentials are not written into repo configuration or payloads.

## Package, submit and monitor

Edit the question, brief and acceptance criteria before submitting. Put all
required files and dependencies under the definition directory, preserving paths.
`package` automatically declares every file under that directory as a payload
input except the root manifest and the declared brief/agent/skill files. It never
uploads the surrounding repo. Inspect inputs before submission; credentials and
unrelated private material must not be placed inside a research definition.
Links and special files are rejected. The existing package quotas and strict
assembler/verifier still apply. Missing or oversized dependencies must be reported
rather than silently omitted.

```sh
research-runner package experiment
# package prints its archive path and SHA-256; verify PATH prints the inventory
research-runner submit experiment                 # preview; local packaging only
research-runner submit experiment --apply         # upload; does not start execution
research-runner start experiment --apply
research-runner status experiment
research-runner events experiment --after 0 --limit 100
research-runner monitor experiment --timeout 60
research-runner monitor experiment --json         # NDJSON for integrations
research-runner pull experiment                   # preview selected generation
research-runner pull experiment --apply
research-runner results experiment
```

Run names default to definition names. Use `submit experiment --run experiment-second
--apply` for a separate experiment, then address that run name for start/status/
monitor/pull. A run pins its server, package hash and durable submission/control
identities before sending requests. Repeat the same submit/start command after
an uncertain response. Changed package bytes require a fresh run name. Retain
local state for recovery. Deliberate control operations can supply a new `--key`;
a fresh start key may resume an eligible interrupted/failed job under its original
deadline, so this is an explicit operator action, never monitoring behavior.

`status`, `events` and `monitor` send only GETs. Monitoring pages through events,
remembers its local cursor and stops on terminal/interrupted states, pending
approval, timeout or Ctrl-C. `--after 0` explicitly replays history. Human output
summarizes events; `--json` includes all events. No monitor automatically approves,
steers, resumes or starts work. The server currently permits one running job.

A dashboard submission can join the same workflow:

```sh
research-runner attach experiment-dashboard REMOTE_JOB_ID
research-runner status experiment-dashboard
research-runner pull experiment-dashboard --apply
```

The existing low-level commands remain available with explicit `--url`, archive
paths, keys and receipts. A dashboard's copied collection command uses the
installed `research-runner` executable from any project directory.

## Recoverable collection

Repo pulls select an immutable generation and write into
`.research-runner/collections/RUN/gGENERATION.partial`. The selection is persisted
before artifact downloads. Each file is hash-checked before replacement. A
successful collection gets a verified receipt and is renamed into `gGENERATION`.
Progress includes verified/reused file and byte counts.

Repeat `pull RUN --apply` after failure: it retains the original generation even
if a newer publication appeared, verifies local files, reuses matching files and
retries missing/corrupt files. Recovery works between files; an interrupted file
is downloaded again, with a maximum published-file size of 32 MiB. No background
retry loop is started. Complete the pending pull before selecting a different
generation. After completion, another pull selects the latest publication;
`--generation N` selects an older one explicitly. Completed collections are
rechecked and never overwritten if modified. Process locks prevent concurrent
writers; interrupted processes release their locks automatically.

The low-level `collect JOB DEST --apply` has the same recovery semantics with
`DEST.partial`. Repeating the same destination keeps its selected generation;
use a fresh destination to collect another generation. Local receipts and staging
directories must remain together. Publication identity, paths, sizes and hashes
are validated before downloads. Symlinks and nonportable artifact paths are
rejected. Deleting local state discards recovery identity; it is not a retry tool.

## Structured research and validation

New definitions require three closeout files:

- `outputs/result.json`: existing `runner-result/v1` outcome and next action.
- `outputs/handoff.md`: readable handoff with scope and practical limits.
- `outputs/research.json`: `runner-research/v1`, defined in the bundled schemas.

The research report contains findings linked to evidence IDs; evidence paths and
optional hashes; validation methods with expected/observed results and
passed/failed/inconclusive/not_run statuses; reproduction commands and dependency
hashes/provenance; deliverables; and limitations. It requires at least one
validation entry, including an explicit `not_run` explanation when work is blocked.
Passed validation requires retained evidence. Evidence and deliverable paths must
refer to published `outputs/` files. Research-side validation is part of the work;
failed checks and uncertainty remain visible instead of being converted into a
single success badge.

On a 0.3 server, malformed research reports or missing referenced artifacts cannot
produce a successful normal closeout. Older v2 packages remain valid without a
research report. `capabilities` advertises `structured_research` support; a newer
producer can still send v2 payloads to an older server, but its report validation
then happens locally through `results` rather than being enforced by that server.
Failed/interrupted runs can still publish incomplete or malformed diagnostics.
`pull` verifies their bytes; `results` reports a schema error for malformed reports.

`results` rechecks collection hashes and returns the researcher-authored outcome
and report alongside `delivery_verified: true` and
`independent_validation: "not_run"`. The latter describes this inspection operation,
not the validation already performed by the researcher. Returned reproduction
commands are data and are never executed by inspection or collection. Independent
review/local acceptance can be implemented by the consuming repo where appropriate.

## Library API

```python
from runner import ResearchRepo

repo = ResearchRepo("/path/to/consumer")
package = repo.package("experiment")
receipt = repo.submit("experiment", run="experiment-second", apply=True)
repo.start("experiment-second", apply=True)
for update in repo.monitor("experiment-second", timeout=60):
    print(update)
collection = repo.pull("experiment-second", apply=True, progress=print)
results = repo.results("experiment-second")
```

`ResearchRepo.init(path, url, name)` provides the same scaffolding. `Client(url,
token=None)` remains the lower-level transport API for capabilities, status, paged
events, monitoring, submission, controls and collection. Both APIs keep remote
mutations explicit with `apply=True`; operation identities in raw `Client` calls
remain the caller's responsibility. `ResearchRepo.attach(name, job_id)` supports
dashboard-created jobs. Tokens can be passed to either constructor without being
persisted.
