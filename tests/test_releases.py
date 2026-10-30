"""Release failure and retry behavior, without credentials or live mutations."""
import io
import json
from pathlib import Path
import subprocess
import tarfile
import tomllib
import zipfile

import pytest

from tools.package_distribution import package_distribution, verify_distribution
from tools.publish_release import GitHub, download_complete_release, publish_release
from tools.prepare_release import prepare_release


TAG = 'v1.2.3'
COMMIT = 'a' * 40


@pytest.fixture
def bundle(tmp_path):
    # Exercise the real docs packager using the maintained doc inventory.
    root = Path(__file__).resolve().parents[1]
    docs = {name: (root / name).read_bytes() for name in ('README.md', 'AGENTS.md')}
    for folder in ('docs', 'schemas', 'examples'):
        docs.update({path.relative_to(root).as_posix(): path.read_bytes()
                     for path in (root / folder).rglob('*') if path.is_file()})
    skill = 'skills/research-runner/SKILL.md'
    docs[skill] = (root / 'runner' / skill).read_bytes()
    with zipfile.ZipFile(tmp_path / 'research_runner-1.2.3-py3-none-any.whl', 'w') as archive:
        for name, data in docs.items():
            archive.writestr('runner/' + name if name == skill else 'runner/_bundle/' + name, data)
    with tarfile.open(tmp_path / 'research_runner-1.2.3.tar.gz', 'w:gz') as archive:
        for name, data in docs.items():
            entry = tarfile.TarInfo('research_runner-1.2.3/' + ('runner/' + name if name == skill else name))
            entry.size = len(data)
            archive.addfile(entry, io.BytesIO(data))
    package_distribution(tmp_path)
    return tmp_path


class FakeGitHub:
    def __init__(self, directory, *, exists=True, draft=False, names=()):
        self.commit_sha = COMMIT
        self.exists = exists
        self.draft = draft
        self.files = {name: (directory / name).read_bytes() for name in names}
        self.mutations = []

    def commit(self, tag):
        assert tag == TAG
        return self.commit_sha

    def release(self, tag):
        if not self.exists:
            return None
        return {'assets': [{'name': name, 'id': name} for name in self.files],
                'draft': self.draft, 'html_url': 'https://example.invalid/release'}

    def download(self, asset):
        return self.files[asset['name']]

    def create(self, tag):
        self.mutations.append(('create', tag))
        self.exists = self.draft = True

    def upload(self, tag, path):
        assert path.name not in self.files  # A published byte is never clobbered.
        self.mutations.append(('upload', path.name))
        self.files[path.name] = path.read_bytes()

    def publish(self, tag):
        self.mutations.append(('publish', tag))
        self.draft = False


class DraftLookupGitHub(FakeGitHub, GitHub):
    """Exercise the real REST adapter with GitHub's published-only tag lookup."""
    repository = 'example/research-runner'
    release = GitHub.release

    def run(self, *args):
        release = FakeGitHub.release(self, TAG)
        if release:
            release['tag_name'] = TAG
        if args == ('api', f'repos/{self.repository}/releases/tags/{TAG}'):
            if release and not release['draft']:
                return json.dumps(release).encode()
            raise subprocess.CalledProcessError(1, args, stderr=b'gh: Not Found (HTTP 404)')
        assert args == ('api', f'repos/{self.repository}/releases?per_page=100', '--paginate', '--slurp')
        # The desired draft can be on a later page among unrelated releases.
        return json.dumps([[{'tag_name': 'v9.9.9'}], [release] if release else []]).encode()


@pytest.mark.parametrize('exists', [False, True])
def test_publisher_can_discover_new_and_existing_drafts(bundle, exists):
    github = DraftLookupGitHub(bundle, exists=exists, draft=True)
    publish_release(bundle, TAG, COMMIT, github)
    assert github.mutations.count(('create', TAG)) == (0 if exists else 1)
    assert github.mutations[-1] == ('publish', TAG)
    assert len(github.files) == 4


def test_complete_draft_can_be_discovered_for_reuse(bundle, tmp_path_factory):
    github = DraftLookupGitHub(bundle, draft=True, names=[path.name for path in bundle.iterdir()])
    assert download_complete_release(tmp_path_factory.mktemp('draft'), TAG, COMMIT, github)
    assert github.mutations == []


def test_new_release_is_draft_until_all_four_downloads_are_verified(bundle):
    github = FakeGitHub(bundle, exists=False)
    result = publish_release(bundle, TAG, COMMIT, github)
    assert github.mutations[0] == ('create', TAG)
    assert github.mutations[-2:] == [('upload', 'SHA256SUMS'), ('publish', TAG)]
    assert len(result['assets']) == 4


def test_repairs_empty_existing_published_release_and_is_repeatable(bundle):
    github = FakeGitHub(bundle)
    publish_release(bundle, TAG, COMMIT, github)
    assert len(github.mutations) == 4
    assert all(action == 'upload' for action, _ in github.mutations)
    github.mutations.clear()
    publish_release(bundle, TAG, COMMIT, github)
    assert github.mutations == []


def test_partial_upload_can_resume_using_the_same_tested_bundle(bundle):
    wheel = 'research_runner-1.2.3-py3-none-any.whl'
    github = FakeGitHub(bundle, draft=True, names=[wheel])
    publish_release(bundle, TAG, COMMIT, github)
    assert ('upload', wheel) not in github.mutations
    assert github.mutations[-1] == ('publish', TAG)


def test_conflicting_existing_asset_fails_before_any_mutation(bundle):
    github = FakeGitHub(bundle, names=['research_runner-1.2.3.tar.gz'])
    github.files['research_runner-1.2.3.tar.gz'] = b'different build'
    with pytest.raises(ValueError, match='refusing to overwrite'):
        publish_release(bundle, TAG, COMMIT, github)
    assert github.mutations == []


def test_failed_upload_can_be_retried_and_keeps_new_release_draft(bundle, monkeypatch):
    github = FakeGitHub(bundle, exists=False)
    upload = github.upload

    def fail_checksum(tag, path):
        if path.name == 'SHA256SUMS':
            raise OSError('connection lost')
        upload(tag, path)

    monkeypatch.setattr(github, 'upload', fail_checksum)
    with pytest.raises(OSError, match='connection lost'):
        publish_release(bundle, TAG, COMMIT, github)
    assert github.draft and 'SHA256SUMS' not in github.files
    monkeypatch.setattr(github, 'upload', upload)
    publish_release(bundle, TAG, COMMIT, github)
    assert not github.draft


def test_corrupt_remote_upload_is_detected_before_publication(bundle, monkeypatch):
    github = FakeGitHub(bundle, exists=False)
    upload = github.upload

    def corrupt(tag, path):
        upload(tag, path)
        github.files[path.name] += b'corruption'

    monkeypatch.setattr(github, 'upload', corrupt)
    with pytest.raises(ValueError, match='refusing to overwrite'):
        publish_release(bundle, TAG, COMMIT, github)
    assert github.draft


def test_moved_tag_does_not_publish(bundle):
    github = FakeGitHub(bundle)
    github.commit_sha = 'b' * 40
    with pytest.raises(ValueError, match='tested source commit'):
        publish_release(bundle, TAG, COMMIT, github)
    assert github.mutations == []


@pytest.mark.parametrize('mutation', ['corrupt', 'extra', 'incomplete', 'traversal', 'duplicate'])
def test_bundle_validation_rejects_corruption_and_unsafe_inventories(bundle, mutation):
    manifest = bundle / 'SHA256SUMS'
    if mutation == 'corrupt':
        (bundle / 'research_runner-1.2.3.tar.gz').write_bytes(b'corrupted')
    elif mutation == 'extra':
        (bundle / 'unexpected.txt').write_text('must never upload')
    elif mutation == 'incomplete':
        manifest.write_text(manifest.read_text().splitlines()[0] + '\n')
    elif mutation == 'duplicate':
        manifest.write_text(manifest.read_text() + manifest.read_text().splitlines()[0] + '\n')
    else:
        manifest.write_text('0' * 64 + '  ../outside\n')
    with pytest.raises(ValueError):
        verify_distribution(bundle, TAG)


def test_complete_release_is_downloaded_and_retested_without_rebuilding(bundle, tmp_path_factory):
    github = FakeGitHub(bundle, names=[path.name for path in bundle.iterdir()])
    destination = tmp_path_factory.mktemp('reused')
    assert download_complete_release(destination, TAG, COMMIT, github)
    assert verify_distribution(destination, TAG) == verify_distribution(bundle, TAG)
    publish_release(destination, TAG, COMMIT, github)
    assert github.mutations == []


def test_incomplete_release_needs_a_build_instead_of_being_reused(bundle, tmp_path_factory):
    github = FakeGitHub(bundle, names=['SHA256SUMS'])
    assert not download_complete_release(tmp_path_factory.mktemp('missing'), TAG, COMMIT, github)


def test_existing_release_corruption_fails_reuse(bundle, tmp_path_factory):
    github = FakeGitHub(bundle, names=[path.name for path in bundle.iterdir()])
    github.files['research_runner-1.2.3.tar.gz'] = b'corrupt'
    with pytest.raises(ValueError, match='checksum mismatch'):
        download_complete_release(tmp_path_factory.mktemp('corrupt'), TAG, COMMIT, github)


def test_only_not_found_is_treated_as_missing_release(monkeypatch):
    github = GitHub('example/research-runner')

    def fail(*args):
        raise subprocess.CalledProcessError(1, args, stderr=b'gh: Forbidden (HTTP 403)')

    monkeypatch.setattr(github, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        github.release(TAG)

    def missing(*args):
        if 'releases/tags/' in args[1]:
            raise subprocess.CalledProcessError(1, args, stderr=b'gh: Not Found (HTTP 404)')
        return b'[[]]'

    monkeypatch.setattr(github, 'run', missing)
    assert github.release(TAG) is None


def test_draft_listing_failure_is_not_treated_as_missing_release(monkeypatch):
    github = GitHub('example/research-runner')

    def fail(*args):
        status = b'404' if 'releases/tags/' in args[1] else b'403'
        raise subprocess.CalledProcessError(1, args, stderr=b'gh: request failed (HTTP ' + status + b')')

    monkeypatch.setattr(github, 'run', fail)
    with pytest.raises(subprocess.CalledProcessError):
        github.release(TAG)


@pytest.fixture
def release_source(tmp_path):
    (tmp_path / 'pyproject.toml').write_text(
        '[project]\nname = "research-runner"\nversion = "0.5.0"\n'
        '[tool.example]\nversion = "keep-me"\n', encoding='utf-8')
    (tmp_path / 'runner').mkdir()
    (tmp_path / 'runner/__init__.py').write_text(
        '"""Runner."""\n__version__ = "0.5.0"\n', encoding='utf-8')
    return tmp_path


@pytest.mark.parametrize('version', ['0.6.0', '1.0.0a1', '1.0.0b2', '1.0.0rc3'])
def test_release_tag_sets_package_and_runtime_versions_without_a_bump_commit(release_source, version):
    assert prepare_release(release_source, 'v' + version) == version
    project = tomllib.loads((release_source / 'pyproject.toml').read_text(encoding='utf-8'))
    assert project['project']['version'] == version
    assert project['tool']['example']['version'] == 'keep-me'
    runtime = {}
    exec((release_source / 'runner/__init__.py').read_text(encoding='utf-8'), runtime)
    assert runtime['__version__'] == version
    before = (release_source / 'pyproject.toml').read_bytes()
    prepare_release(release_source, 'v' + version)
    assert (release_source / 'pyproject.toml').read_bytes() == before


@pytest.mark.parametrize('tag', ['0.6.0', 'v0.6', 'v01.2.3', 'v1.2.3rc01',
                                 'v1.2.3+local', 'v1.2.3/post1', 'v1.2.3\nother'])
def test_invalid_release_tags_cannot_modify_source(release_source, tag):
    before = {path: path.read_bytes() for path in release_source.rglob('*') if path.is_file()}
    with pytest.raises(ValueError, match='release tag must be'):
        prepare_release(release_source, tag)
    assert {path: path.read_bytes() for path in before} == before


def test_missing_runtime_version_cannot_leave_package_half_prepared(release_source):
    (release_source / 'runner/__init__.py').write_text('"""No version."""\n', encoding='utf-8')
    before = (release_source / 'pyproject.toml').read_bytes()
    with pytest.raises(ValueError, match='runtime __version__'):
        prepare_release(release_source, 'v0.6.0')
    assert (release_source / 'pyproject.toml').read_bytes() == before
