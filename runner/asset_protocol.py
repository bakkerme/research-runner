"""JSON Schema for the independent loader wire protocol (stdlib only)."""
PROTOCOL = 'runner-asset-loader/v1'


def schema():
    text = {'type': 'string', 'minLength': 1}
    digest = {'type': 'string', 'pattern': '^[a-f0-9]{64}$'}
    kind = {'type': 'string', 'pattern': '^[a-z][a-z0-9-]{0,63}$'}

    def obj(properties):
        return {'type': 'object', 'additionalProperties': False,
                'required': list(properties), 'properties': properties}

    def request(operation, extra):
        return obj({'protocol': {'const': PROTOCOL}, 'operation': {'const': operation},
                    'asset_type': kind, **extra})

    def result(extra):
        return obj({'event': {'const': 'result'}, 'protocol': {'const': PROTOCOL}, **extra})

    progress = obj({'event': {'const': 'progress'}, 'message': dict(text, maxLength=1000),
                    'bytes_completed': {'type': 'integer', 'minimum': 0},
                    'bytes_total': {'type': 'integer', 'minimum': 0}})
    progress['required'] = ['event', 'message']
    definitions = {
        'describe-request': request('describe', {}),
        'resolve-request': request('resolve', {'asset_id': dict(text, maxLength=2000)}),
        'materialize-request': request('materialize', {'asset_id': dict(text, maxLength=2000),
            'resolved_id': dict(text, maxLength=2000), 'sha256': digest, 'staging_directory': text}),
        'describe-result': result({'loader_version': dict(text, maxLength=100),
            'operations': {'type': 'array', 'items': {'enum': ['resolve', 'materialize']},
                           'uniqueItems': True, 'minItems': 2, 'maxItems': 2}}),
        'resolve-result': result({'resolved_id': dict(text, maxLength=2000), 'sha256': digest}),
        'materialize-result': result({'materialized': {'const': True}}),
        'progress': progress,
        'error': obj({'event': {'const': 'error'}, 'code': {'enum': ['not_found', 'access_denied', 'unavailable', 'invalid_request']}}),
    }
    return {'$schema': 'https://json-schema.org/draft/2020-12/schema',
            'title': PROTOCOL, '$defs': definitions,
            'anyOf': [{'$ref': '#/$defs/' + name} for name in definitions]}
