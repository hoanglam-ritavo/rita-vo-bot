"""Black-box assertions: authored evidence requirements, not response-derived baselines."""
from datetime import date, datetime
from calendar import monthrange
import re
import unicodedata

from .inventory import RETRIEVAL_RULES, retrieval_rule


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
    statuses = o.get('statuses', []) if answer else RETRIEVAL_RULES[retrieval_rule(scenario)]
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
        if result['matches'] and 'evidence_completeness_not_assessed' not in result['relevance']['warnings']:
            failures.append('retrieval_warning')
        if corpus_records is not None:
            import hashlib
            for match in result['matches']:
                pinned = corpus_records.get(match['chunk_id'])
                if (pinned is None or pinned['content_hash'] != hashlib.sha256(match['content'].encode()).hexdigest()
                        or any(match.get(k) != v for k,v in pinned.items() if k != 'content_hash')):
                    failures.append('retrieval_snapshot_provenance')
        if any(n['kind'] in ('calculation', 'document') for n in evidence):
            failures.append('retrieve_does_not_answer')
    if any(not documents.intersection(group) for group in o.get('required_source_groups', [])):
        failures.append('required_source')
    if documents.intersection(o.get('forbidden_documents', [])):
        failures.append('forbidden_source')
    # Exact policy provenance comes from authored refs, never from nearest matches.
    cardmap = {c['id']: c for c in catalog['cards']}
    target_ids = set(o.get('task_ids_any', []))
    relevant = [r for c in cardmap.values() for section in c['sections'].values()
                for unit in section['units'] for r in unit['refs']]
    aspects = {t.split(':', 1)[1] for t in scenario.tags if t.startswith('aspect:')}
    target_refs = [r for tid in target_ids for aspect, section in cardmap[tid]['sections'].items()
                   if not aspects or aspect in aspects
                   for unit in section['units'] for r in unit['refs']]
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
        for n in evidence:
            if n['kind'] != 'document':
                continue
            source = source_map[n['source_id']]
            if corpus_records is not None:
                for origin in source['origins']:
                    pinned = corpus_records.get(origin.get('chunk_id'))
                    if pinned is None or any(origin.get(k) != pinned.get(k) for k in
                            ('chunk_id','revision_id','document_id','run_id','source_path','source_spans')):
                        failures.append('answer_snapshot_provenance')
            candidates = [r for r in relevant if any(address(origin, r) for origin in source['origins'])]
            if not any(n['quote'] in r['quote'] and all(source.get(k) == r['metadata'].get(k)
                       for k in ('title','applicability','effective_from','effective_to','version_label')) for r in candidates):
                failures.append('citation_provenance')
        policy_claims = [c for c in result['claims'] if any(nodes[k]['kind'] == 'document' for k in c['evidence_refs'])]
        cited = {key for c in policy_claims for key in c['evidence_refs'] if nodes[key]['kind'] == 'document'}
        if target_refs and o.get('claims') == 'required' and not any(
                nodes[key]['quote'] in ref['quote'] and any(address(origin, ref)
                    for origin in source_map[nodes[key]['source_id']]['origins'])
                for key in cited for ref in target_refs):
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
    expected_calc = calculation_expectation(scenario, binding, catalog, business_day)
    calculations = [n for n in evidence if n['kind'] == 'calculation']
    if answer and expected_calc:
        if len(calculations) != 1 or any(calculations[0].get(k) != expected_calc[k]
                                       for k in ('operation','value','unit','as_of')):
            failures.append('calculation_value')
        elif not any(nodes[k]['kind'] == 'employee' and nodes[k]['field'] == expected_calc['field']
                     for k in calculations[0]['input_refs']):
            failures.append('calculation_input')
        elif expected_calc['policy_rule'] and not calculations[0]['rule_refs']:
            failures.append('calculation_rule')
    elif calculations:
        failures.append('unexpected_calculation')
    return sorted(set(failures))
