"""Future opt-in SELECT-only snapshot preparation; never called at import/inventory."""
from collections import defaultdict
from datetime import date, datetime, timezone
from pathlib import Path
import hashlib

from .config import EvaluationError, digest
from .inventory import task_sections
from .assertions import question_dates
from src.evaluation.questions import normalize_whitespace


def display(value):
    if value is None:
        return None
    if isinstance(value, (date, datetime)):
        return value.isoformat()
    if isinstance(value, bool):
        return str(value)
    if not isinstance(value, str):
        raise EvaluationError('unsupported_snapshot_value')
    value = value.strip()
    return None if value in ('', 'NULL') else value


def representatives(rows, key='user_id'):
    """Independent evaluation oracle for raw SQL rows, not production select_rows.

    Rank columns are prescribed by the employee-store contract. Normalization and
    source validation remain owned by that module; this is test expectation logic.
    """
    from src.type_b.employee_store.fields import RANK_TEXT_FIELDS
    groups = defaultdict(list)
    for row in rows:
        if row[key] is not None:
            groups[row[key]].append(row)
    chosen = {}
    for identity, candidates in groups.items():
        def rank(r):
            stamp = r['updated_at']
            if isinstance(stamp, str):
                stamp = datetime.fromisoformat(stamp.replace('Z', '+00:00'))
            score = sum(isinstance(r.get(k), str) and bool(r[k].strip()) for k in RANK_TEXT_FIELDS)
            score += type(r.get('active')) is bool
            return stamp is not None, stamp or datetime.min.replace(tzinfo=timezone.utc), score, r['employee_id']
        best = sorted(candidates, key=rank, reverse=True)
        if len(best) > 1 and rank(best[0]) == rank(best[1]) and best[0] != best[1]:
            continue  # No arbitrary expected winner for conflicting ties.
        chosen[identity] = best[0]
    return chosen


def employee_facts(row, rows, selected=None):
    from src.answering.context_contracts import EmployeeField
    from typing import get_args
    allowed = get_args(EmployeeField)
    facts = {k: display(row.get(k)) for k in allowed if k not in ('manager_name', 'manager_email')}
    manager = None
    selected = representatives(rows, 'employee_id') if selected is None else selected
    if row.get('manager_id') is not None:
        manager = selected.get(row['manager_id'])
    elif row.get('manager_code'):
        code = display(row['manager_code'])
        options = [r for r in selected.values() if display(r.get('employee_code')) == code]
        if len(options) == 1:
            manager = options[0]
    # A stale/wrong link or self reference never supplies expected manager facts.
    if manager and (manager['employee_id'] == row['employee_id'] or
            manager.get('user_id') is not None and manager['user_id'] == row['user_id'] or
            row.get('manager_code') and display(row['manager_code']) != display(manager.get('employee_code'))):
        manager = None
    facts['manager_name'] = display(manager.get('employee_name')) if manager else None
    facts['manager_email'] = display(manager.get('work_email')) if manager else None
    return facts


def build_bindings(scenarios, snapshot):
    """Choose before HTTP; private values never enter public result records."""
    rows = snapshot['employees']
    selected = representatives(rows)
    managers = representatives(rows, 'employee_id')
    profiles = [(uid, r, employee_facts(r, rows, managers)) for uid, r in sorted(selected.items())]
    records = {}
    base = next((uid for uid, _, _ in profiles), None)
    if base is None:
        raise EvaluationError('no_test_identity')
    roles = {}
    for name, predicate in {
        'complete_work': lambda r, v: all(v.get(k) for k in ('employee_name','employee_code','job_title','department_name')),
        'inactive': lambda r, v: r.get('active') is False and v.get('job_title') is not None,
        'duplicate_user': lambda r, v: v.get('employee_code') is not None and sum(x['user_id'] == r['user_id'] for x in rows) > 1,
        'manager': lambda r, v: v['manager_name'] is not None,
    }.items():
        roles[name] = next((uid for uid, r, v in profiles if predicate(r, v)), None)
    existing = {r['user_id'] for r in rows}
    missing = max([x for x in existing if x is not None] + [0]) + 1
    roles['not_found'] = missing if missing <= 2**53-1 else next((x for x in range(1, len(existing)+2) if x not in existing), None)
    for s in scenarios:
        if s.reason:
            continue
        fields = s.oracle.get('employee_fields', [])
        nulls = set(s.oracle.get('null_fields', []))
        choices = [(uid, row, values) for uid, row, values in profiles
                   if all(values.get(k) is None if k in nulls else values.get(k) is not None for k in fields)]
        if s.oracle.get('calculation_required'):
            dates = question_dates(s.text)
            date_fields = [key for key in fields if key.endswith('_date')]
            if not dates or len(date_fields) != 1:
                raise EvaluationError('invalid_calculation_scenario')
            choices = [item for item in choices if item[2][date_fields[0]] <= dates[-1]]
        entry = {'role': 'self:' + digest([sorted(fields), sorted(nulls)])[:12],
                 'hrm_user_id': base, 'expected_values': {}, 'null_fields': sorted(nulls),
                 'state': 'ready', 'reason': None}
        role = next((t.split(':', 1)[1] for t in s.tags if t.startswith('role:')), None)
        if role:
            entry['role'] = role
            choices = [item for item in choices if item[0] == roles.get(role)]
            if role == 'not_found':
                entry['hrm_user_id'] = roles[role]
            elif not choices:
                entry.update(state='blocked', reason='profile_role_missing')
        if fields:
            if not choices:
                entry.update(state='blocked', reason='profile_role_missing')
            else:
                uid, row, values = choices[0]
                entry.update(hrm_user_id=uid, expected_values={k: values[k] for k in fields})
        groups = s.oracle.get('required_source_groups', [])
        if any(not set(group).intersection(snapshot['documents']) for group in groups):
            entry.update(state='blocked', reason='policy_source_missing')
        bundles = snapshot.get('task_requirements', {}).get(s.key, [])
        if bundles and not any(set(bundle) <= set(snapshot['valid_references']) for bundle in bundles):
            entry.update(state='blocked', reason='policy_reference_missing')
        records[s.key] = entry
    return records, roles


def reference_key(ref):
    return digest(ref)


def reference_inventory(scenarios, catalog):
    cards = {c['id']: c for c in catalog['cards']}
    refs, requirements = {}, {}
    for s in scenarios:
        if s.reason or s.protocol or s.oracle.get('answer_kind') == 'excerpt':
            continue
        oracle = s.oracle
        retrieve = s.endpoint.endswith('/retrieve')
        if retrieve and not oracle.get('required_source_groups'):
            continue
        bundles = []
        for tid in oracle.get('task_ids_any', []):
            units = []
            for section in task_sections(s, cards[tid]).values():
                for unit in section['units']:
                    keys = []
                    for ref in unit['refs']:
                        key = reference_key(ref)
                        refs[key] = ref
                        keys.append(key)
                    units.append((unit.get('text', ''), keys))
            if not units:
                continue
            exact = oracle.get('expected_policy_claim_texts')
            if retrieve:
                bundles.extend([key] for _, keys in units for key in keys)
            elif exact:
                # A claim may occur in more than one section or source. Keep
                # alternative sufficient reference sets for that text.
                authored = defaultdict(list)
                for text, keys in units:
                    authored[normalize_whitespace(text)].append(set(keys))
                choices = [authored[normalize_whitespace(text)] for text in exact]
                if all(choices):
                    from itertools import product
                    bundles.extend(sorted(set().union(*selection)) for selection in product(*choices))
            elif oracle.get('claims') == 'required' and 'partial' in oracle.get('statuses', []):
                bundles.extend(sorted(set(keys)) for _, keys in units)
            elif oracle.get('claims') == 'required' or oracle.get('statuses') == ['answered']:
                bundles.append(sorted({key for _, keys in units for key in keys}))
        if (oracle.get('expected_policy_claim_texts') and not retrieve and not bundles):
            raise EvaluationError('unknown_policy_claim_reference')
        requirements[s.key] = bundles
    return refs, requirements


async def read_snapshot(config, scenarios, catalog, root):
    """Network/DB use only on explicit prepare/run, using bounded readonly scopes."""
    refs, requirements = reference_inventory(scenarios, catalog)
    from sqlalchemy import text
    from sqlalchemy.ext.asyncio import create_async_engine
    from src.database.config import validate_url
    from src.type_b.employee_store import EmployeeSourceService, EmployeeSourceSettings
    from src.type_b.employee_store.fields import FIELD_REGISTRY, WORK_FIELDS
    from src.type_b.employee_store.connection import ReadOnlySource
    from src.type_b.employee_store.repository import EmployeeRepository
    from src.artifact_authoring.sources import decode, checked
    # Metadata/privilege validation is reused; no test DML or source mutations.
    settings = EmployeeSourceSettings(validate_url(config.hrm_url, 'TEST_HRM_DATABASE_URL'), config.schema)
    service = EmployeeSourceService(settings)
    source = ReadOnlySource(settings)
    try:
        await service.check_source()
        requested = {k for s in scenarios if not s.reason for k in s.oracle.get('employee_fields', [])}
        requested -= {'manager_name', 'manager_email'}
        names = set(WORK_FIELDS) | requested
        if not names <= set(FIELD_REGISTRY):
            raise EvaluationError('invalid_employee_projection')
        names = sorted(names)
        projection = EmployeeRepository(config.schema)._projection(names)
        async with source._read_scope() as connection:
            # Only needed work/context fields; one REPEATABLE READ snapshot.
            result = await connection.execute(text(f'SELECT {projection} FROM "{config.schema}"."employee_sso_store" ORDER BY employee_id, user_id, updated_at, id LIMIT 10001'))
            rows = [dict(row) for row in result.mappings()]
            if len(rows) > 10000:
                raise EvaluationError('employee_survey_too_large')
    finally:
        try:
            await service.close()
        finally:
            await source.close()

    manifest = decode(checked(Path(root) / 'artifacts/runtime-assets.json').read_bytes())
    pin = manifest['models']['e5']
    url = validate_url(config.corpus_url, 'DATABASE_URL')
    engine = create_async_engine(url, echo=False, hide_parameters=True, pool_size=1, max_overflow=0,
        isolation_level='REPEATABLE READ', connect_args={'connect_timeout': 10, 'options':
            '-c default_transaction_read_only=on -c statement_timeout=10000'})
    from src.business_calendar import business_today
    today = business_today().isoformat()
    query = text('''SELECT r.document_id::text, r.id::text AS revision_id, r.sha256,
        r.title, r.source_path, r.applicability, r.version_label,
        r.effective_from::text, r.effective_to::text, c.chunk_index, c.content,
        c.id::text AS chunk_id, i.id::text AS run_id, c.author_id, c.role,
        c.source_location->'spans' AS source_spans
        FROM document_chunks c JOIN ingestion_runs i ON i.id = c.run_id
        JOIN document_revisions r ON r.id = i.revision_id
        WHERE i.status = 'succeeded' AND i.is_active AND r.is_catalog_current
        AND r.indexing_decision = 'include' AND r.lifecycle_status = 'active'
        AND (r.effective_from IS NULL OR r.effective_from <= CAST(:today AS date))
        AND (r.effective_to IS NULL OR r.effective_to >= CAST(:today AS date))
        AND EXISTS (SELECT 1 FROM document_access_rules a WHERE a.revision_id = r.id AND a.group_name = 'public')
        AND i.model_name = 'intfloat/multilingual-e5-small' AND i.model_revision = :revision
        AND i.embedding_dimensions = 384 AND c.embedding IS NOT NULL
        AND i.configuration->>'model_files_sha256' = :fingerprint
        AND i.configuration->>'normalize_embeddings' = 'true'
        AND i.configuration->>'prefix' = 'passage: '
        ORDER BY r.document_id, r.id, c.chunk_index, i.id''')
    try:
        async with engine.connect() as connection:
            async with connection.begin():
                await connection.execute(text('SET TRANSACTION READ ONLY'))
                if await connection.scalar(text('SHOW transaction_read_only')) != 'on':
                    raise EvaluationError('readonly_required')
                result = await connection.execute(query, {'today': today, 'revision': pin['revision'],
                                                         'fingerprint': pin['files_sha256']})
                corpus = [dict(row) for row in result.mappings()]
    finally:
        await engine.dispose()
    by_address = defaultdict(list)
    for row in corpus:
        by_address[(row['document_id'], row['revision_id'], row['chunk_index'])].append(row)
    valid = []
    for key, ref in refs.items():
        candidates = by_address[(ref['document_id'], ref['revision_id'], ref['chunk_index'])]
        if len(candidates) != 1:
            continue
        row = candidates[0]
        if (row['sha256'] == ref['source_sha256'] and
                hashlib.sha256(row['content'].encode()).hexdigest() == ref['content_sha256'] and
                ref['quote'] in row['content'] and
                all(row.get(k) == v for k, v in ref['metadata'].items())):
            valid.append(key)
    # Content is transient; only hashes/addresses are persisted, never source text.
    identity = {'employees': rows, 'corpus': [{k: v for k, v in r.items() if k != 'content'} |
                {'content_hash': hashlib.sha256(r['content'].encode()).hexdigest()} for r in corpus],
                'business_day': today, 'target': digest([config.hrm_url, config.schema, config.corpus_url])}
    return {'employees': rows, 'documents': sorted({r['document_id'] for r in corpus}),
            'valid_references': sorted(valid), 'task_requirements': requirements,
            'fingerprint': digest(identity), 'business_day': today,
            'corpus_records': {r['chunk_id']: {k:v for k,v in r.items() if k not in ('content','sha256')} |
                {'content_hash': hashlib.sha256(r['content'].encode()).hexdigest()} for r in corpus}}
