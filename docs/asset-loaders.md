# Independent asset loaders

This is the public authoring contract for private, independently deployed loaders.
An agent building a loader should read this entire document and
`schemas/asset-loader.json`, then start from `examples/asset-loader/loader.py`.
The example uses only the Python standard library and imports nothing from Runner.
You can maintain your loader in another repository, language and virtual environment.
Do not put private loader code, credentials, internal catalogues or downloaded data
into Research Runner's repository or research payloads.

## Instructions for a loader-building agent

Work in the separate loader project. Obtain the required asset type, supported
asset IDs, source access method, desired file layout and deployment location from
the operator. Implement the three operations below and stream safe progress while
retrieving large assets. Package all implementation dependencies with your loader,
not with Runner. The protocol is the integration boundary; no Runner installation
or server imports are required to implement it.

Deliver the loader, its own installation instructions and tests, an example server
registration with absolute executable paths, and an example payload dependency.
Test describe/resolve/materialize as fresh processes through stdin/stdout. Cover
unknown IDs, denied access, changed source revisions, interrupted retrieval,
progress, hash verification, unsafe archive entries and accidental secret disclosure.
Use fixture data first; report actual private-source checks separately. Do not
claim live source access was verified unless it was. A useful prompt is:

> Read docs/asset-loaders.md and schemas/asset-loader.json. In a separate project,
> build a loader for asset type TYPE that retrieves DATA from SOURCE. Materialize
> the files in LAYOUT. Keep source access private, emit download progress, and
> deliver installation, tests, registration TOML and an example dependency.

## Payload declaration

The optional `dependencies` array in a research-package/v2 manifest contains only:

```json
{
  "dependencies": [
    {
      "asset_type": "local-files",
      "asset_id": "reference-data/2026-10-20",
      "destination": "inputs/reference-data"
    }
  ]
}
```

Types match `[a-z][a-z0-9-]{0,63}`. An ID is a nonempty opaque string of at most
2000 characters. The loader interprets it; it is not an executable or an arbitrary
host path. The destination is a normalized relative POSIX path to a directory.
To deliver one file, create that file inside the staging directory; it will appear
under the destination directory. This v1 protocol always materializes directories.

Destinations cannot overlap each other, packaged files, `manifest.json`,
`package.json` or `outputs/`. The producer still packages every ordinary input;
asset contents are supplied later by the server. Up to 100 dependencies are allowed.
Older v2 manifests without dependencies remain valid. Clients check the server's
`features.asset_dependencies` before submitting a package that needs assets.
The server capabilities list registered `asset_types`, without commands or secrets.

## Installation and registration

Install a loader separately on the Unix Runner host. Save an operator-owned TOML
file outside payloads, for example `/etc/research-runner/loaders.toml`:

```toml
[asset_loaders.local-files]
command = ["/opt/private-assets/.venv/bin/python", "/opt/private-assets/loader.py", "--catalog", "/etc/research-runner/catalog.json"]
# Both are optional. Omission means no loader-specific cap.
# timeout_seconds = 300
# max_bytes = 10737418240
```

The executable must be an absolute, executable file path. Fixed arguments are
allowed. Commands are executed directly without a shell; payload IDs are JSON
values on stdin, never substituted into arguments. Only registered types can run.
A single executable may be registered under several asset types if it supports them.
Unknown configuration fields are rejected. Provided limits must be positive;
max_bytes is an integer. timeout_seconds applies separately to each operation.
max_bytes applies per asset to the total regular-file bytes; Runner checks it during
retrieval approximately once a second and verifies it before publication. This is
not an OS disk quota and a fast writer can temporarily exceed it.

Validate and start the server with the same file:

```sh
research-runner validate-loaders --config /etc/research-runner/loaders.toml
research-runner --state /srv/research-runner serve --loader-config /etc/research-runner/loaders.toml
```

Validation checks registration syntax, executable availability and each loader's
`describe` handshake. It does not fetch assets or prove source access. A handshake
has a 10-second ceiling even when the operation timeout is omitted. Restart the
server after changing registrations. Registration does not install or download code.
Do not edit a currently running loader installation in place; deploy versioned
installations and update the registration when restarting the server.

## Transport and operations

Protocol name: `runner-asset-loader/v1`. Runner starts a fresh process for each
operation. Read one UTF-8 JSON object followed by a newline from stdin. Emit UTF-8
NDJSON on stdout, flushing each line. Every request and output line is limited to
64 KiB, including its newline. Stdout contains protocol events only. A successful
operation emits zero or more progress events, exactly one result, then exits zero.
Do not emit events after the result. Errors exit nonzero. Stderr is discarded by
Runner; log safe diagnostics privately if needed.

### Describe

Request:

```json
{"protocol":"runner-asset-loader/v1","operation":"describe","asset_type":"local-files"}
```

Response:

```json
{"event":"result","protocol":"runner-asset-loader/v1","loader_version":"1.0.0","operations":["resolve","materialize"]}
```

Describe must be quick and avoid source downloads. It identifies the loader release
(nonempty string, at most 100 characters) and supported operations.

### Resolve

Request:

```json
{"protocol":"runner-asset-loader/v1","operation":"resolve","asset_type":"local-files","asset_id":"reference-data/2026-10-20"}
```

Response:

```json
{"event":"result","protocol":"runner-asset-loader/v1","resolved_id":"reference-data/revision-184","sha256":"<64 lowercase hex characters>"}
```

Resolve an alias to an immutable revision and an independently expected inventory
digest. resolved_id is nonempty and at most 2000 characters. Obtain hashes from a
trusted catalogue or inspect the source. If a remote source does not provide an
inventory digest, the loader may have to download/inspect it during resolve and
retain a private cache for materialize. Emit progress during this work too.
Do not return credentials, signed URLs, private host paths or opaque secret tokens
in resolved IDs: IDs, digests, loader versions and progress are published to callers.

Runner persists each resolution before resolving the next dependency. Once locked,
it never resolves that asset's alias again on a retry. Your materializer must honor
resolved_id and sha256, or fail if that historical revision is unavailable.
Runner also requires the recorded loader version when it must retrieve a locked
asset again; retained verified content can be reused without invoking the loader.

### Materialize

Request:

```json
{"protocol":"runner-asset-loader/v1","operation":"materialize","asset_type":"local-files","asset_id":"reference-data/2026-10-20","resolved_id":"reference-data/revision-184","sha256":"<64 lowercase hex characters>","staging_directory":"/absolute/runner/owned/staging-directory"}
```

Write the requested content inside that existing, empty directory. Do not write
elsewhere in the job, follow source/archive links, create special files or spawn
background processes. The loader must complete and exit before Runner verifies it.
Never run installer hooks or downloaded code implicitly during retrieval.

Response:

```json
{"event":"result","protocol":"runner-asset-loader/v1","materialized":true}
```

Runner rejects unsafe content, independently computes the inventory digest, and
publishes the destination only after verification. Content and provenance are kept
outside the workspace for retries; an unchanged retained asset needs no source
connection. Resuming with modified workspace asset content fails rather than
silently replacing research inputs. To change the research inputs, submit a new job.

### Progress and errors

During either resolve or materialize, emit messages such as:

```json
{"event":"progress","message":"Downloading model weights","bytes_completed":1048576,"bytes_total":8589934592}
```

message is required, 1..1000 characters. Byte counts are optional nonnegative
integers; completed cannot exceed total when both are present. Omit total for an
unknown length. Never put credentials, private URLs or sensitive source metadata
in messages. Prefer updates approximately once a second; Runner persists at most
two loader progress events a second per operation and adds a five-second activity
heartbeat if the loader is quiet. These appear as `asset_progress` events in the
API, dashboard Activity and CLI monitor, with asset ID and destination. Verification
and workspace copying also emit periodic activity. A heartbeat confirms Runner is
waiting; it does not prove the source is making progress.

On failure, optionally emit this and exit nonzero:

```json
{"event":"error","code":"unavailable"}
```

Allowed codes: `not_found`, `access_denied`, `unavailable`, `invalid_request`.
Runner suppresses raw exception and stderr details from loaders. Other malformed
or unsupported protocol output fails preparation.

## Canonical inventory digest

SHA-256 here identifies the materialized directory tree, not an archive download.
For every descendant directory, including empty ones, record:

```json
{"path":"subdirectory","kind":"directory"}
```

For every descendant regular file, record:

```json
{"path":"subdirectory/file.bin","kind":"file","size":123,"sha256":"<file SHA-256>","executable":false}
```

Paths are normalized relative POSIX paths; no absolute paths, dot/traversal segments,
links, hardlinks or special files. The root directory itself is excluded. File size
is a byte count and executable is true if any POSIX execute bit is set. Ownership,
read/write permissions and timestamps are excluded. The protocol supports at most
10000 entries per asset. Sort entries by path using Unicode codepoint order, serialize
with sorted object keys, ASCII escaping, no whitespace and no trailing newline,
then hash those ASCII bytes. In Python:

```python
encoded = json.dumps(entries, sort_keys=True, separators=(',', ':'), ensure_ascii=True).encode('ascii')
digest = hashlib.sha256(encoded).hexdigest()
```

An empty tree hashes the two bytes `[]`. The standalone example implements the
same algorithm without importing Runner. For archive sources, safely extract into
a private temporary tree first; an archive hash is a separate transport check.

## Lifecycle, credentials and limits

Submission/monitoring never invokes loaders. Explicit start begins preparation
asynchronously, reserves the single execution slot and starts the existing job
budget. Status remains running (or finalizing), and activity identifies preparation.
Stop cancels preparation and terminates the loader process group. The job's hard
execution cutoff applies even without timeout_seconds. Failed preparation never
launches the research worker. A retry uses the existing budget and locked inputs.
A controller restart kills a recorded loader process before reconciling the job.
Preparation does not automatically retry source requests or start/resume research.

Runner retains a controller-owned receipt and publishes it as
`outputs/dependencies.json` alongside research artifacts. That name is reserved
when dependencies exist; a worker-authored replacement is ignored. The receipt
records requested and resolved IDs, resolver version, verified inventory, bytes and
hash. Keep private metadata out of these fields. Receipts establish input identity,
not correctness or safety of executable content. Retention follows the job's state;
there is no shared cache, eviction policy or cross-job access bypass in this version.
Allow disk for private retained content, staging and the workspace copy.

Loader processes receive only PATH, HOME, LANG, SYSTEMROOT and TMPDIR from the
server environment when present. Provision credentials through loader-owned files,
a credential service or a fixed private wrapper; do not place secrets in command
arguments or payloads. Runner does not inject loader secrets into the research
worker. The server's existing authentication model applies: control-token callers
(or all callers in trusted-network mode) can request registered types. Per-project
or per-asset authorization is not implemented; enforce additional restrictions in
your deployment/loader service before exposing sensitive assets to such callers.

A separate process is not a security sandbox. A native research worker running
under the same OS account may access the same files and network. Enforce strict
credential separation through a separate identity or service if required. Docker
execution mounts prepared asset directories read-only; native execution does not
enforce immutable mounts. Private data materialized into the workspace may be
read by the agent and appear in model requests or outputs. Source confidentiality
requires a narrower service interface if the agent must not see the underlying data.
