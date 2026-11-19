"""Black-box assertions: authored evidence requirements, not response-derived baselines."""
from datetime import date, datetime
from calendar import monthrange
import hashlib
import re
import unicodedata

from src.evaluation.questions import normalize_whitespace, policy_content_failures
from .inventory import RETRIEVAL_RULES, retrieval_rule, task_sections

SOURCE_METADATA = ('title', 'applicability', 'effective_from', 'effective_to', 'version_label')
ORIGIN_FIELDS = ('chunk_id', 'revision_id', 'document_id', 'run_id', 'source_path', 'source_spans')
DIRECT_EMPLOYEE_LABELS = {
    'employee_name': 'Họ tên', 'employee_code': 'Mã nhân viên', 'job_title': 'Chức danh',
    'department_name': 'Phòng ban', 'manager_name': 'Quản lý trực tiếp',
    'probation_date': 'Ngày bắt đầu thử việc', 'probation_end_date': 'Ngày kết thúc thử việc',
    'official_date': 'Ngày chính thức', 'resignation_date': 'Ngày nghỉ việc',
}
LEAVE_SCOPE_LABELS = {'workit': 'Workit', 'eleave': 'E-Leave'}


def folded(value):
    return ''.join(c for c in unicodedata.normalize('NFD', value.casefold().replace('đ', 'd'))
                   if not unicodedata.combining(c))


def question_dates(text):
    dates = []
    for raw in re.findall(r'\b(?:\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b', text):
        try:
            dates.append((date.fromisoformat(raw) if '-' in raw else
                          datetime.strptime(raw, '%d/%m/%Y').date()).isoformat())
        except ValueError:
            pass
    return dates


def calculation_expectation(scenario, binding, catalog, business_day):
    if not scenario.endpoint.endswith('/answer'):
        return None
    cards = {c['id']: c for c in catalog['cards']}
    specs = [cards[t].get('context', {}) for t in scenario.oracle.get('task_ids_any', [])]
    defined = [s for s in specs if s.get('operation') and s.get('date_field')]
    text = folded(scenario.text)
    operation, field = None, None
    if len(defined) == 1:
        operation, field = defined[0]['operation'], defined[0]['date_field']
    elif 'bao nhieu ngay' in text or 'bao nhieu thang' in text:
        fields = [k for k in scenario.oracle.get('employee_fields', []) if k.endswith('_date')]
        if len(fields) == 1:
            operation = 'completed_months' if 'bao nhieu thang' in text else 'interval_days'
            field = fields[0]
    dates = question_dates(scenario.text)
    as_of = dates[-1] if dates else business_day if 'hom nay' in text else None
    start = binding.get('expected_values', {}).get(field)
    if not operation or not start or not as_of:
        return None
    start, end = date.fromisoformat(start), date.fromisoformat(as_of)
    # Independent calendar oracle, not the application's calculate().
    anniversary_day = min(start.day, monthrange(end.year, end.month)[1])
    amount = ((end-start).days if operation == 'interval_days' else
              (end.year-start.year)*12 + end.month-start.month - (end.day < anniversary_day))
    if amount < 0:
        return None
    return {'operation': operation, 'value': str(amount), 'unit': 'day' if operation == 'interval_days' else 'month',
            'as_of': as_of, 'field': field, 'policy_rule': bool(defined)}


def employee_claims_match(fields, values, employee, claims):
    by_field = {node['field']: node for node in employee}
    direct_claims = {(claim['evidence_refs'][0], normalize_whitespace(claim['text'])) for claim in claims
                     if len(claim['evidence_refs']) == 1}
    for field in fields:
        label = DIRECT_EMPLOYEE_LABELS.get(field)
        node = by_field.get(field)
        if label is None or node is None or node['label'] != label or field not in values:
            return False
        value = values[field]
        if value is not None and field.endswith('_date'):
            try:
                value = date.fromisoformat(value).strftime('%d/%m/%Y')
            except ValueError:
                return False
        expected = (f'{label} của bạn: {value}.' if value is not None else
                    f'Hồ sơ hiện chưa có thông tin về {label.lower()}.')
        if (node['id'], normalize_whitespace(expected)) not in direct_claims:
            return False
    return True


def check_response(scenario, binding, status, body, headers, catalog, business_day, *, corpus_records=None):
    failures = []
    o = scenario.oracle
    expected_http = o.get('http_status', 200)
    if status != expected_http:
        return ['http_status']
    if headers.get('cache-control', '').lower() != 'no-store':
        failures.append('no_store')
    if status != 200:
        if not isinstance(body, dict) or body.get('error', {}).get('code') != o.get('error_code'):
            failures.append('error_code')
        return failures
    try:
        from src.api.contracts import UnifiedAnswerResponse, UnifiedRetrievalResponse
        model = UnifiedAnswerResponse if scenario.endpoint.endswith('/answer') else UnifiedRetrievalResponse
        result = model.model_validate(body).model_dump()
    except Exception:
        return failures + ['response_schema']
    if body.get('schema_version') != 2:
        failures.append('schema_version')
    answer = scenario.endpoint.endswith('/answer')
    statuses = o.get('statuses', []) if answer else (
        o.get('retrieve_statuses') or RETRIEVAL_RULES[retrieval_rule(scenario)])
    if result['status'] not in statuses:
        failures.append('status')
    if o.get('application_state') and result['application']['state'] != o['application_state']:
        failures.append('application_state')
    if binding['expected_values'] and result['application']['state'] in ('disabled','unavailable','not_found'):
        failures.append('employee_source_not_applied')
    evidence = result['evidence']
    employee = [n for n in evidence if n['kind'] == 'employee']
    actual = {n['field']: n['value'] for n in employee}
    if set(actual) != set(o.get('employee_fields', [])) or len(employee) != len(actual):
        failures.append('employee_fields')
    if actual != binding['expected_values']:
        failures.append('employee_values')
    for node in employee:
        if node['subject'] != ('manager' if node['field'].startswith('manager_') else 'self'):
            failures.append('employee_subject')
    if any(actual.get(k) is not None or k not in actual for k in o.get('null_fields', [])):
        failures.append('null_field')
    if answer:
        sources = result['sources']
        documents = {origin['document_id'] for s in sources for origin in s['origins']}
    else:
        sources = []
        documents = {m['document_id'] for m in result['matches']}
        if len(result['matches']) > 5:
            failures.append('top_k')
        if result['matches'] and (result['document_status'] != 'matches_found' or
                result['relevance']['status'] != 'unassessed'):
            failures.append('retrieval_semantics')
        if not result['matches'] and result['document_status'] == 'matches_found':
            failures.append('document_status')
        if (o.get('retrieve_documents') == 'none' and (result['matches'] or result['document_status'] is not None)
                or o.get('retrieve_documents') == 'required' and not result['matches']):
            failures.append('retrieve_document_lookup')
        if result['matches'] and 'evidence_completeness_not_assessed' not in result['relevance']['warnings']:
            failures.append('retrieval_warning')
        if corpus_records is not None:
            for match in result['matches']:
                pinned = corpus_records.get(match['chunk_id'])
                if (pinned is None or pinned['content_hash'] != hashlib.sha256(match['content'].encode()).hexdigest()
                        or any(match.get(k) != v for k,v in pinned.items() if k != 'content_hash')):
                    failures.append('retrieval_snapshot_provenance')
        if any(n['kind'] in ('calculation', 'document') for n in evidence):
            failures.append('retrieve_does_not_answer')
    if any(not documents.intersection(group) for group in o.get('required_source_groups', [])):
        failures.append('required_source')
    forbidden_documents = o.get('forbidden_documents', [])
    if not answer and o.get('retrieve_forbidden_documents') is not None:
        forbidden_documents = o['retrieve_forbidden_documents']
    if documents.intersection(forbidden_documents):
        failures.append('forbidden_source')
    # Exact policy provenance comes from authored refs, never from nearest matches.
    cardmap = {c['id']: c for c in catalog['cards']}
    target_ids = set(o.get('task_ids_any', []))
    relevant = [r for c in cardmap.values() for section in c['sections'].values()
                for unit in section['units'] for r in unit['refs']]
    units_by_task = {tid: [unit for section in task_sections(scenario, cardmap[tid]).values()
                          for unit in section['units']] for tid in target_ids}
    units = [unit for task_units in units_by_task.values() for unit in task_units]
    target_refs = [ref for unit in units for ref in unit['refs']]
    def address(origin, ref):
        # Answer origins expose chunk_id, not chunk_index. Resolve that ID from
        # the independently pinned SQL snapshot; retrieval matches expose both.
        row = origin
        if 'chunk_index' not in row and corpus_records is not None:
            row = corpus_records.get(origin.get('chunk_id'), {})
        return (row.get('document_id') == ref['document_id'] and
                row.get('revision_id') == ref['revision_id'] and
                ('chunk_index' not in row and corpus_records is None or row.get('chunk_index') == ref['chunk_index']))
    if not answer and target_refs and o.get('required_source_groups'):
        if not result['matches'] or not any(address(result['matches'][0], r) for r in target_refs):
            failures.append('expected_first')
    nodes = {n['id']: n for n in evidence}
    policy_claims = []
    if answer:
        source_map = {s['source_id']: s for s in sources}
        excerpt = o.get('answer_kind') == 'excerpt'
        if (result['fallback_reason'] == 'document_lookup') != excerpt:
            failures.append('answer_kind')
        cited_ids = {key for claim in result['claims'] for key in claim['evidence_refs']}
        if any(node['id'] not in cited_ids for node in employee):
            failures.append('employee_claim_evidence')
        if not employee_claims_match(o.get('expected_employee_claim_fields', []),
                                     binding['expected_values'], employee, result['claims']):
            failures.append('employee_claim_value')
        for n in evidence:
            if n['kind'] != 'document':
                continue
            source = source_map[n['source_id']]
            if corpus_records is not None or excerpt:
                for origin in source['origins']:
                    pinned = (corpus_records or {}).get(origin.get('chunk_id'))
                    if pinned is None or any(origin.get(k) != pinned.get(k) for k in ORIGIN_FIELDS):
                        failures.append('answer_snapshot_provenance')
                    elif excerpt and (pinned['content_hash'] != hashlib.sha256(n['quote'].encode()).hexdigest()
                            or any(source.get(k) != pinned.get(k) for k in SOURCE_METADATA)):
                        failures.append('excerpt_snapshot_content')
            if excerpt:
                if not source['origins']:
                    failures.append('answer_snapshot_provenance')
                continue
            candidates = [r for r in relevant if any(address(origin, r) for origin in source['origins'])]
            if not any(n['quote'] == r['quote'] and all(source.get(k) == r['metadata'].get(k)
                       for k in SOURCE_METADATA) for r in candidates):
                failures.append('citation_provenance')
        policy_claims = [c for c in result['claims'] if any(nodes[k]['kind'] == 'document' for k in c['evidence_refs'])]

        def backs_unit(claim, unit):
            cited = [nodes[key] for key in claim['evidence_refs'] if nodes[key]['kind'] == 'document']
            return bool(unit['refs']) and all(any(node['quote'] == ref['quote'] and any(address(origin, ref)
                for origin in source_map[node['source_id']]['origins']) for node in cited) for ref in unit['refs'])

        display = [f"{claim['text']} [{claim['id']}]" for claim in result['claims']] or result['limitations']
        lines = result['answer'].splitlines()
        if policy_claims and lines and normalize_whitespace(lines[0]).startswith('Phạm vi trả lời là '):
            scope_labels = set()
            for tid, task_units in units_by_task.items():
                if any(backs_unit(claim, unit) for unit in task_units for claim in policy_claims):
                    scope_labels.add(cardmap[tid].get('label'))
                    if tid.startswith('task.leave.'):
                        scope_labels.add(LEAVE_SCOPE_LABELS.get(tid.split('.')[2]))
            if normalize_whitespace(lines[0]) in {f'Phạm vi trả lời là {label}.' for label in scope_labels if label}:
                lines = lines[1:]
        if not display or normalize_whitespace(' '.join(lines)) != normalize_whitespace(' '.join(display)):
            failures.append('answer_display')

        failures.extend(policy_content_failures(o.get('expected_policy_claim_texts'), o.get('required_limitations'),
                                                policy_claims, result['limitations']))
        if excerpt:
            if any(len(c['evidence_refs']) != 1 or c['text'] != nodes[c['evidence_refs'][0]]['quote']
                   for c in policy_claims):
                failures.append('excerpt_claim_text')
        elif target_refs and o.get('claims') == 'required':
            def backed(claim):
                return any(backs_unit(claim, unit) and (not o.get('expected_policy_claim_texts')
                    or normalize_whitespace(claim['text']) == normalize_whitespace(unit.get('text', '')))
                    for unit in units)
            if not any(map(backed, policy_claims)) or (o.get('expected_aspects') or o.get('expected_policy_claim_texts')) and not all(map(backed, policy_claims)):
                failures.append('required_task_evidence')
        if o.get('claims') == 'required' and not policy_claims or o.get('claims') == 'none' and policy_claims:
            failures.append('policy_claims')
        text = ' '.join(c['text'] for c in policy_claims)
        numbers = {int(re.sub(r'[.,]', '', n)) for n in re.findall(r'\d{1,3}(?:[.,]\d{3})+(?!\d)|\d+', text)}
        if not set(o.get('required_numbers', [])) <= numbers:
            failures.append('required_numbers')
        forbidden = [r for tid in o.get('forbidden_task_ids', []) for section in cardmap[tid]['sections'].values()
                     for u in section['units'] for r in u['refs']]
        allowed_addresses = {(r['document_id'], r['revision_id'], r['chunk_index']) for r in target_refs}
        forbidden = [r for r in forbidden if (r['document_id'],r['revision_id'],r['chunk_index']) not in allowed_addresses]
        if any(address(origin, r) for s in sources for origin in s['origins'] for r in forbidden):
            failures.append('forbidden_task_source')
    for n in evidence:
        if n['kind'] == 'user_input':
            dates = question_dates(scenario.text)
            if folded(n['value']) not in folded(scenario.text) and n['value'] not in dates:
                failures.append('user_input_provenance')
    if answer:
        failures.extend(calculation_failures(scenario, binding, result, catalog, business_day))
    return sorted(set(failures))


def calculation_failures(scenario, binding, result, catalog, business_day):
    """Check the result and both provenance inputs against independent expectations."""
    nodes = {node['id']: node for node in result['evidence']}
    calculations = [node for node in nodes.values() if node['kind'] == 'calculation']
    required = scenario.oracle.get('calculation_required')
    expected = None if required is False else calculation_expectation(scenario, binding, catalog, business_day)
    if expected is None:
        if required:
            return ['calculation_expectation_missing']
        return ['unexpected_calculation'] if calculations else []
    if len(calculations) != 1:
        return ['calculation_value']
    calculation = calculations[0]
    if any(calculation[key] != expected[key] for key in ('operation', 'value', 'unit', 'as_of')):
        return ['calculation_value']
    inputs = [nodes[key] for key in calculation['input_refs'] if key in nodes]
    if (not any(n['kind'] == 'employee' and n['field'] == expected['field'] for n in inputs)
            or not any(n['kind'] == 'user_input' and n['slot'] == 'as_of' and n['value'] == expected['as_of']
                       for n in inputs)):
        return ['calculation_input']
    if expected['policy_rule'] and not calculation['rule_refs']:
        return ['calculation_rule']
    if not any(calculation['id'] in claim['evidence_refs'] for claim in result['claims']):
        return ['calculation_claim']
    return []
