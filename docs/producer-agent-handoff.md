# Producer-agent MVP handoff

Use the [implemented MVP contract](job-runner-spec.md) and
[example package](../sample-mvp/manifest.json). Keep project-specific discovery,
context selection and acceptance checks in your workload; the runner is generic.

1. Select authoritative existing work and original inputs. Record source hashes,
   provenance, exclusions, prior claims and checks actually performed. Resolve
   conflicts explicitly; do not replace active work with templates. Preserve
   dependency-relative paths and keep credentials out of portable files.
2. Create a fresh source directory with a v2 manifest, brief, AGENTS.md, optional
   skills and declared inputs. A project-specific plan or inventory is an ordinary
   input; no new runner adapter is necessary. Select the codex adapter for real
   model work. Codex runs on the host; dependency containers are optional workload
   tools and do not require containerizing the agent. The fixture adapter is only for protocol rehearsal.
3. Define bounded scope, success, allowed tools and evidence requirements in the
   brief. Optional dependency containers need workload-owned image pins,
   ownership, deadlines and cleanup. Choose total time and finalization/termination reserves. Require useful
   partials, checkpoints, exact blockers and early finish when no path remains.
4. Declare result.json and handoff.md plus outputs required for complete scope.
   Follow the checkpoint/result schemas. Workload conclusions remain worker
   claims; include your own check reports and limitations rather than implying
   the runner independently validated them.
5. Native runtime needs only the SDK pin; image/image_id apply only to an
   explicitly containerized agent. Inspect server capabilities; stay within package limits. Large external dataset
   references and external workers are deferred. Report missing dependencies or
   capabilities instead of silently packaging an incomplete workload.
6. Assemble, verify, preview and apply submission using the generic remote CLI.
   Persist the receipt and reuse the same key on uncertain responses. Start
   separately under existing authorization. Monitor through read-only HTTP calls;
   never open the server's state directory from a second process.
7. Collect a selected artifact generation to a fresh directory and verify it.
   Preserve complete, partial and blocked outcomes separately from execution
   failures. Production integration or deployment is a separate action.

Repeatable client sequence (substitute destination, paths, job ID and keys):

```sh
research-runner assemble SOURCE job.rrpkg
research-runner verify job.rrpkg
research-runner --url URL capabilities
research-runner --url URL submit job.rrpkg --key SUBMISSION_KEY --receipt receipt.json
research-runner --url URL submit job.rrpkg --key SUBMISSION_KEY --receipt receipt.json --apply
research-runner --url URL start JOB_ID --key START_KEY --apply
research-runner --url URL status JOB_ID
research-runner --url URL events JOB_ID --after 0 --limit 100
research-runner --url URL collect JOB_ID NEW_DIRECTORY
research-runner --url URL collect JOB_ID NEW_DIRECTORY --apply
```

Set RESEARCH_RUNNER_TOKEN privately if server auth is configured. Receipt files
contain identity, not credentials. A lost submission response can be reconciled
with the same key and receipt. Launch uses the caller's persisted START_KEY;
reusing it is safe and does not grant another attempt. A deliberate retry after
interruption uses a new start key under the same original deadline.

Return package digest, source coverage, blockers, submission receipt/job ID if
submitted, monitoring commands and validation bounds. Do not create schedules,
send messages or launch work merely because this handoff was supplied. Existing
user authorization determines which steps to apply.
