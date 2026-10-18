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


def load_contract(model, data):
    if len(data) > 65536:
        raise ValueError('contract exceeds 64 KiB')
    return model.model_validate_json(data).model_dump()


def is_mvp(manifest):
    return manifest['format'] == 'research-package/v2'


def capabilities():
    from .package import INPUT_MAX_FILE, INPUT_MAX_TOTAL, MAX_ARCHIVE, MAX_FILE, MAX_TOTAL, MAX_FILES
    return {
        'format': 'runner-capabilities/v1', 'version': '0.2.2',
        'package_formats': ['research-package/v1', 'research-package/v2'],
        'adapters': ['fixture', 'codex'], 'max_running_jobs': 1,
        'default_engine': 'host', 'execution_engines': ['host', 'docker'],
        'limits': {'file_bytes': INPUT_MAX_FILE, 'package_bytes': INPUT_MAX_TOTAL,
                   'archive_bytes': MAX_ARCHIVE, 'files': MAX_FILES, 'contract_bytes': 65536,
                   'output_file_bytes': MAX_FILE, 'output_total_bytes': MAX_TOTAL},
        'features': {'bounded_runs': True, 'graceful_finalize': True,
                     'worker_deadline_failsafe': True,
                     'container_auth_file_projection': True, 'container_auth_refresh_qualified': False,
                     'partial_artifacts': True, 'event_paging': True,
                     'datasets': False, 'automatic_retries': False,
                     'automatic_continuation': False, 'external_workers': False,
                     'domain_validation': False, 'budget_extension': False},
    }


def schemas():
    result = {name: model.model_json_schema() for name, model in
              [('policy', Policy), ('checkpoint', Checkpoint), ('result', Result)]}
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
    result['manifest-v2'] = {
        '$schema': 'https://json-schema.org/draft/2020-12/schema', 'type': 'object',
        'additionalProperties': False, 'required': list(properties), 'properties': properties,
        'description': 'Cross-field/path, dependency and archive constraints are additionally checked by research-runner verify.',
    }
    return result
