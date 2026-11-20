"""Structured question oracles; fixture checks do not certify DB publication."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
import re
from pathlib import Path
from types import SimpleNamespace
from typing import Literal
from uuid import uuid4

from pydantic import Field
from src.answering.contracts import Strict
from src.artifact_authoring.sources import checked, decode
from src.paths import REPOSITORY_ROOT


class ExpectedSlots(Strict):
    system: str | None = None
    surface: Literal['web', 'mobile'] | None = None
    workit_action: str | None = None
    subtype: str | None = None
    days: int | float | None = None
    sessions: int | float | None = None
    granularity: Literal['day', 'session'] | None = None
    intent: Literal['policy', 'submission', 'procedure'] | None = None


class ExpectedContext(Strict):
    has_request: bool | None = None
    slots: ExpectedSlots | None = None
    user_inputs: dict[str, str | None] = Field(default_factory=dict)
    missing_slots: list[str] | None = None
    pending_inputs: list[str] | None = None
    pending: Literal['slots', 'task', 'request', 'unsupported', 'inputs'] | None = None


class ExpectedCitation(Strict):
    document_id: str
    revision_id: str
    quote_sha256: str = Field(pattern=r'^[a-f0-9]{64}$')


class Oracle(Strict):
    case_id: str
    turn: int = Field(ge=0)
    statuses: list[str] = Field(min_length=1)
    http_status: int = 200
    task_ids_any: list[str] = Field(default_factory=list)
    forbidden_task_ids: list[str] = Field(default_factory=list)
    expected_aspects: list[Literal['overview', 'amount', 'conditions', 'paperwork', 'procedure']] | None = Field(
        default=None, min_length=1, max_length=5)
    expected_context: ExpectedContext | None = None
    required_source_groups: list[list[str]] = Field(default_factory=list)
    required_citations: list[ExpectedCitation] = Field(default_factory=list, max_length=10)
    fallback_reasons: list[str] | None = Field(default=None, min_length=1)
    unavailable_documents: list[str] = Field(default_factory=list, max_length=10)
    forbidden_documents: list[str] = Field(default_factory=list)
    employee_fields: list[str] = Field(default_factory=list)
    expected_employee_claim_fields: list[str] = Field(default_factory=list)
    null_fields: list[str] = Field(default_factory=list)
    expected_values: dict[str, str | None] = Field(default_factory=dict)
    application_state: str | None = None
    required_numbers: list[int] = Field(default_factory=list)
    claims: str = Field(default='any', pattern='^(any|none|required)$')
    expected_policy_claim_texts: list[str] | None = Field(default=None, min_length=1)
    required_limitations: list[str] | None = Field(default=None, min_length=1)
    retrieve_statuses: list[Literal['evidence_found', 'partial_evidence', 'no_evidence', 'needs_clarification', 'unsupported']] | None = Field(default=None, min_length=1)
    retrieve_documents: Literal['required', 'none'] | None = None
    retrieve_forbidden_documents: list[str] | None = None
    answer_kind: Literal['excerpt'] | None = None
    calculation_required: bool | None = None
    fixture: str = Field(default='current', pattern='^(current|missing|withdrawn|changed|denied|partial|unrelated_changed|catalog_unavailable|catalog_changed|employee_unavailable|feature_disabled|missing_policy)$')


def load_dataset(root=REPOSITORY_ROOT, *, questions_dir=None):
    root = Path(root)
    root = root.absolute()
    directory = root / (questions_dir if questions_dir is not None else 'artifacts/questions')
    if questions_dir is not None and ('..' in directory.parts or directory == root / '.tmp'
                                      or not directory.is_relative_to(root / '.tmp')):
        raise ValueError('private_question_directory_required')
    directory = checked(directory)
    index_path = checked(directory / 'index.json')
    index_bytes = index_path.read_bytes()
    index = decode(index_bytes)
    cases, oracles, inputs = [], {}, {}
    inputs[index_path.relative_to(root).as_posix()] = hashlib.sha256(index_bytes).hexdigest()
    for entry in index['files']:
        path = directory / entry['path']
        if '..' in path.parts or not path.is_relative_to(directory):
            raise ValueError('invalid_question_path')
        raw = checked(path).read_bytes()
        inputs[path.relative_to(root).as_posix()] = hashlib.sha256(raw).hexdigest()
        group = decode(raw)
        for case in group['cases']:
            if any(c['id'] == case['id'] for c in cases):
                raise ValueError('duplicate_question_case')
            cases.append(case)
    for relative in index.get('oracles', []):
        if (not relative.startswith('oracles/') or len(Path(relative).parts) != 2
                or Path(relative).suffix != '.json'):
            raise ValueError('invalid_oracle_path')
        path = checked(directory / relative)
        raw = path.read_bytes()
        inputs[path.relative_to(root).as_posix()] = hashlib.sha256(raw).hexdigest()
        for item in decode(raw)['turns']:
            oracle = Oracle.model_validate(item)
            key = (oracle.case_id, oracle.turn)
            if key in oracles:
                raise ValueError('duplicate_question_oracle')
            oracles[key] = oracle
    expected = {(case['id'], n) for case in cases for n in range(len(case['turns']))}
    if set(oracles) != expected:
        raise ValueError('incomplete_question_oracles')
    return cases, oracles, inputs


def normalize_whitespace(text):
    return ' '.join(text.split())


def policy_content_failures(expected_texts, required_limits, claims, limitations):
    failures = []
    if expected_texts is not None and Counter(map(normalize_whitespace, expected_texts)) != Counter(
            normalize_whitespace(claim['text']) for claim in claims):
        failures.append('policy_claim_texts')
    if required_limits is not None and not set(map(normalize_whitespace, required_limits)).issubset(
            map(normalize_whitespace, limitations)):
        failures.append('required_limitations')
    return failures


def context_failures(expected, state):
    """Compare compacted fixture state without adding its values to reports."""
    if expected is None:
        return []
    if 'request' not in state:
        return ['context_unavailable']
    failures = []
    request = state['request']
    if expected.has_request is not None and (request is not None) != expected.has_request:
        failures.append('context_request')
    request = request or {}
    slots = request.get('slots', {})
    if expected.slots is not None and any(
            slots.get(key) != value or isinstance(slots.get(key), bool)
            for key, value in expected.slots.model_dump(exclude_unset=True).items()):
        failures.append('context_slots')
    raw_inputs = request.get('user_inputs', {})
    inputs = state.get('user_inputs', {})
    if any(
            (key in raw_inputs or key in inputs) if value is None else
            (raw_inputs.get(key) != value or inputs.get(key) != {'value': value, 'origin': 'user_input'})
            for key, value in expected.user_inputs.items()):
        failures.append('context_user_inputs')
    for key in ('missing_slots', 'pending_inputs'):
        expected_slots = getattr(expected, key)
        if expected_slots is not None and Counter(state.get(key, [])) != Counter(expected_slots):
            failures.append(f'context_{key}')
    if 'pending' in expected.model_fields_set and state.get('pending') != expected.pending:
        failures.append('context_pending')
    return failures


def check_turn(oracle, status_code, result, selected=(), *, aspects=()):
    """Assert authored per-turn contracts, never infer an oracle from output."""
    failures = []
    if status_code != oracle.http_status:
        failures.append('http_status')
    if status_code != 200:
        if result.get('error', {}).get('code') not in oracle.statuses:
            failures.append('error_code')
        return failures
    if result.get('status') not in oracle.statuses:
        failures.append('status')
    if oracle.fallback_reasons is not None and result.get('fallback_reason') not in oracle.fallback_reasons:
        failures.append('fallback_reason')
    if oracle.application_state is not None and result.get('application', {}).get('state') != oracle.application_state:
        failures.append('application_state')
    if oracle.task_ids_any and not set(selected).intersection(oracle.task_ids_any):
        failures.append('task')
    if set(selected).intersection(oracle.forbidden_task_ids):
        failures.append('forbidden_task')
    if oracle.expected_aspects is not None and set(aspects) != set(oracle.expected_aspects):
        failures.append('aspect')
    docs = {origin['document_id'] for source in result.get('sources', []) for origin in source.get('origins', [])}
    if any(not docs.intersection(group) for group in oracle.required_source_groups):
        failures.append('required_source')
    if docs.intersection(oracle.forbidden_documents):
        failures.append('forbidden_source')
    facts = [node for node in result.get('evidence', []) if node['kind']=='employee']
    if {f['field'] for f in facts} != set(oracle.employee_fields):
        failures.append('employee_fields')
    if any(not any(f['field'] == field and f['value'] is None for f in facts) for field in oracle.null_fields):
        failures.append('null_field')
    actual_values = {f['field']: f['value'] for f in facts}
    if any(field not in actual_values or actual_values[field] != value for field,value in oracle.expected_values.items()):
        failures.append('employee_values')
    nodes = {node['id']:node for node in result.get('evidence', [])}
    claims = [claim for claim in result.get('claims', []) if any(nodes[ref]['kind']=='document' for ref in claim['evidence_refs'])]
    if oracle.claims == 'none' and claims or oracle.claims == 'required' and not claims:
        failures.append('claims')
    failures.extend(policy_content_failures(oracle.expected_policy_claim_texts, oracle.required_limitations,
                                            claims, result.get('limitations', [])))
    text = ' '.join(claim['text'] for claim in claims)
    observed_numbers = {int(re.sub(r'[.,]', '', token)) for token in
                        re.findall(r'\d{1,3}(?:[.,]\d{3})+(?!\d)|\d+', text)}
    if not set(oracle.required_numbers) <= observed_numbers:
        failures.append('required_numbers')
    # Every policy claim must carry a quote and a returned source. This is a
    # structural invariant in addition to source/hash checks in the renderer.
    sources = {s['source_id'] for s in result.get('sources', [])}
    if any(not nodes[ref].get('quote') or nodes[ref]['source_id'] not in sources
            for claim in claims for ref in claim['evidence_refs'] if nodes[ref]['kind']=='document'):
        failures.append('citation_integrity')
    if oracle.required_citations:
        origins = {source['source_id']: source.get('origins', ()) for source in result.get('sources', ())}
        cited = {(origin['document_id'], origin['revision_id'], hashlib.sha256(node['quote'].encode()).hexdigest())
                 for claim in claims for ref in claim['evidence_refs'] for node in (nodes[ref],)
                 if node['kind'] == 'document' and node.get('quote')
                 for origin in origins.get(node['source_id'], ())}
        if any((item.document_id, item.revision_id, item.quote_sha256) not in cited
               for item in oracle.required_citations):
            failures.append('required_citation')
    return failures


def turn_metadata(result, selected, aspects):
    return {'status': result.get('status', result.get('error', {}).get('code')),
            'task_ids': sorted({card.id for card in selected}), 'aspects': list(aspects),
            'document_ids': sorted({origin['document_id'] for source in result.get('sources', [])
                                    for origin in source.get('origins', [])}),
            'employee_fields': sorted({node['field'] for node in result.get('evidence', [])
                                       if node['kind'] == 'employee'})}


def group_counts(rows):
    groups = {}
    for row in rows:
        group = row['case_id'].rsplit('.', 1)[0]
        counts = groups.setdefault(group, dict(passed=0, failed=0, deferred=0))
        state = row.get('state') or ('passed' if row['passed'] else 'failed')
        counts[state] += 1
    return groups


class FixtureRetrieval:
    """Current-package evidence fixtures, explicitly NOT a model/DB emulator."""
    started = True

    def __init__(self, policy, records):
        self.policy, self.records = policy, records
        self.fixture = 'current'
        self.unavailable_documents = set()

    async def read_evidence(self, references):
        from src.retrieval.reranking import relevance
        found = []
        for ref in references:
            if ref.document_id in self.unavailable_documents:
                continue
            row = self.records.get((ref.document_id, ref.revision_id, ref.chunk_index))
            if row is None:
                continue
            row = deepcopy(row)
            if self.fixture in ('missing', 'withdrawn', 'denied', 'missing_policy'):
                continue
            if self.fixture == 'changed' or self.fixture == 'unrelated_changed' and row['chunk_index'] == 6:
                row['content'] = 'Changed fixture; old source-bound claims must be refused.'
            if self.fixture == 'partial' and row['chunk_index'] != 6:
                continue
            if row not in found:
                found.append(row)
        return dict(status='matches_found' if found else 'no_matches', matches=found,
                    relevance=relevance(found), fallback_reason=None if found else 'no_candidates', rerank_seconds=0.)

    async def retrieve(self, question, top_k=5):
        # Only explicit lookup reaches this fixture. No fake cosine ranking is
        # counted toward recall/ordering acceptance.
        from src.retrieval.policy import folded
        key = folded(question)
        refs = self.policy.references(question)
        rows = [deepcopy(self.records[(r.document_id,r.revision_id,r.chunk_index)]) for r in refs
                if (r.document_id,r.revision_id,r.chunk_index) in self.records][:top_k]
        rows = rows or [deepcopy(row) for row in self.records.values()
                if any(term in key and term in folded(row['title']) for term in ('e leave', 'workit', 'bravo', 'outlook'))][:top_k]
        rows = [row for row in rows if row['document_id'] not in self.unavailable_documents]
        from src.retrieval.reranking import relevance
        return dict(status='matches_found' if rows else 'no_matches', matches=rows, relevance=relevance(rows),
                    fallback_reason=None if rows else 'no_candidates', rerank_seconds=0.)

    async def prepare_query(self, question, top_k=5):
        return question

    async def discover(self, question, references, top_k=8):
        # Fixture checks exercise declared source addresses without ranking claims.
        raw = await self.read_evidence(references)
        return {**raw, 'matches': raw['matches'][:top_k]}

    async def retrieve_prepared(self, prepared, top_k=5):
        return await self.retrieve(prepared, top_k)


def fixture_records(root):
    from src.semantics.corpus import corpus_snapshot, decode_json
    snapshot = corpus_snapshot(Path(root) / 'chunks')
    metadata = {p['document_id']: decode_json((Path(root) / 'chunks' / p['document_id'] / 'metadata.json').read_bytes())
                for p in snapshot['packages']}
    duplicates = [(meta['revision_id'], relation['revision_id']) for meta in metadata.values()
                  for relation in meta.get('relations', ()) if relation['type'] == 'duplicate_of']
    from src.answering.catalog import METADATA
    result = {}
    for chunk in snapshot['chunks']:
        meta = metadata[chunk['document_id']]
        row = {**{key: meta.get(key) for key in METADATA}, 'content': chunk['content'],
            'chunk_id': str(uuid4()), 'document_id': chunk['document_id'], 'revision_id': meta['revision_id'],
            'chunk_index': chunk['chunk_index'], 'run_id': str(uuid4()), 'source_sha256': meta['sha256'],
            'source_spans': [], 'author_id': None, 'role': None, 'cosine_distance': .1, 'rerank_score': None}
        row['duplicate_revision_pairs'] = [pair for pair in duplicates if row['revision_id'] in pair]
        result[(row['document_id'], row['revision_id'], row['chunk_index'])] = row
    return result


async def run_fixture(root=REPOSITORY_ROOT, *, questions_dir=None):
    import httpx
    from src.api.app import create_app
    from src.api.config import Settings
    from src.api.service import ApiService
    from src.answering.catalog import load_catalog
    from src.answering.config import AnswerSettings
    from src.answering.pipeline import select_cards
    from src.conversation.context import plan_turn, compact, finalize_scope
    from functools import partial
    from src.type_b.employee_store.fields import PROFILE_FIELDS
    from src.type_b.employee_store.normalization import normalize_profile
    from src.retrieval.policy import RetrievalPolicy
    from src.type_b.config import TypeBSettings
    from src.type_b.tasks import BY_FIELD

    root = Path(root)
    cases, oracles, inputs = load_dataset(root, questions_dir=questions_dir)
    for relative in ('artifacts/answers/catalog.json', 'artifacts/semantics/lexicon.json', 'artifacts/retrieval/catalog.json'):
        inputs[relative] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    for relative in ('src/retrieval/policy.py', 'src/retrieval/language.py', 'src/answering/pipeline.py',
                     'src/conversation/context.py', 'src/conversation/continuation.py', 'src/answering/scope.py',
                     'src/type_b/tasks.py', 'src/type_b/service.py',
                     'src/answering/contextual.py', 'src/answering/planning.py', 'src/answering/context_spec.py',
                     'src/answering/discovery.py',
                     'src/answering/context_contracts.py', 'src/answering/calculations.py',
                     'src/answering/slots.py', 'src/api/contracts.py',
                     'src/type_b/employee_store/service.py', 'src/type_b/employee_store/normalization.py',
                     'src/type_b/employee_store/fields.py', 'src/type_b/employee_store/selector.py',
                     'src/type_b/employee_store/repository.py', 'src/type_b/employee_store/connection.py',
                     'src/evaluation/questions.py'):
        inputs[relative] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    catalog, lexicon = load_catalog(root / 'artifacts/answers/catalog.json', root / 'artifacts/semantics/lexicon.json')
    policy = RetrievalPolicy(root / 'artifacts/retrieval/catalog.json', root / 'artifacts/semantics/lexicon.json')
    policy.start()
    if policy.catalog is None:
        raise ValueError('evaluation_policy_unavailable')
    records = fixture_records(root)
    from src.conversation.context import identity
    base_stamp = identity(catalog, lexicon, policy)
    rows = []
    for case in cases:
        # One owner/client/state per case; there are no durable writes or DB IO.
        retrieval = FixtureRetrieval(policy, records)
        fields = dict.fromkeys(PROFILE_FIELDS)
        fields.update(id=1, employee_id=42, user_id=7, updated_at='2026-01-01T00:00:00+07:00')
        for field, task in BY_FIELD.items():
            fields[field] = '2020-01-01' if task.date else 'Fictional ' + field
        async def employee_read(user_id):
            if user_id != 7:
                raise ValueError('unexpected_fixture_identity')
            return normalize_profile(fields)
        async def read_context(user_id, selected, *, manager=False):
            from types import MappingProxyType
            from src.type_b.employee_store.contracts import EmployeeContext, ManagerResult, ManagerSummary
            profile = await employee_read(user_id)
            relation = ManagerResult('found', ManagerSummary(90,None,'M90',fields['manager_name'],None,None,None,'manager@example.test',None,True,None)) if manager else None
            return EmployeeContext(MappingProxyType({field:getattr(profile,field) for field in selected}),relation,'2026-11-18T00:00:00Z',profile.updated_at)
        owner = ApiService(Settings(Path('unused'), 'a'*40, service_key='fictional-evaluation-key',
            answer=AnswerSettings(enabled=True), type_b=TypeBSettings(True)), retrieval=retrieval,
            employee_service=SimpleNamespace(read_context=read_context))
        owner.started = owner.answers.available = owner.context.available = True
        owner.answers.catalog, owner.answers.lexicon = catalog, lexicon
        execute = owner.context.execute
        state = {}
        app = create_app(owner.settings, service_factory=lambda _: owner)
        app.state.retrieval_service = owner
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url='http://fixture', headers={'X-Rita-Service-Key': 'fictional-evaluation-key'}) as client:
            # No lifespan: no startup/model/database hooks run.
            for n, turn in enumerate(case['turns']):
                oracle = oracles[(case['id'], n)]
                retrieval.fixture = oracle.fixture
                retrieval.unavailable_documents = set(oracle.unavailable_documents)
                owner.answers.available = oracle.fixture != 'catalog_unavailable'
                owner.context.available = oracle.fixture != 'employee_unavailable'
                for field in oracle.null_fields:
                    fields[field] = None
                turn_catalog, stamp = catalog, base_stamp
                if oracle.fixture == 'catalog_changed':
                    # Change the active binding, not just the overall stamp. A
                    # compatible task can survive an unrelated catalog change.
                    active_task = state.get('task_id') or state.get('input_task_id')
                    turn_catalog = catalog.model_copy(update={
                        'cards': [card for card in catalog.cards if card.id != active_task]})
                    stamp = identity(turn_catalog, lexicon, policy)
                owner.answers.catalog = turn_catalog
                analysis, state = plan_turn(turn['text'], state, turn_catalog, lexicon, policy=policy, identity_stamp=stamp)
                question = analysis.question
                def resolved_plan(resolved):
                    nonlocal analysis, state
                    if analysis is not resolved:
                        analysis, state = resolved, finalize_scope(state, resolved)

                owner.context.execute = partial(execute, analysis=analysis, on_resolved=resolved_plan)
                body = {'text': question, 'hrm_user_id': 7, 'top_k': 5}
                if oracle.fixture == 'feature_disabled':
                    # Model the documented disabled HTTP guard, not fake facts.
                    from dataclasses import replace
                    owner.context.settings = replace(owner.context.settings, enabled=False)
                    owner.context.available = False
                response = await client.post('/api/v1/answer', json=body)
                value = response.json()
                selected, ambiguous = analysis.cards, analysis.ambiguous
                if analysis.document_lookup:
                    # Lookup task labels annotate fixture recall, not template authorization.
                    selected, ambiguous = select_cards(question, turn_catalog, lexicon, policy, contextual=True)
                selected = [] if ambiguous else selected
                failures = check_turn(oracle, response.status_code, value,
                                      [c.id for c in selected], aspects=analysis.aspects)
                if oracle.calculation_required is not None and response.status_code == 200:
                    from src.evaluation.http_e2e.assertions import calculation_failures
                    from src.evaluation.http_e2e.inventory import Scenario
                    scenario = Scenario(case['id'], '', '/api/v1/answer', turn['text'], oracle.model_dump())
                    failures.extend(calculation_failures(scenario, {'expected_values': oracle.expected_values},
                                                        value, catalog.model_dump(), None))
                if response.status_code == 200:
                    state = compact(state, value, analysis=analysis)
                failures.extend(context_failures(oracle.expected_context, state if response.status_code == 200 else {}))
                rows.append(dict(case_id=case['id'], turn=n, passed=not failures, failures=failures,
                                 **turn_metadata(value, () if analysis.document_lookup else selected, analysis.aspects)))
    failed = sum(not row['passed'] for row in rows)
    if any(hashlib.sha256((root / path).read_bytes()).hexdigest() != expected for path, expected in inputs.items()):
        raise ValueError('evaluation_inputs_changed')
    return {'schema_version': 1, 'mode': 'source-fixture-http-and-reducer', 'independent_acceptance': False,
            'database_verified': False, 'retrieval_metrics': 'not_measured_no_vector_search',
            'cases': len(cases), 'turns': len(rows), 'passed': len(rows)-failed, 'failed': failed,
            'groups': dict(Counter('.'.join(row['case_id'].split('.')[1:-1]) for row in rows if not row['passed'])),
            'group_counts': group_counts(rows),
            'inputs': inputs, 'results': rows}
