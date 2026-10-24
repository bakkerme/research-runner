"""Exercise the privacy guard against real outgoing Git histories."""
import importlib.util
from pathlib import Path
import shutil
import subprocess
import sys

import pytest


SOURCE = Path(__file__).resolve().parents[1]
SCRIPT = SOURCE / 'tools/check_portability.py'
spec = importlib.util.spec_from_file_location('portability', SCRIPT)
guard = importlib.util.module_from_spec(spec)
spec.loader.exec_module(guard)


def git(repo, *args):
    return subprocess.check_output(['git', '-C', str(repo), *args], stderr=subprocess.PIPE).decode().strip()


@pytest.fixture
def repo(tmp_path):
    root = tmp_path / 'repo'
    root.mkdir()
    git(root, 'init', '-b', 'main')
    git(root, 'config', 'user.name', 'Example Developer')
    git(root, 'config', 'user.email', 'developer@example.test')
    (root / 'README.md').write_text('Portable project\n')
    commit(root, 'Initial')
    return root


def commit(repo, message):
    git(repo, 'add', '.')
    git(repo, 'commit', '-m', message)
    return git(repo, 'rev-parse', 'HEAD')


def check(repo, *args, updates=None):
    return subprocess.run([sys.executable, str(SCRIPT), '--repo', str(repo), *args],
                          input=updates, text=True, capture_output=True)


def update(local, remote=None):
    return f'refs/heads/main {local} refs/heads/main {remote or "0" * 40}\n'


def private_address():
    return '.'.join(('10', '23', '45', '67'))


@pytest.mark.parametrize('value', [
    '/'.join(('', 'Users', 'private-person', 'project')),
    '/'.join(('', 'home', 'private-person', 'project')),
    '\\'.join(('C:', 'Users', 'private-person', 'project')),
    '\\\\'.join(('C:', 'Users', 'private-person', 'project')),
    '/'.join(('', 'Volumes', 'private-drive', 'project')),
    '.'.join(('10', '23', '45', '67')),
    ':'.join(('fd12', '3456', '', '1')),
    '.'.join(('private-server', 'ts', 'net')),
    '.'.join(('private-server', 'internal')),
    '='.join(('RESEARCH_RUNNER_CONTROL_TOKEN', 'private-example-value')),
    'https://' + ':'.join(('account', 'private-example-value')) + '@example.test',
])
def test_machine_specific_values_are_rejected_without_echoing_values(repo, value):
    (repo / 'settings.txt').write_text(value + '\n')
    result = check(repo)
    assert result.returncode == 1
    assert 'settings.txt:1:' in result.stderr
    assert value not in result.stderr


def test_generic_examples_and_canonical_public_urls_are_allowed(repo):
    (repo / 'examples.txt').write_text(
        'http://127.0.0.1:8765 0.0.0.0 ::1 192.0.2.10 198.51.100.3 203.0.113.4\n'
        '2001:db8::1 https://example.test https://github.com/example/project\n'
        '$HOME/.local/bin %h/.codex /home/USER/project\n'
        'env.update(RESEARCH_RUNNER_READ_TOKEN=self.read_token)\n'
        '[asset_loaders.local-files]\nKeep the same user/home/runtime.\n')
    assert check(repo).returncode == 0


def test_python_slice_syntax_is_not_an_ipv6_address(repo):
    step = ':' * 2 + '2'
    (repo / 'slices.py').write_text(
        f'values = range(10)\na = values[{step}]\nb = values[1{step}]\n'
        f'c = values[{{"key": 1}}["key"]{step}]\n'
        f'd = f"{{values[{step}]}}"\n')
    assert check(repo).returncode == 0


@pytest.mark.parametrize('context', ['string', 'comment', 'fstring', 'escaped', 'invalid-python'])
def test_python_ipv6_literals_and_comments_still_rejected(repo, context):
    address = ':'.join(('fd12', '3456', '', '2'))
    sources = {
        'string': f'host = "{address}"\n',
        'comment': f'# server: {address}\n',
        'fstring': f'host = f"http://[{address}]:{{port}}"\n',
        'escaped': 'host = ' + repr(address).replace(':', r'\x3a') + '\n',
        'invalid-python': f'host = "{address}"\nunfinished = (\n',
    }
    (repo / 'settings.py').write_text(sources[context])
    result = check(repo)
    assert result.returncode == 1
    assert 'literal network address' in result.stderr


def test_public_repository_coordinates_do_not_trigger_local_markers():
    public = 'https://github.com/bakkerme/research-runner'
    owner = public.split('/')[3]
    for suffix in ('', '/', '/releases/download/v0.3.0/example.whl', '/actions/workflows/test.yml'):
        assert guard.reasons(public + suffix, [owner]) == []
    assert guard.reasons('gh run view --repo ' + '/'.join(public.split('/')[-2:]), [owner]) == []
    # Only the canonical owner/repository coordinates are public exceptions.
    for text in (owner, f'https://github.com/{owner}/private-project',
                 public + '-private', public + '/' + owner,
                 public + '?account=' + owner, public + '.example.test',
                 'https://example.test/' + '/'.join(public.split('/')[-2:])):
        assert 'local user, hostname or private marker' in guard.reasons(text, [owner])
    text = public + ' ' + private_address()
    assert any('literal network address' in reason for reason in guard.reasons(text, [owner]))


def test_local_markers_and_ignored_files(repo):
    (repo / '.gitignore').write_text('.portability-local.txt\n.env\n')
    marker = 'lab'
    (repo / '.portability-local.txt').write_text('# Private additions\n' + marker + '\n')
    (repo / '.env').write_text(private_address())
    assert check(repo).returncode == 0
    (repo / 'notes.txt').write_text(marker)
    assert check(repo).returncode == 1


def test_committed_tree_checked_even_when_working_tree_is_cleaned(repo):
    (repo / 'settings.txt').write_text(private_address())
    tip = commit(repo, 'Configuration')
    (repo / 'settings.txt').write_text('SERVER_ADDRESS')
    assert check(repo).returncode == 0
    assert check(repo, '--revision', tip).returncode == 1


def test_intermediate_leak_is_rejected_after_removal(repo):
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'settings.txt').write_text(private_address())
    leak = commit(repo, 'Temporary configuration')
    (repo / 'settings.txt').unlink()
    tip = commit(repo, 'Remove configuration')
    result = check(repo, '--pre-push', 'origin', updates=update(tip, base))
    assert result.returncode == 1
    assert leak[:12] in result.stderr
    assert check(repo, '--revision', tip).returncode == 0


def test_new_branch_tags_multiple_refs_and_deletion(repo):
    base = git(repo, 'rev-parse', 'HEAD')
    git(repo, 'update-ref', 'refs/remotes/origin/main', base)
    (repo / 'notes.txt').write_text('Portable additions')
    tip = commit(repo, 'Update')
    git(repo, 'tag', '-a', 'v-test', '-m', 'Example release')
    tag = git(repo, 'rev-parse', 'v-test')
    updates = update(tip) + f'refs/tags/v-test {tag} refs/tags/v-test {"0" * 40}\n'
    assert check(repo, '--pre-push', 'origin', updates=updates).returncode == 0
    assert check(repo, '--pre-push', 'origin', updates=update('0' * 40, tip)).returncode == 0
    missing = check(repo, '--pre-push', 'origin', updates=update(tip, '1' * 40))
    assert missing.returncode == 2


def test_private_filenames_and_symlink_targets_are_checked(repo):
    (repo / 'credentials.json').write_text('{}')
    assert check(repo).returncode == 1
    (repo / 'credentials.json').unlink()
    link = repo / 'external-config'
    try:
        link.symlink_to('/'.join(('', 'home', 'private-person', 'secret')))
    except OSError:
        pytest.skip('symlinks unavailable')
    assert check(repo).returncode == 1


def test_installed_hook_blocks_a_real_push_without_changing_remote(repo, tmp_path):
    bare = tmp_path / 'remote.git'
    subprocess.run(['git', 'init', '--bare', str(bare)], check=True, capture_output=True)
    git(repo, 'remote', 'add', 'origin', str(bare))
    (repo / 'tools').mkdir()
    shutil.copyfile(SCRIPT, repo / 'tools/check_portability.py')
    hooks = repo / '.githooks'
    hooks.mkdir()
    shutil.copyfile(SOURCE / '.githooks/pre-push', hooks / 'pre-push')
    (hooks / 'pre-push').chmod(0o755)
    commit(repo, 'Install guard')
    git(repo, 'config', 'core.hooksPath', '.githooks')
    assert 'Portability check passed.' in git(repo, 'push', 'origin', 'main')
    base = git(repo, 'rev-parse', 'HEAD')
    (repo / 'settings.txt').write_text(private_address())
    commit(repo, 'Private configuration')
    (repo / 'settings.txt').unlink()
    commit(repo, 'Remove private configuration')
    result = subprocess.run(['git', '-C', str(repo), 'push', 'origin', 'main'], capture_output=True, text=True)
    assert result.returncode != 0
    assert 'Portability check failed' in result.stderr
    assert git(bare, 'rev-parse', 'main') == base
