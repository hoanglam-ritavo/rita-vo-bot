"""Sequential socket HTTP runner with explicit preflight and private bindings."""
from collections import Counter
from .config import EvaluationError, digest
from .assertions import check_response
from .snapshot import build_bindings, read_snapshot


async def prepare(config, scenarios, inputs, catalog, root, *, snapshot_reader=read_snapshot):
    snapshot = await snapshot_reader(config, scenarios, catalog, root)
    records, roles = build_bindings(scenarios, snapshot)
    return {'schema_version': 1, 'mode': 'external-http-private-binding',
            'target_fingerprint': digest(config.url), 'inputs': inputs,
            'snapshot_fingerprint': snapshot['fingerprint'], 'business_day': snapshot['business_day'],
            'bindings': records, 'roles': roles, 'corpus_records': snapshot.get('corpus_records', {})}


def request_for(scenario, binding, key):
    body = {'text': scenario.text, 'hrm_user_id': binding['hrm_user_id'], 'top_k': 5}
    headers = {'X-Rita-Service-Key': key}
    name = scenario.protocol
    if name == 'missing_key':
        headers = {}
    elif name == 'wrong_key':
        headers['X-Rita-Service-Key'] = 'invalid-key' if key != 'invalid-key' else 'other-invalid-key'
    elif name == 'missing_identity':
        body.pop('hrm_user_id')
    elif name in ('null_identity','string_identity','boolean_identity','zero_identity'):
        body['hrm_user_id'] = {'null_identity': None, 'string_identity': str(binding['hrm_user_id']),
                               'boolean_identity': True, 'zero_identity': 0}[name]
    elif name == 'blank_text':
        body['text'] = '   '
    elif name in ('top_k_zero','top_k_large','top_k_boolean'):
        body['top_k'] = {'top_k_zero': 0, 'top_k_large': 21, 'top_k_boolean': True}[name]
    return body, headers


def result_row(s, state, reason=None, assertions=()):
    return {'case_id': s.case_id, 'endpoint': s.endpoint, 'group': s.group,
            'state': state, 'reason': reason, 'assertions': list(assertions)}


def report(rows, inputs, binding_hash, problem=None):
    counts = dict(Counter(r['state'] for r in rows))
    grouped = {}
    for r in rows:
        for group in (r['endpoint'], r['group']):
            grouped.setdefault(group, Counter())[r['state']] += 1
    exit_code = 2 if problem or counts.get('blocked') else 1 if counts.get('failed') else 0
    return {'schema_version': 1, 'mode': 'external-http-e2e', 'independent_acceptance': False,
            'inputs': inputs, 'binding_fingerprint': binding_hash, 'problem': problem,
            'counts': counts, 'groups': {k: dict(v) for k, v in grouped.items()},
            'results': rows, 'exit_code': exit_code}


async def run(config, scenarios, inputs, catalog, root, binding, binding_hash, *,
              snapshot_reader=read_snapshot, transport=None):
    """Only explicit run performs HTTP; transport injection is solely for local tests."""
    import httpx
    rows = [result_row(s, 'not_applicable' if s.reason else 'blocked',
                       s.reason or 'preflight_not_completed') for s in scenarios]
    snapshot_started = False
    problem = None
    try:
        if (type(binding.get('schema_version')) is not int or binding['schema_version'] != 1
                or binding.get('mode') != 'external-http-private-binding'
                or digest(binding) != binding_hash or binding.get('inputs') != inputs
                or binding.get('target_fingerprint') != digest(config.url)):
            raise EvaluationError('binding_mismatch')
        required = {s.key for s in scenarios if not s.reason}
        if set(binding.get('bindings', {})) != required:
            raise EvaluationError('binding_coverage')
        for b in binding['bindings'].values():
            if (type(b.get('hrm_user_id')) is not int or not 0 < b['hrm_user_id'] <= 2**53-1
                    or b.get('state') not in ('ready','blocked') or not isinstance(b.get('expected_values'), dict)):
                raise EvaluationError('invalid_binding')
        async with httpx.AsyncClient(base_url=config.url, timeout=150.0, follow_redirects=False,
                                     trust_env=False, transport=transport) as client:
            for path, expected in [('/health/live', 'alive'), ('/health/ready', 'ready'), ('/health/answer', 'ready')]:
                headers = {} if path.endswith('/live') else {'X-Rita-Service-Key': config.service_key}
                try:
                    response = await client.get(path, headers=headers, timeout=20.0)
                    health = response.json()
                except Exception:
                    raise EvaluationError('health_unavailable') from None
                if response.status_code != 200 or not isinstance(health, dict) or health.get('status') != expected:
                    raise EvaluationError('health_not_ready')
                if path == '/health/answer' and health.get('reason') is not None:
                    raise EvaluationError('health_not_ready')
            current = await snapshot_reader(config, scenarios, catalog, root)
            if (current['fingerprint'] != binding['snapshot_fingerprint']
                    or current['business_day'] != binding['business_day']):
                raise EvaluationError('snapshot_drift_before_run')
            expected_bindings, expected_roles = build_bindings(scenarios, current)
            if (expected_bindings != binding['bindings'] or expected_roles != binding.get('roles')
                    or current.get('corpus_records', {}) != binding.get('corpus_records')):
                raise EvaluationError('binding_expectations_changed')
            snapshot_started = True
            stopped = False
            for i, s in enumerate(scenarios):
                if s.reason:
                    continue
                b = binding['bindings'][s.key]
                if stopped:
                    rows[i] = result_row(s, 'blocked', 'run_interrupted')
                elif b['state'] == 'blocked':
                    rows[i] = result_row(s, 'blocked', b['reason'])
                else:
                    body, headers = request_for(s, b, config.service_key)
                    try:
                        response = await client.post(s.endpoint, json=body, headers=headers)
                        try:
                            payload = response.json()
                        except ValueError:
                            payload = None
                        failures = check_response(s, b, response.status_code, payload,
                            response.headers, catalog, binding['business_day'],
                            corpus_records=binding['corpus_records'])
                        rows[i] = result_row(s, 'failed' if failures else 'passed', assertions=failures)
                        if response.status_code in (429, 503, 504):
                            stopped = True
                            problem = 'service_execution_interrupted'
                    except httpx.HTTPError:
                        rows[i] = result_row(s, 'failed', assertions=['http_transport'])
                        stopped = True
                        problem = 'http_transport'
                    except Exception:
                        rows[i] = result_row(s, 'failed', assertions=['invalid_response'])
            final = await snapshot_reader(config, scenarios, catalog, root)
            if final['fingerprint'] != binding['snapshot_fingerprint']:
                problem = 'snapshot_drift_after_run'
                # Responses still existed, but no passing conclusion survives drift.
                for row in rows:
                    if row['state'] == 'passed':
                        row.update(state='blocked', reason='snapshot_drift_after_run')
    except EvaluationError as error:
        problem = str(error)
    except Exception:
        problem = 'execution_unavailable'
    if problem and snapshot_started:
        # A failed final snapshot cannot leave earlier passes as verified.
        if problem == 'execution_unavailable':
            for row in rows:
                if row['state'] == 'passed':
                    row.update(state='blocked', reason='final_snapshot_unavailable')
    return report(rows, inputs, binding_hash, problem)
