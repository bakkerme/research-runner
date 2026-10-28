"""Publish an already-tested bundle, repairing missing assets without replacing bytes.

Uses the authenticated GitHub CLI; needs only the Python standard library.
"""
import argparse
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
from urllib.parse import quote

try:
    from .package_distribution import distribution_files, verify_distribution
except ImportError:  # Direct invocation from the repository or sdist.
    from package_distribution import distribution_files, verify_distribution


class GitHub:
    def __init__(self, repository):
        if not re.fullmatch(r'[\w.-]+/[\w.-]+', repository):
            raise ValueError('expected an owner/repository GitHub coordinate')
        self.repository = repository

    def run(self, *args):
        return subprocess.check_output(['gh', *args], stderr=subprocess.PIPE)

    def api(self, path, missing_ok=False):
        try:
            return json.loads(self.run('api', f'repos/{self.repository}/{path}'))
        except subprocess.CalledProcessError as exc:
            if missing_ok and b'(HTTP 404)' in exc.stderr:
                return None
            raise

    def commit(self, tag):
        # The commits endpoint also dereferences annotated tags.
        return self.api('commits/' + quote('refs/tags/' + tag, safe=''))['sha']

    def release(self, tag):
        return self.api('releases/tags/' + quote(tag, safe=''), missing_ok=True)

    def download(self, asset):
        return self.run('api', f'repos/{self.repository}/releases/assets/{asset["id"]}',
                        '-H', 'Accept: application/octet-stream')

    def create(self, tag):
        args = ['release', 'create', tag, '--repo', self.repository, '--verify-tag',
                '--draft', '--title', 'Research Runner ' + tag, '--generate-notes']
        if re.search(r'(?:a|b|rc)\d+$', tag):
            args.append('--prerelease')
        self.run(*args)

    def upload(self, tag, path):
        self.run('release', 'upload', tag, str(path), '--repo', self.repository)

    def publish(self, tag):
        self.run('release', 'edit', tag, '--repo', self.repository, '--draft=false')


def check_existing(github, release, inventory):
    """Preflight every existing asset before making any changes."""
    existing = {}
    for asset in release['assets']:
        name = asset['name']
        if name not in inventory:
            continue
        if name in existing:
            raise ValueError('duplicate release asset: ' + name)
        actual = hashlib.sha256(github.download(asset)).hexdigest()
        if actual != inventory[name]:
            raise ValueError('existing release asset differs; refusing to overwrite: ' + name)
        existing[name] = asset
    return existing


def download_complete_release(directory, tag, commit, github):
    """Reuse published bytes on duplicate events, rather than rebuild timestamps."""
    expected = distribution_files(tag.removeprefix('v')) | {'SHA256SUMS'}
    if not tag.startswith('v') or github.commit(tag) != commit:
        raise ValueError('release tag no longer points at the tested source commit')
    release = github.release(tag)
    if release is None:
        return False
    assets = {asset['name']: asset for asset in release['assets']}
    if not expected <= assets.keys():
        return False
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True)
    for name in sorted(expected):
        (directory / name).write_bytes(github.download(assets[name]))
    verify_distribution(directory, tag)
    return True


def publish_release(directory, tag, commit, github):
    inventory = verify_distribution(directory, tag)
    if not re.fullmatch(r'[0-9a-f]{40}', commit) or github.commit(tag) != commit:
        raise ValueError('release tag no longer points at the tested source commit')
    release = github.release(tag)
    if release is None:
        github.create(tag)
        release = github.release(tag)
    existing = check_existing(github, release, inventory)
    # Put the checksum manifest last so it never advertises pending uploads.
    for name in sorted(inventory, key=lambda name: (name == 'SHA256SUMS', name)):
        if name not in existing:
            github.upload(tag, Path(directory) / name)
    release = github.release(tag)
    verified = check_existing(github, release, inventory)
    if verified.keys() != inventory.keys():
        raise ValueError('release is still missing required assets')
    if github.commit(tag) != commit:
        raise ValueError('release tag moved during publication')
    if release['draft']:
        github.publish(tag)
    return {'tag': tag, 'commit': commit, 'assets': sorted(verified), 'url': release['html_url']}


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--tag', required=True)
    parser.add_argument('--commit', required=True, help='Full commit SHA whose bundle passed all tests')
    parser.add_argument('--dist', default='dist')
    parser.add_argument('--repo', default=os.environ.get('GH_REPO'))
    parser.add_argument('--download-existing', action='store_true',
                        help='Download a complete existing release for retesting; do not publish')
    args = parser.parse_args()
    if not args.repo:
        parser.error('--repo or GH_REPO is required')
    try:
        github = GitHub(args.repo)
        if args.download_existing:
            reused = download_complete_release(args.dist, args.tag, args.commit, github)
            if os.environ.get('GITHUB_OUTPUT'):
                with open(os.environ['GITHUB_OUTPUT'], 'a') as output:
                    output.write('reused=' + str(reused).lower() + '\n')
            print(json.dumps({'reused': reused}))
        else:
            print(json.dumps(publish_release(args.dist, args.tag, args.commit, github), indent=2))
    except subprocess.CalledProcessError as exc:
        raise SystemExit(exc.stderr.decode(errors='replace').strip()) from exc
