"""Endpoint-specific authored rules; enumerate every case without executing it."""
from dataclasses import dataclass, field
from collections import Counter
from pathlib import Path
from typing import Literal

from .config import EvaluationError, digest

ENDPOINTS = ('/api/v1/retrieve', '/api/v1/answer')
Profile = Literal['full', 'smoke']
SMOKE_QUESTIONS = frozenset({
    'q.boundaries.ambiguity.001', 'q.boundaries.evidence-availability.012',
    'q.boundaries.privacy-and-injection.001', 'q.employee.self-work-profile.009',
    'q.employee.self-work-profile.032', 'q.employee.self-work-profile.033',
    'q.mixed.policy-and-profile.001', 'q.policy.benefits.024', 'q.policy.leave.015',
    'q.software.workit.093', 'q.software.eleave.021',
})
MANAGER_CONFLICT_USER_ID = 910006
# These are deliberately retrieval contracts, not renamed answer statuses.
RETRIEVAL_RULES = {
    'documents': ['evidence_found', 'partial_evidence'],
    'profile': ['evidence_found', 'partial_evidence'],
    'null_profile': ['no_evidence'],
    'clarification': ['needs_clarification'],
    'unresolved': ['needs_clarification', 'unsupported', 'no_evidence', 'evidence_found'],
}
PROTOCOL = {
    'missing_key': (401, 'service_authentication_required'),
    'wrong_key': (401, 'service_authentication_required'),
    'missing_identity': (422, 'invalid_request'), 'null_identity': (422, 'invalid_request'),
    'string_identity': (422, 'invalid_request'), 'boolean_identity': (422, 'invalid_request'),
    'zero_identity': (422, 'invalid_request'), 'blank_text': (422, 'invalid_request'),
    'top_k_zero': (422, 'invalid_request'), 'top_k_large': (422, 'invalid_request'),
    'top_k_boolean': (422, 'invalid_request'),
}


@dataclass
class Scenario:
    case_id: str
    group: str
    endpoint: str
    text: str = field(repr=False)
    oracle: dict = field(repr=False)
    tags: tuple = ()
    reason: str | None = None
    protocol: str | None = None
    references: dict = field(default_factory=dict, repr=False)

    @property
    def key(self):
        return self.case_id + ':' + self.endpoint.rsplit('/', 1)[-1]


def build_inventory(cases, oracles):
    scenarios = []
    for case in cases:
        original = oracles[case['id'], 0]
        oracle = original.model_dump() if hasattr(original, 'model_dump') else dict(original)
        reason = ('conversation_required' if len(case['turns']) != 1 else
                  'controlled_fixture_required' if oracle['fixture'] != 'current' else None)
        for endpoint in ENDPOINTS:
            scenarios.append(Scenario(case['id'], '.'.join(case['id'].split('.')[1:-1]),
                endpoint, case['turns'][0]['text'], dict(oracle), tuple(case['tags']), reason,
                references=case.get('references', {})))
    for name, (status, code) in PROTOCOL.items():
        for endpoint in ENDPOINTS:
            scenarios.append(Scenario('protocol.' + name, 'protocol', endpoint,
                'Chức danh của tôi là gì?', {'http_status': status, 'error_code': code},
                protocol=name))
    for role, fields, question in [
        ('complete_work', ['employee_name','employee_code','job_title','department_name'],
         'Họ tên, mã nhân viên, chức danh và phòng ban của tôi là gì?'),
        ('inactive', ['job_title'], 'Chức danh của tôi là gì?'),
        ('duplicate_user', ['employee_code'], 'Mã nhân viên của tôi là gì?'),
        ('manager', ['manager_name'], 'Quản lý trực tiếp của tôi là ai?'),
        ('not_found', [], 'Chức danh của tôi là gì?'),
    ]:
        for endpoint in ENDPOINTS:
            scenarios.append(Scenario('hrm.role.' + role, 'hrm.roles', endpoint, question,
                {'http_status': 200, 'statuses': ['insufficient_evidence'] if role == 'not_found' else ['answered'],
                 'employee_fields': fields, 'expected_employee_claim_fields': fields,
                 'fixture': 'current', 'claims': 'none',
                 'application_state': 'not_found' if role == 'not_found' else 'applied'},
                tags=('role:' + role,)))
    for endpoint in ENDPOINTS:
        scenarios.append(Scenario('hrm.role.manager_conflicting', 'hrm.roles', endpoint,
            'Quản lý trực tiếp của tôi là ai?',
            {'statuses': ['insufficient_evidence'], 'retrieve_statuses': ['no_evidence'],
             'retrieve_documents': 'none', 'employee_fields': ['manager_name'],
             'expected_employee_claim_fields': ['manager_name'], 'null_fields': ['manager_name'],
             'application_state': 'partial', 'claims': 'none', 'fixture': 'current'},
            tags=('role:manager_conflicting',)))
    return scenarios


def select_profile(scenarios, profile: Profile):
    if profile == 'full':
        return scenarios
    selected = [s for s in scenarios if not s.reason and (
        s.case_id in SMOKE_QUESTIONS or s.protocol or s.group == 'hrm.roles')]
    bank_keys = {(s.case_id, s.endpoint) for s in selected if s.case_id in SMOKE_QUESTIONS}
    if profile != 'smoke' or bank_keys != {(case, endpoint) for case in SMOKE_QUESTIONS for endpoint in ENDPOINTS}:
        raise EvaluationError('invalid_smoke_inventory')
    return selected


def selection_fingerprint(scenarios):
    return digest([(s.key, s.reason) for s in scenarios])


def load_inventory(root):
    from src.evaluation.questions import load_dataset
    from src.artifact_authoring.sources import checked, decode
    import hashlib
    root = Path(root).absolute()
    cases, oracles, inputs = load_dataset(root)
    # Catalog data are read locally; never initialize a policy/model/service.
    path = checked(Path(root) / 'artifacts/answers/catalog.json')
    raw = path.read_bytes()
    catalog = decode(raw)
    inputs['artifacts/answers/catalog.json'] = hashlib.sha256(raw).hexdigest()
    for relative in ('artifacts/semantics/lexicon.json', 'artifacts/retrieval/catalog.json',
                     'artifacts/runtime-assets.json', 'src/evaluation/questions.py'):
        inputs[relative] = hashlib.sha256(checked(Path(root) / relative).read_bytes()).hexdigest()
    for path in sorted((Path(__file__).parent).glob('*.py')):
        inputs['src/evaluation/http_e2e/' + path.name] = hashlib.sha256(path.read_bytes()).hexdigest()
    cli = Path(__file__).resolve().parents[2] / 'cli/evaluate_http_e2e.py'
    inputs['src/cli/evaluate_http_e2e.py'] = hashlib.sha256(cli.read_bytes()).hexdigest()
    return build_inventory(cases, oracles), inputs, catalog


def public_inventory(scenarios, inputs, *, profile: Profile = 'full'):
    rows = [{'case_id': s.case_id, 'endpoint': s.endpoint, 'group': s.group,
             'state': 'not_applicable' if s.reason else 'pending',
             'reason': s.reason, 'oracle': 'protocol' if s.protocol else
             'answer' if s.endpoint.endswith('/answer') else retrieval_rule(s)} for s in scenarios]
    return {'schema_version': 1, 'mode': 'external-http-inventory',
            'independent_acceptance': False, 'inputs': inputs, 'profile': profile,
            'selection_fingerprint': selection_fingerprint(scenarios),
            'inventory_fingerprint': digest(rows), 'results': rows,
            'counts': dict(Counter(r['state'] for r in rows)),
            'retrieve_unresolved_default': sum(r['state'] == 'pending' and r['oracle'] == 'unresolved' for r in rows)}


def retrieval_rule(scenario):
    o = scenario.oracle
    if o.get('retrieve_statuses'):
        return 'explicit'
    if o.get('application_state') == 'not_found':
        return 'null_profile'
    if o.get('required_source_groups'):
        return 'documents'
    if o.get('null_fields') and set(o.get('null_fields', [])) == set(o.get('employee_fields', [])):
        return 'null_profile'
    if o.get('employee_fields'):
        return 'profile'
    if o.get('statuses') == ['needs_clarification']:
        return 'clarification'
    return 'unresolved'


def task_sections(scenario, card):
    """Use the same authored aspect selection for preflight and response checks."""
    aspects = scenario.oracle.get('expected_aspects') or [
        tag.removeprefix('aspect:') for tag in scenario.tags if tag.startswith('aspect:')]
    return {key: section for key, section in card['sections'].items()
            if key in (aspects or ['overview'])}
