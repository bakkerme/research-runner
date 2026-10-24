"""Reject machine-specific configuration in source or outgoing Git commit trees.

Standard-library only: suitable for a Git hook before dependencies are installed.
"""
import argparse
import getpass
import ipaddress
import os
from pathlib import Path
import re
import socket
import subprocess
import sys


HOME_PATH = re.compile(r"(?<![\w])(?:/(?:Users|home|Volumes)/|[A-Za-z]:[\\/]+(?:Users|Documents and Settings)[\\/]+)([^/\\\s\"'`<>]+)", re.I)
IPV4 = re.compile(r'(?<![\w.])(?:\d{1,3}\.){3}\d{1,3}(?![\w.])')
IPV6 = re.compile(r'(?<![\w:])(?:[0-9a-fA-F]{0,4}:){2,7}[0-9a-fA-F]{0,4}(?![\w:])')
PRIVATE_HOST = re.compile(r'\b[\w-]+(?:\.[\w-]+)*\.(?:local|internal|lan|home|ts\.net)(?![\w.-])', re.I)
TOKEN_ASSIGNMENT = re.compile(r'''(?:^\s*(?:export\s+)?|["'])(?:RESEARCH_RUNNER_(?:CONTROL_|READ_)?TOKEN|[A-Z_]*(?:API_KEY|PASSWORD|ACCESS_TOKEN))["']?\s*[:=]\s*["']?([^\s"'`,;]+)''')
URL_CREDENTIALS = re.compile(r'https?://[^\s/@]+:[^\s/@]+@', re.I)
EXAMPLE_USERS = {'user', 'username', 'example', 'your-user', 'your-username', 'public'}
EXAMPLE_NETWORKS = tuple(ipaddress.ip_network(n) for n in (
    '192.0.2.0/24', '198.51.100.0/24', '203.0.113.0/24', '2001:db8::/32'))


def git(root, *args):
    return subprocess.check_output(['git', '-C', str(root), *args], stderr=subprocess.PIPE)


def local_markers(root):
    """Personal markers stay local, so the guard itself contains no personal names."""
    values = {v for v in (getpass.getuser(), socket.gethostname())
              if len(v) >= 4 and v.lower() not in {
                  'root', 'user', 'runner', 'admin', 'administrator', 'localhost'}}
    config = root / '.portability-local.txt'
    if config.exists():
        values.update(line.strip() for line in config.read_text().splitlines()
                      if line.strip() and not line.lstrip().startswith('#'))
    return sorted(values)


def reasons(text, markers):
    found = set()
    for match in HOME_PATH.finditer(text):
        if match[1].lower() not in EXAMPLE_USERS:
            found.add('personal home directory')
    for pattern in (IPV4, IPV6):
        for match in pattern.finditer(text):
            try:
                address = ipaddress.ip_address(match[0])
            except ValueError:
                continue
            if address.is_loopback or address.is_unspecified:
                continue
            if any(address.version == net.version and address in net for net in EXAMPLE_NETWORKS):
                continue
            found.add('literal network address; use configuration or a documentation address')
    if PRIVATE_HOST.search(text):
        found.add('private hostname')
    for match in TOKEN_ASSIGNMENT.finditer(text):
        value = match[1]
        if not value.startswith(('REPLACE_', 'YOUR_', '<', '${', '$', '%')):
            found.add('literal credential assignment; use environment configuration')
    if URL_CREDENTIALS.search(text) or re.search(r'-----BEGIN (?:[A-Z]+ )?PRIVATE KEY-----', text):
        found.add('embedded credentials or private key')
    if any(re.search(r'(?<!\w)' + re.escape(value) + r'(?!\w)', text, re.I)
           for value in markers):
        found.add('local user, hostname or private marker')
    return sorted(found)


def private_file(path):
    parts = Path(path).parts
    name = Path(path).name.lower()
    return (any(p in {'.aws', '.ssh', '.codex', '.research-runner', '.venv', 'var', 'runs', 'dist', 'wheelhouse'} for p in parts)
            or name == '.portability-local.txt'
            or (name.startswith('.env') and name not in {'.env.example', '.env.sample', '.env.template'})
            or name in {'auth.json', 'credentials.json', 'id_rsa', 'id_ed25519'}
            or name.endswith(('.rrpkg', '.pem', '.key'))
            or (name.startswith('receipt') and name.endswith('.json')))


def inspect(path, data, label, markers):
    issues = []
    if private_file(path):
        issues.append(f'{label}:{path}: private state, credential or generated artifact filename')
    for reason in reasons(path, markers):
        issues.append(f'{label}:{path}: {reason}')
    # Binary assets need separate review; do not print source values or secrets.
    if b'\0' in data:
        return issues
    for number, line in enumerate(data.decode('utf-8', errors='replace').splitlines(), 1):
        for reason in reasons(line, markers):
            issues.append(f'{label}:{path}:{number}: {reason}')
    return issues


def tree_issues(root, revision, markers, seen):
    issues = []
    entries = git(root, 'ls-tree', '-rz', '--full-tree', revision).split(b'\0')
    for entry in filter(None, entries):
        meta, raw_path = entry.split(b'\t', 1)
        mode, kind, oid = meta.decode().split()
        path = os.fsdecode(raw_path)
        if kind != 'blob':
            issues.append(f'{revision[:12]}:{path}: submodule requires independent portability review')
            continue
        key = (path, oid)
        if key in seen:
            continue
        seen.add(key)
        issues.extend(inspect(path, git(root, 'cat-file', 'blob', oid), revision[:12], markers))
    return issues


def outgoing_revisions(root, updates, remote):
    revisions = set()
    for line in updates.splitlines():
        _, local_oid, _, remote_oid = line.split()
        if set(local_oid) == {'0'}:  # Deleting a ref transfers no new content.
            continue
        # Fail closed for missing objects and tags that do not resolve to commits.
        tip = git(root, 'rev-parse', '--verify', local_oid + '^{commit}').decode().strip()
        revisions.add(tip)
        if set(remote_oid) == {'0'}:
            args = [tip, '--not', '--remotes=' + remote]
        else:
            base = git(root, 'rev-parse', '--verify', remote_oid + '^{commit}').decode().strip()
            args = [tip, '^' + base]
        revisions.update(git(root, 'rev-list', *args).decode().splitlines())
    return sorted(revisions)


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--repo', type=Path, default=Path.cwd())
    parser.add_argument('--pre-push', action='store_true')
    parser.add_argument('--revision', help='scan a committed tree, for CI')
    parser.add_argument('remote', nargs='?', default='origin')
    parser.add_argument('remote_url', nargs='?')  # Git pre-push also supplies the URL.
    args = parser.parse_args()
    try:
        root = Path(os.fsdecode(git(args.repo, 'rev-parse', '--show-toplevel')).strip())
        markers = local_markers(root)
        issues = []
        if args.pre_push or args.revision:
            revisions = (outgoing_revisions(root, sys.stdin.read(), args.remote)
                         if args.pre_push else [args.revision])
            seen = set()
            for revision in revisions:
                issues.extend(tree_issues(root, revision, markers, seen))
        else:
            paths = git(root, 'ls-files', '-z', '--cached', '--others', '--exclude-standard')
            for path in sorted(set(filter(None, map(os.fsdecode, paths.split(b'\0'))))):
                file = root / path
                if file.is_symlink():
                    data = os.fsencode(os.readlink(file))
                elif file.is_file():
                    data = file.read_bytes()
                else:
                    continue
                issues.extend(inspect(path, data, 'working-tree', markers))
        if issues:
            print('Portability check failed:', file=sys.stderr)
            print('\n'.join(issues), file=sys.stderr)
            print('Use configurable values, home-relative paths and documented examples. '
                  'Review findings before pushing; source values are intentionally omitted.', file=sys.stderr)
            return 1
        print('Portability check passed.')
        return 0
    except (OSError, ValueError, subprocess.CalledProcessError) as error:
        # Do not echo Git stderr or remote URLs, which may contain credentials.
        print(f'Portability check could not complete ({type(error).__name__}). '
              'Check the repository and fetch the destination refs before retrying.', file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
