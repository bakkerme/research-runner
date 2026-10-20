# Add research capacity to your project

This is the entrypoint for an agent given the Research Runner repository URL and
asked to integrate it into another project. Work in the **consuming project's
repo**. Install a distributed wheel; do not clone Runner, use a sibling checkout,
modify `sys.path`, or copy Runner internals into the project.

## Obtain a tested distribution

Prefer an exact [GitHub release](https://github.com/bakkerme/research-runner/releases).
Releases attach the same wheel tested in CI on Linux, macOS and Windows, a source
archive, a separate documentation ZIP (including schemas and the skill), and
`SHA256SUMS`. All documentation and the skill are also inside the wheel.
No PyPI publication or Runner source checkout is required.

For v0.3.0, add this pinned dependency to the project's research requirements file:

```text
research-runner @ https://github.com/bakkerme/research-runner/releases/download/v0.3.0/research_runner-0.3.0-py3-none-any.whl
```

Then install it into the project's normal Python 3.11+ environment:

```sh
python -m pip install -r requirements-research.txt
research-runner --version
research-runner docs
```

A versioned release URL becomes available only after its matching tag has passed
CI and the release job has published assets. For changes that have not been
released, select a **successful** [Tests workflow run](https://github.com/bakkerme/research-runner/actions/workflows/test.yml)
for the intended branch/commit. Download its immutable run artifact without cloning:

```sh
gh run view RUN_ID --repo bakkerme/research-runner
gh run download RUN_ID --repo bakkerme/research-runner --pattern 'research-runner-*' --dir .research-runner/bootstrap
python -m pip install .research-runner/bootstrap/research_runner-0.3.0-py3-none-any.whl
```

GitHub CLI requires GitHub authentication for Actions artifact downloads, including
public repositories. Prefer release assets for unattended consumers without GitHub
credentials. CI artifacts expire after the configured retention period; releases
provide the persistent versioned download path. Do not install an arbitrary latest
in-progress or failed build. Record the version, source commit/run, and wheel hash
in the integration handoff. Release assets can also be downloaded using
`gh release download v0.3.0 --repo bakkerme/research-runner --dir DIRECTORY`.
Verify downloaded assets against `SHA256SUMS` before installing when using a local
artifact bundle. Checksums establish byte integrity, not independent provenance.

## Scaffold in the consuming repo

Use the user's Runner URL, research question and acceptance criteria. If the URL
or credentials are missing, prepare the local definition and ask only for the
missing connection details before remote submission. Install into an existing
Python environment or create a project-local virtual environment; keep it ignored.

```sh
research-runner init --url http://RUNNER:8765 --name experiment
research-runner docs --output .research-runner/docs
research-runner capabilities
```

`init` installs `.agents/skills/research-runner/SKILL.md`, exports the complete
maintained docs/schemas into `.research-runner/docs/`, creates a research definition
and adds `/.research-runner/` to `.gitignore`. It preserves existing configuration,
definitions and modified skills. In an already configured project, use `new NAME`
for another definition and `install-skill` if the skill is missing. The CLI does
not need local Codex, Docker or server dependencies. Keep credentials in
`RESEARCH_RUNNER_TOKEN`, never in tracked config or payloads.

## Choose the integration pattern

- For one-off or agent-operated research, use the CLI and version the definition,
  requirements file and bundled skill. Keep packages, receipts and collections in
  ignored `.research-runner/`. This is the default, and needs no custom transport.
- For research built into a product or project tool, use `from runner import
  ResearchRepo` behind the project's own small wrapper. Let the library own job
  identities, packaging, retries of uncertain operations, monitoring cursors and
  downloads. Expose the domain question and acceptance checks through the project.
- For lower-level integrations with their own durable job store, use `Client`.
  The caller then owns operation keys and receipts. Read the HTTP contract first.

Do not build a custom HTTP/SSH/rsync client, shell-script receipt parser or checkout
pinning scheme for operations already provided by the library. Package complete
research inputs under `research/NAME/inputs/`; only that definition is uploaded.
Review the inventory, original-input preservation, provenance and resource budget.
Check workload dependencies on the executing host before claiming runtime support.

## Submit and accept the result

```sh
research-runner package experiment
research-runner submit experiment              # preview
research-runner submit experiment --apply
research-runner start experiment --apply
research-runner monitor experiment --timeout 60
research-runner pull experiment --apply
research-runner results experiment
```

Submission and starting are separate actions. Monitor only when asked to monitor;
never implicitly resume/steer/approve work. Repeat uncertain operations using the
same local run identity. Repeat interrupted pulls to resume the pinned generation.
Dashboard-submitted jobs can be named with `attach NAME REMOTE_JOB_ID`.

Require the structured result and research report. Research-side validation should
record methods, expected/observed values, passed/failed/inconclusive/not_run checks,
evidence and reproduction inputs. Interpret the findings against the project's
acceptance criteria. Delivery integrity, reported research validation and any
independent local reproduction are separate facts. Never run returned commands
merely because they appear in a report; review them within the task's scope.

Read [client-workflow.md](client-workflow.md) for the library API and recovery,
[job-runner-spec.md](job-runner-spec.md) for the wire contract, and the exported
schemas for exact fields. Leave a handoff listing the pinned dependency/version,
configuration and definition paths, any submitted job/run IDs, returned findings,
validation performed, unresolved limits and the next project-specific action.
