"""Bound worker telemetry independently of the agent's tool results."""
import json

MAX_EVENT_CHARS = 1024 * 1024
OUTPUT_FIELDS = {'aggregated_output', 'aggregatedOutput', 'output', 'delta'}


def encode_event(kind, data):
 def encode(value):
  return json.dumps({'kind': kind, 'data': value}, sort_keys=True, default=str)

 line = encode(data)
 if len(line) + 1 <= MAX_EVENT_CHARS:
  return line
 if kind != 'codex':
  bounded = _bound_generic(kind, data, len(line) + 1)
  bounded_line = encode(bounded)
  if len(bounded_line) + 1 <= MAX_EVENT_CHARS:
   return bounded_line
  # Identity fields only. A fileChange diff or exception string must not
  # remain large enough for the controller to reject the line.
  keep = {'runner_log': bounded.get('runner_log', {'truncated': True, 'original_chars': len(line) + 1,
          'message': 'Oversized worker event; payload omitted.'})}
  if isinstance(data, dict):
   for key in ('id', 'turn_id', 'method', 'message', 'type'):
    if isinstance(data.get(key), str):
     keep[key] = data[key][:512]
  return encode(keep)
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


def _bound_generic(kind, data, original_size):
 """Bound non-Codex events (approval diffs, exception text, progress).

 Walk a fixed node budget. Clipping every nested value without a cap copies
 a wide tree (an approval payload of nested objects) and can exhaust memory
 before the line-size check runs.
 """
 note = {'truncated': True, 'original_chars': original_size,
         'message': 'Oversized worker event; fields abbreviated.'}
 nodes = 0

 def event_len(value):
  return len(json.dumps({'kind': kind, 'data': value}, sort_keys=True, default=str)) + 1

 def clip(value, depth, budget):
  nonlocal nodes
  nodes += 1
  if isinstance(value, str):
   limit = min(4096, max(0, budget))
   if len(value) > limit:
    return value[:limit] + '\n[Runner log output truncated]'
   return value
  if isinstance(value, (int, float, bool)) or value is None:
   return value
  if nodes > 64 or depth <= 0 or budget <= 0 or not isinstance(value, (dict, list)):
   return '[Runner log: nested value omitted]'
  if isinstance(value, dict):
   out = {}
   used = 2
   for key, item in value.items():
    if len(out) >= 32 or used >= budget:
     break
    name = str(key)[:128]
    child = clip(item, depth - 1, min(4096, max(0, budget - used - len(name) - 4)))
    out[name] = child
    used += len(name) + len(json.dumps(child, default=str)) + 4
   return out
  out = []
  used = 2
  for item in value:
   if len(out) >= 32 or used >= budget:
    break
   child = clip(item, depth - 1, min(4096, max(0, budget - used - 2)))
   out.append(child)
   used += len(json.dumps(child, default=str)) + 2
  return out

 body = clip(data, 6, MAX_EVENT_CHARS // 2) if isinstance(data, dict) else {'value': clip(data, 6, 4096)}
 if not isinstance(body, dict):
  body = {'value': body}
 else:
  body = dict(body)
 if isinstance(data, dict):
  for key in ('id', 'attempt', 'turn_id', 'method', 'message', 'type'):
   if key in body:
    continue
   raw = data.get(key)
   if isinstance(raw, str):
    body[key] = raw[:512]
   elif key == 'attempt' and isinstance(raw, int) and not isinstance(raw, bool):
    body[key] = raw
 body['runner_log'] = note
 if event_len(body) <= MAX_EVENT_CHARS:
  return body
 keep = {}
 if isinstance(data, dict):
  for key in ('id', 'attempt', 'turn_id', 'method', 'message', 'type'):
   raw = data.get(key)
   if isinstance(raw, str):
    keep[key] = raw[:512]
 keep['runner_log'] = note
 return keep
