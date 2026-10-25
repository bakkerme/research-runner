"""Bound Codex telemetry independently of the agent's tool results."""
import json

MAX_EVENT_CHARS = 1024 * 1024
OUTPUT_FIELDS = {'aggregated_output', 'aggregatedOutput', 'output', 'delta'}


def encode_event(kind, data):
 def encode(value):
  return json.dumps({'kind': kind, 'data': value}, sort_keys=True, default=str)

 line = encode(data)
 if kind != 'codex' or len(line) + 1 <= MAX_EVENT_CHARS:
  return line
 original_size = len(line) + 1

 def clip(value):
  if isinstance(value, dict):
   return {key: (item[:4096] + '\n[Runner log output truncated]'
                 if key in OUTPUT_FIELDS and isinstance(item, str) and len(item) > 4096
                 else clip(item)) for key, item in value.items()}
  if isinstance(value, list):
   return [clip(item) for item in value]
  return value

 bounded = clip(data)
 bounded['runner_log'] = {'truncated': True, 'original_chars': original_size,
                         'message': 'Oversized Codex event; log output abbreviated. Agent tool results are unchanged.'}
 line = encode(bounded)
 if len(line) + 1 <= MAX_EVENT_CHARS:
  return line
 # Unrecognised or aggregate payloads can also be large. Keep lifecycle and
 # command identity without forwarding an unbounded payload to the controller.
 def identity(value):
  if not isinstance(value, dict):
   return {}
  return {key: value[key][:512] for key in
          ('id', 'type', 'status', 'item_id', 'itemId', 'thread_id', 'threadId',
           'turn_id', 'turnId', 'command') if isinstance(value.get(key), str)}

 payload = data.get('payload', {})
 summary = identity(payload)
 if isinstance(payload, dict):
  for key in ('item', 'turn', 'thread'):
   if key in payload:
    summary[key] = identity(payload[key])
 return encode({'method': str(data.get('method', ''))[:512], 'payload': summary,
                'runner_log': bounded['runner_log']})
