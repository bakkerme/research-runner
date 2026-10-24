# Keep the shared project portable

Research Runner's core contract is workload-neutral. Operator paths, hostnames,
network addresses, tokens, hardware choices and private loader implementations
belong in local configuration or workload packages. Use environment variables,
CLI arguments, `~`, `$HOME`, or systemd's `%h` rather than a real account's home.
Keep canonical public project release URLs intact so installations remain usable.
Use reserved documentation addresses or `.test` / `.invalid` domains for fixtures.

## Enable the Git pre-push check

From the repository root, inspect any existing hooks configuration first:

```sh
git config --get core.hooksPath
git rev-parse --git-path hooks/pre-push
```

If there is no custom configuration or existing pre-push hook to preserve:

```sh
git config --local core.hooksPath .githooks
python3 tools/check_portability.py
```

This is a **Git pre-push hook**, with no Codex runtime or third-party dependencies.
Python 3.11+ and Git must be available. Each fresh clone needs to enable it;
Git does not activate repository hooks automatically. If you already use hooks,
integrate the invocation from `.githooks/pre-push` into the existing pre-push
hook and pass through Git's arguments and stdin. Do not overwrite existing hooks
or consume stdin before passing the ref updates to this checker.

## What it checks

The manual command scans tracked and non-ignored untracked working files. The
pre-push hook reads Git's proposed ref updates and checks the committed trees
being pushed, independently of the working tree or current branch. It checks
each outgoing commit, so adding a private value and deleting it in a later commit
does not conceal it. Existing destination refs are excluded from traversal;
new branches exclude commits already known through that destination's remote
tracking refs. The tip is always scanned. Branch deletion transfers no new content.
Fetch destination refs if the check reports missing Git objects. Tags pointing
to commits are supported; non-commit tags fail closed for manual review.

The checker flags:

- Real user home paths on Unix, macOS and Windows, and named macOS volume paths.
- Literal IPv4/IPv6 addresses, except loopback, unspecified bind addresses and
  reserved documentation ranges. This includes private, VPN and public host IPs.
- Private hostnames ending in `.local`, `.internal`, `.lan`, `.home` or `.ts.net`.
- The current local username/hostname and additional private markers.
- Credential filenames, private state directories and generated research packages
  or receipts that should not be committed. Submodules require separate review.
- Literal token/password assignments, credentials in HTTP URLs and private key headers.

Reports include file locations and categories, without echoing matched source
values. CI runs the same check on the committed tree, even when a contributor
has not installed the local hook.

In parseable Python files, IPv6 scanning examines decoded string/byte literals,
f-string literal segments and comments, so slice expressions are not mistaken
for addresses. Unparseable Python receives the conservative plain-text scan.
The canonical public GitHub owner/repository coordinates (URLs and GitHub CLI
repository arguments) are excluded only from local-marker matching; URL paths,
queries and all other detection rules still
apply. Other repositories and lookalike URLs receive no such exception.

Add private project names, account names, hostnames, mount paths or identifiers
to the ignored `.portability-local.txt` in the repository root, one literal value
per line. Blank lines and lines beginning with `#` are ignored. Do not commit this
file. These local rules supplement the shared patterns and do not reach CI.

## Review and limitations

Fix findings using configuration or generic examples and rerun the check. If a
private value exists in an unpushed intermediate commit, fixing only the tip
leaves it in the push: clean up the affected unpushed history before pushing.
Do not rewrite published history automatically.

Pattern checks cannot infer every personal reference or hardware assumption.
Binary asset contents, arbitrary secret formats, commit messages and author
metadata are not scanned. Review the diff and outgoing history as well; this
check complements dedicated secret scanning. Local hooks are bypassable, and
CI runs after a push, so neither provides a server-side guarantee against leaks.
Avoid putting private data into commits in the first place.

References: [Git pre-push hooks](https://git-scm.com/docs/githooks#_pre_push).
