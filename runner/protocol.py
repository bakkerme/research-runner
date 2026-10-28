"""Small, workload-neutral MVP wire contracts. No domain-specific validation."""
from typing import Literal

from pydantic import BaseModel, ConfigDict, Field, StrictStr, model_validator


class Contract(BaseModel):
    model_config = ConfigDict(extra='forbid', strict=True, allow_inf_nan=False)


class Policy(Contract):
    finalization_seconds: float = Field(gt=0)
    termination_seconds: float = Field(gt=0)
    checkpoint_seconds: float = Field(ge=0.1, le=300)

    @model_validator(mode='after')
    def reserves(self):
        if self.termination_seconds >= self.finalization_seconds:
            raise ValueError('termination reserve must be smaller than finalization reserve')
        return self


class AssetDependency(Contract):
    asset_type: StrictStr = Field(pattern='^[a-z][a-z0-9-]{0,63}$')
    asset_id: StrictStr = Field(min_length=1, max_length=2000)
    destination: StrictStr = Field(min_length=1, max_length=2000)


class Checkpoint(Contract):
    format: Literal['runner-checkpoint/v1']
    summary: StrictStr = Field(min_length=1, max_length=8000)
    next_action: StrictStr = Field(max_length=8000)
    blockers: list[StrictStr] = Field(max_length=100)


class Result(Contract):
    format: Literal['runner-result/v1']
    outcome: Literal['complete', 'partial', 'blocked']
    reason: Literal['scope_complete', 'budget_exhausted', 'no_actionable_work', 'user_requested']
    summary: StrictStr = Field(min_length=1, max_length=8000)
    next_action: StrictStr = Field(max_length=8000)
    blockers: list[StrictStr] = Field(max_length=100)

    @model_validator(mode='after')
    def consistent(self):
        if (self.outcome == 'complete') != (self.reason == 'scope_complete'):
            raise ValueError('complete requires scope_complete; unfinished scope cannot claim it')
        if (self.outcome == 'blocked') != (self.reason == 'no_actionable_work'):
            raise ValueError('blocked requires no_actionable_work')
        if self.outcome == 'blocked' and not self.blockers:
            raise ValueError('blocked requires an explanation')
        return self


class Evidence(Contract):
    id: StrictStr = Field(min_length=1, max_length=100)
    path: StrictStr
    description: StrictStr = Field(min_length=1, max_length=8000)
    sha256: StrictStr | None = Field(default=None, pattern='^[a-f0-9]{64}$')


class Finding(Contract):
    id: StrictStr = Field(min_length=1, max_length=100)
    claim: StrictStr = Field(min_length=1, max_length=8000)
    confidence: Literal['supported', 'tentative', 'unresolved']
    evidence: list[StrictStr] = Field(min_length=1, max_length=100)
    limitations: list[StrictStr] = Field(max_length=100)


class ValidationCheck(Contract):
    id: StrictStr = Field(min_length=1, max_length=100)
    method: StrictStr = Field(min_length=1, max_length=8000)
    status: Literal['passed', 'failed', 'inconclusive', 'not_run']
    expected: StrictStr = Field(min_length=1, max_length=8000)
    observed: StrictStr = Field(min_length=1, max_length=8000)
    evidence: list[StrictStr] = Field(max_length=100)


class Dependency(Contract):
    path: StrictStr = Field(min_length=1, max_length=2000)
    sha256: StrictStr = Field(pattern='^[a-f0-9]{64}$')
    provenance: StrictStr = Field(min_length=1, max_length=8000)


class Reproduction(Contract):
    commands: list[StrictStr] = Field(max_length=100)
    dependencies: list[Dependency] = Field(max_length=512)
    notes: StrictStr = Field(max_length=8000)


class ResearchReport(Contract):
    """Workload-authored research and validation, separate from transport integrity."""
    format: Literal['runner-research/v1']
    scope: StrictStr = Field(min_length=1, max_length=8000)
    findings: list[Finding] = Field(max_length=100)
    evidence: list[Evidence] = Field(max_length=100)
    validation: list[ValidationCheck] = Field(min_length=1, max_length=100)
    reproduction: Reproduction
    deliverables: list[StrictStr] = Field(max_length=100)
    limitations: list[StrictStr] = Field(max_length=100)

    @model_validator(mode='after')
    def references(self):
        from .package import relpath
        ids = [item.id for item in self.evidence]
        if len(set(ids)) != len(ids):
            raise ValueError('duplicate evidence ids')
        for group in (self.findings, self.validation):
            if len({item.id for item in group}) != len(group):
                raise ValueError('duplicate finding or validation ids')
            for item in group:
                if not set(item.evidence) <= set(ids):
                    raise ValueError('unknown evidence reference')
                if isinstance(item, ValidationCheck) and item.status == 'passed' and not item.evidence:
                    raise ValueError('passed validation requires evidence')
        for path in self.deliverables + [item.path for item in self.evidence]:
            if not relpath(path).startswith('outputs/'):
                raise ValueError('research artifact paths must start with outputs/')
        return self


def validate_research(data, artifacts=None):
    report = load_contract(ResearchReport, data)
    if artifacts is not None:
        for path in report['deliverables'] + [item['path'] for item in report['evidence']]:
            if path not in artifacts:
                raise ValueError('research references unpublished artifact: ' + path)
        for item in report['evidence']:
            if item['sha256'] and artifacts[item['path']]['sha256'] != item['sha256']:
                raise ValueError('research evidence hash mismatch: ' + item['path'])
    return report


def load_contract(model, data):
    if len(data) > 65536:
        raise ValueError('contract exceeds 64 KiB')
    return model.model_validate_json(data).model_dump()


def is_mvp(manifest):
    return manifest['format'] in ('research-package/v2', 'research-package/v3')


def capabilities():
    from . import __version__
    from .package import INPUT_MAX_FILE, INPUT_MAX_TOTAL, MAX_ARCHIVE, MAX_FILE, MAX_TOTAL, MAX_FILES
    return {
        'format': 'runner-capabilities/v1', 'version': __version__,
        'package_formats': ['research-package/v1', 'research-package/v2', 'research-package/v3'],
        'adapters': ['fixture', 'codex'], 'max_running_jobs': 1,
        'default_engine': 'host', 'execution_engines': ['host', 'docker'],
        'limits': {'file_bytes': INPUT_MAX_FILE, 'package_bytes': INPUT_MAX_TOTAL,
                   'archive_bytes': MAX_ARCHIVE, 'files': MAX_FILES, 'contract_bytes': 65536,
                   'output_file_bytes': MAX_FILE, 'output_total_bytes': MAX_TOTAL},
        'features': {'bounded_runs': True, 'graceful_finalize': True,
                     'asset_dependencies': True, 'tool_images': True, 'required_checks': True,
                     'worker_deadline_failsafe': True,
                     'container_auth_file_projection': True, 'container_auth_refresh_qualified': False,
                     'partial_artifacts': True, 'event_paging': True,
                     'datasets': False, 'automatic_retries': False,
                     'automatic_continuation': False, 'external_workers': False,
                     'domain_validation': False, 'structured_research': True, 'budget_extension': False},
    }


def schemas():
    result = {name: model.model_json_schema() for name, model in
              [('policy', Policy), ('checkpoint', Checkpoint), ('result', Result), ('research', ResearchReport),
               ('asset-dependency', AssetDependency)]}
    path = {'type': 'string', 'minLength': 1,
            'description': 'Normalized relative POSIX path; no traversal, links or reserved input paths.'}
    paths = {'type': 'array', 'items': path, 'uniqueItems': True}
    properties = {
        'format': {'const': 'research-package/v2'},
        'name': {'type': 'string', 'minLength': 1, 'maxLength': 100},
        'adapter': {'enum': ['fixture', 'codex']},
        'runtime': {'type': 'object', 'required': ['sdk'],
                    'dependentRequired': {'image': ['image_id'], 'image_id': ['image']},
                    'properties': {'image': {'type': 'string'},
                                   'image_id': {'type': 'string', 'pattern': '^sha256:[a-f0-9]{64}$'},
                                   'sdk': {'const': 'openai-codex==0.159.0'}}},
        'brief': path, 'agents': path, 'skills': paths, 'inputs': paths,
        'resources': {'type': 'object', 'required': ['cpu', 'memory_mb', 'time_seconds'],
                      'properties': {key: {'type': 'number', 'exclusiveMinimum': 0, 'maximum': maximum}
                                     for key, maximum in [('cpu', 8), ('memory_mb', 32768), ('time_seconds', 86400)]}},
        'required_outputs': dict(paths, allOf=[{'contains': {'const': name}}
                                              for name in ['outputs/result.json', 'outputs/handoff.md']]),
        'policy': result['policy'],
    }
    from .asset_protocol import schema as asset_loader_schema
    result['asset-loader'] = asset_loader_schema()
    result['manifest-v2'] = {
        '$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object',
        'additionalProperties': False, 'required': list(properties), 'properties': properties,
        'description': 'Cross-field/path, dependency and archive constraints are additionally checked by research-runner verify.',
    }
    properties['dependencies'] = {'type': 'array', 'maxItems': 100,
                                  'items': result['asset-dependency']}
    sha = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
    v3 = dict(properties)
    v3['format'] = {'const': 'research-package/v3'}
    v3['tool_images'] = {
        'type': 'array', 'minItems': 1, 'maxItems': 8,
        'items': {
            'type': 'object', 'additionalProperties': False,
            'required': ['name', 'containerfile', 'context', 'platform', 'inputs', 'tag', 'timeout_seconds'],
            'properties': {
                'name': {'type': 'string', 'pattern': '^[a-z0-9](?:[a-z0-9-]{0,62}[a-z0-9])?$'},
                'containerfile': path, 'context': path,
                'platform': {'type': 'string', 'pattern': '^linux/[a-z0-9]+$'},
                'inputs': {'type': 'object', 'minProperties': 1, 'maxProperties': 128, 'additionalProperties': sha},
                'fetch': {'type': 'array', 'maxItems': 16, 'items': {
                    'type': 'object', 'additionalProperties': False, 'required': ['sha256', 'dest'],
                    'properties': {'url': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
                                   'url_from': {'type': 'string', 'minLength': 1, 'maxLength': 2000},
                                   'sha256': sha, 'dest': path}}},
                'tag': {'type': 'string', 'minLength': 1, 'maxLength': 200},
                'timeout_seconds': {'type': 'integer', 'minimum': 1, 'maximum': 7200},
            },
        },
    }
    v3['required_checks'] = {
        'type': 'array', 'maxItems': 50, 'uniqueItems': True,
        'items': {'type': 'string', 'pattern': '^[a-z0-9][a-z0-9-]{0,99}$'},
    }
    v3['provenance'] = {
        'type': 'object', 'minProperties': 1, 'maxProperties': 16,
        'propertyNames': {'pattern': '^[a-z][a-z0-9_]{0,63}$'},
        'additionalProperties': {'type': 'string', 'minLength': 1, 'maxLength': 256},
    }
    result['manifest-v3'] = {
        '$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object',
        'additionalProperties': False, 'required': list(result['manifest-v2']['required']),
        'properties': v3,
        'description': 'research-package/v3 is v2 plus optional tool_images, required_checks and provenance. Tag, hash, path and archive constraints are checked by research-runner verify.',
    }
    return result
