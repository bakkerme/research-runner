"""Closeout accepts complete only when every required check passed."""
import json
import shutil

import pytest

from runner.package import assemble, validate_manifest
from runner.service import Service
from tests.v3_support import ROOT, install_docker, pack, passed_report, wait_job


def research(check='native-load', status='passed'):
    report = passed_report(check)
    report['validation'][0]['status'] = status
    if status != 'passed':
        report['validation'][0]['evidence'] = []
    return report


def archive_for(tmp_path, checks, report, mode='complete'):
    source = tmp_path / 'payload'
    shutil.copytree(ROOT / 'sample-mvp', source)
    manifest = json.loads((source / 'manifest.json').read_text())
    manifest['format'] = 'research-package/v3'
    manifest['required_checks'] = checks
    manifest['required_outputs'] = ['outputs/result.json', 'outputs/handoff.md', 'outputs/research.json']
    manifest['resources']['time_seconds'] = 30
    (source / 'manifest.json').write_text(json.dumps(manifest), encoding='utf-8')
    fixture = {'mode': mode, 'seconds': 0.05, 'summary': mode, 'text': 'retained', 'research': report}
    (source / 'inputs' / 'fixture.json').write_text(json.dumps(fixture), encoding='utf-8')
    archive = tmp_path / 'job.rrpkg'
    assemble(source, archive)
    return archive


def run(tmp_path, archive):
    service = Service(tmp_path / 'state')
    try:
        jid, _ = service.submit(archive, 'submit')
        service.launch(jid, 'start')
        return service, wait_job(service, jid)
    except Exception:
        service._owner.close()
        raise


def test_required_checks_must_be_unique_ids_and_need_the_research_output():
    source = ROOT / 'sample-mvp'
    manifest = json.loads((source / 'manifest.json').read_text())
    manifest['format'] = 'research-package/v3'
    manifest['required_checks'] = ['native-load', 'native-load']
    with pytest.raises(ValueError, match='duplicate required_checks'):
        validate_manifest(manifest)
    manifest['required_checks'] = ['Not A Check']
    with pytest.raises(ValueError, match='required_checks'):
        validate_manifest(manifest)
    manifest['required_checks'] = ['native-load']
    with pytest.raises(ValueError, match='outputs/research.json'):
        validate_manifest(manifest)


def test_complete_rejects_not_run_or_missing_checks(tmp_path):
    service, job = run(tmp_path, archive_for(tmp_path, ['native-load'], research('native-load', 'not_run')))
    try:
        assert job['state'] == 'failed', job
        assert job['error'] == 'complete requires passed check native-load (status not_run)'
        assert job['outcome'] is None
    finally:
        service._owner.close()
    service, job = run(tmp_path / 'missing', archive_for(tmp_path / 'missing', ['native-load'], passed_report('parser-suite')))
    try:
        assert job['state'] == 'failed', job
        assert job['error'] == 'complete requires passed check native-load (status missing)'
    finally:
        service._owner.close()


def test_partial_is_accepted_when_a_required_check_did_not_pass(tmp_path):
    service, job = run(tmp_path, archive_for(tmp_path, ['native-load'], research('native-load', 'not_run'), mode='partial'))
    try:
        assert job['state'] == 'finished', job
        assert job['outcome'] == 'partial'
    finally:
        service._owner.close()


def test_complete_is_accepted_when_every_required_check_passed(tmp_path):
    service, job = run(tmp_path, archive_for(tmp_path, ['parser-suite'], passed_report('parser-suite')))
    try:
        assert job['state'] == 'finished', job
        assert job['outcome'] == 'complete'
    finally:
        service._owner.close()


def test_tool_image_provisioned_is_satisfied_from_the_receipt(tmp_path, monkeypatch):
    install_docker(tmp_path, monkeypatch)
    archive, _ = pack(tmp_path, checks=['tool-image-provisioned', 'parser-suite'], research=passed_report('parser-suite'))
    service = Service(tmp_path / 'state')
    try:
        jid, _ = service.submit(archive, 'submit')
        service.launch(jid, 'start')
        job = wait_job(service, jid)
        assert job['state'] == 'finished', job
        assert job['outcome'] == 'complete'
        report = json.loads(service.download(jid, 'outputs/research.json')[0])
        assert 'tool-image-provisioned' not in {item['id'] for item in report['validation']}
    finally:
        service._owner.close()
