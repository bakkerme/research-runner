# Research Runner: guidance for project agents

If your task is to add remote research to another project, work in that project's
repo. Install Research Runner's distributed wheel; **do not clone this repo** or
add it as a sibling checkout/submodule to use the client. Read
[the agent quickstart](docs/agent-quickstart.md) for release/CI artifact downloads,
installation, library integration and research acceptance criteria.

The installed CLI provides `research-runner docs` before setup and `init` installs
the bundled skill into the consuming repo's `.agents/skills/research-runner/`.
`init` also exports the full docs and schemas into ignored `.research-runner/docs/`.
Use that skill and the installed library to package, submit, monitor and collect.
Choose a pinned tested version; keep Runner URLs configurable and tokens in the
environment. Monitoring requests do not authorize starting/resuming/steering jobs.

If the task is to develop Research Runner itself, edit its source here. Producer
imports must not load server dependencies. Build wheel and sdist using
`python -m build`, then `python tools/package_distribution.py --dist dist`.
Both distributions must carry the current docs, schemas and skill. The installed
client tests must run from outside this checkout without server dependencies.
Server tests use `python -m pip install -e '.[server,test]'` and `pytest -q`.
Preserve existing working changes, private state, generated packages and receipts;
never commit workload inputs, credentials or generated research outputs.
