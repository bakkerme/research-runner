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

## Portability and pushes

Keep the shared project independent of any operator's account, workstation,
private network or workload. Use configurable addresses, home-relative paths,
and generic examples. Canonical project release URLs are public distribution
coordinates and should remain real. Tailscale is an optional bind mode;
the default server binds to localhost.

Install the repository's Git pre-push hook with
`git config --local core.hooksPath .githooks` (preserve any existing hook setup).
Before pushing, run `python3 tools/check_portability.py` and review the changes
for local paths, addresses, private hostnames, credentials and assumptions about
installed tools or hardware. The hook also scans outgoing committed trees,
including intermediate commits. Do not bypass a failure or add broad exclusions.
See [the portability guide](docs/portability.md) for local private markers,
installation and the check's limits.

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
