---
name: research-runner
description: Prepare, submit, monitor and collect bounded remote research from a repo configured with Research Runner. Use for research-runner.toml projects and Runner job handoffs.
---

# Research Runner

Use the installed `research-runner` CLI or `from runner import ResearchRepo`.
For setup from a repository URL, use a pinned GitHub release wheel or a successful
CI artifact as described by `research-runner docs`. Do not clone Runner to use it.
Full docs and schemas are exported by `init` into `.research-runner/docs/`, or by
`research-runner docs --output DIRECTORY` before setup. Use the CLI by default;
use a small project wrapper around ResearchRepo for product integration.
No Runner checkout, local server, Codex login or container engine is required on
the producer. Read `research-runner.toml` and the chosen `research/NAME/` definition.
Credentials come from `RESEARCH_RUNNER_TOKEN`; never put them into payloads.

`research-runner init --url URL --name NAME` scaffolds a repo; `new NAME` adds a
definition. Edit its question, scope, acceptance criteria, brief and manifest.
Put required objects, tools, provenance and dependencies inside that definition.
`package NAME` packages all its files and verifies the archive. It does not upload
the surrounding repo. Links are rejected. Review the input inventory with
`verify ARCHIVE`; keep secrets and unrelated private files out of the definition.

Submit with `submit NAME --apply`. Use `--run NEW-NAME` for a separate experiment.
Then `start RUN --apply`. Without `--apply`, these commands preview. Submission
does not start execution. Durable identities live in ignored `.research-runner/`;
retry the same run after an uncertain response. Changed package bytes require a
new run name. Control keys are reused; a deliberate new attempt needs an explicit
fresh `--key` and task authorization. Never infer permission to resume, stop,
finalize, approve or steer from a monitoring request.

`status RUN`, `events RUN` and `monitor RUN --timeout 60` use GET requests only.
Monitoring stops at terminal/interrupted states or a pending approval; report the
condition without acting on it. `attach RUN JOB_ID` names an existing dashboard
job locally using a status GET.

`pull RUN --apply` selects an immutable generation, verifies every file and
publishes a fresh local collection. Repeating an interrupted pull resumes the
same generation. Preserve `.partial` directories and local receipts. The CLI
reports the collection directory; `results RUN` verifies it again and reads the
structured result without executing returned code.

New definitions require `outputs/result.json`, `outputs/handoff.md` and
`outputs/research.json`. Their exact schemas are bundled into the payload under
`inputs/contracts.json`. Research reports link claims to evidence IDs, preserve
passed/failed/inconclusive/not_run checks with methods and expected/observed
results, and include reproduction steps, dependency hashes and limitations.
Research-side validation is part of the work. A collection hash verifies delivered
bytes; report the research's validation separately and qualify anything not
independently reproduced. Run returned commands only when within the user's
authorized scope, after reviewing their inputs and effects.
