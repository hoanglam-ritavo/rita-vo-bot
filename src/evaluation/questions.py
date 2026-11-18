"""Structured question oracles; fixture checks do not certify DB publication."""
from collections import Counter
from copy import deepcopy
import hashlib
import json
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

from pydantic import Field
from src.answering.contracts import Strict
from src.artifact_authoring.sources import checked, decode
from src.paths import REPOSITORY_ROOT


class Oracle(Strict):
    case_id: str
    turn: int = Field(ge=0)
    statuses: list[str] = Field(min_length=1)
    http_status: int = 200
    task_ids_any: list[str] = Field(default_factory=list)
    forbidden_task_ids: list[str] = Field(default_factory=list)
    required_source_groups: list[list[str]] = Field(default_factory=list)
    forbidden_documents: list[str] = Field(default_factory=list)
    employee_fields: list[str] = Field(default_factory=list)
    null_fields: list[str] = Field(default_factory=list)
    expected_values: dict[str, str | None] = Field(default_factory=dict)
    claims: str = Field(default='any', pattern='^(any|none|required)$')
    fixture: str = Field(default='current', pattern='^(current|missing|withdrawn|changed|denied|partial|unrelated_changed|catalog_unavailable|catalog_changed|employee_unavailable|feature_disabled|missing_policy)$')


def load_dataset(root=REPOSITORY_ROOT):
    root = Path(root)
    directory = checked(root / 'artifacts/questions')
    index = decode((directory / 'index.json').read_bytes())
    cases, oracles, inputs = [], {}, {}
    for entry in index['files']:
        path = checked(directory / entry['path'])
        if not path.is_relative_to(directory):
            raise ValueError('invalid_question_path')
        raw = path.read_bytes()
        inputs[path.relative_to(root).as_posix()] = hashlib.sha256(raw).hexdigest()
        group = decode(raw)
        for case in group['cases']:
            if any(c['id'] == case['id'] for c in cases):
                raise ValueError('duplicate_question_case')
            cases.append(case)
    for relative in index.get('oracles', []):
        if not relative.startswith('oracles/') or len(Path(relative).parts) != 2:
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


def check_turn(oracle, status_code, result, selected=()):
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
    if oracle.task_ids_any and not set(selected).intersection(oracle.task_ids_any):
        failures.append('task')
    if set(selected).intersection(oracle.forbidden_task_ids):
        failures.append('forbidden_task')
    docs = {origin['document_id'] for source in result.get('sources', []) for origin in source.get('origins', [])}
    if any(not docs.intersection(group) for group in oracle.required_source_groups):
        failures.append('required_source')
    if docs.intersection(oracle.forbidden_documents):
        failures.append('forbidden_source')
    facts = result.get('employee_evidence', {}).get('facts', [])
    if {f['field'] for f in facts} != set(oracle.employee_fields):
        failures.append('employee_fields')
    if any(not any(f['field'] == field and f['value'] is None for f in facts) for field in oracle.null_fields):
        failures.append('null_field')
    actual_values = {f['field']: f['value'] for f in facts}
    if any(field not in actual_values or actual_values[field] != value for field,value in oracle.expected_values.items()):
        failures.append('employee_values')
    claims = result.get('claims', [])
    if oracle.claims == 'none' and claims or oracle.claims == 'required' and not claims:
        failures.append('claims')
    # Every policy claim must carry a quote and a returned source. This is a
    # structural invariant in addition to source/hash checks in the renderer.
    sources = {s['source_id'] for s in result.get('sources', [])}
    if any(not c.get('citations') or any(not ref.get('quote') or ref['source_id'] not in sources
            for ref in c['citations']) for c in claims):
        failures.append('citation_integrity')
    return failures


class FixtureRetrieval:
    """Current-package evidence fixtures, explicitly NOT a model/DB emulator."""
    started = True

    def __init__(self, policy, records):
        self.policy, self.records = policy, records
        self.fixture = 'current'

    async def read_evidence(self, references):
        from src.retrieval.reranking import relevance
        found = []
        for ref in references:
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
        from src.retrieval.reranking import relevance
        return dict(status='matches_found' if rows else 'no_matches', matches=rows, relevance=relevance(rows),
                    fallback_reason=None if rows else 'no_candidates', rerank_seconds=0.)


def fixture_records(root):
    from src.semantics.corpus import corpus_snapshot, decode_json
    snapshot = corpus_snapshot(Path(root) / 'chunks')
    metadata = {p['document_id']: decode_json((Path(root) / 'chunks' / p['document_id'] / 'metadata.json').read_bytes())
                for p in snapshot['packages']}
    from src.answering.catalog import METADATA
    result = {}
    for chunk in snapshot['chunks']:
        meta = metadata[chunk['document_id']]
        row = {**{key: meta.get(key) for key in METADATA}, 'content': chunk['content'],
            'chunk_id': str(uuid4()), 'document_id': chunk['document_id'], 'revision_id': meta['revision_id'],
            'chunk_index': chunk['chunk_index'], 'run_id': str(uuid4()), 'source_sha256': meta['sha256'],
            'source_spans': [], 'author_id': None, 'role': None, 'cosine_distance': .1, 'rerank_score': None}
        result[(row['document_id'], row['revision_id'], row['chunk_index'])] = row
    return result


async def run_fixture(root=REPOSITORY_ROOT):
    import httpx
    from src.api.app import create_app
    from src.api.config import Settings
    from src.api.service import ApiService
    from src.answering.catalog import load_catalog
    from src.answering.config import AnswerSettings
    from src.answering.pipeline import select_cards
    from src.conversation.context import reduce_question, compact
    from src.employee_source.contracts import PROFILE_FIELDS, normalize_profile
    from src.retrieval.policy import RetrievalPolicy
    from src.type_b.config import TypeBSettings
    from src.type_b.tasks import BY_FIELD

    root = Path(root)
    cases, oracles, inputs = load_dataset(root)
    for relative in ('artifacts/answers/catalog.json', 'artifacts/semantics/lexicon.json', 'artifacts/retrieval/catalog.json'):
        inputs[relative] = hashlib.sha256((root / relative).read_bytes()).hexdigest()
    catalog, lexicon = load_catalog(root / 'artifacts/answers/catalog.json', root / 'artifacts/semantics/lexicon.json')
    policy = RetrievalPolicy(root / 'artifacts/retrieval/catalog.json', root / 'artifacts/semantics/lexicon.json')
    policy.start()
    if policy.catalog is None:
        raise ValueError('evaluation_policy_unavailable')
    records = fixture_records(root)
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
        owner = ApiService(Settings(Path('unused'), 'a'*40, service_key='fictional-evaluation-key',
            answer=AnswerSettings(enabled=True), type_b=TypeBSettings(True)), retrieval=retrieval,
            employee_service=SimpleNamespace(find_by_user_id=employee_read))
        owner.started = owner.answers.available = owner.context.available = True
        owner.answers.catalog, owner.answers.lexicon = catalog, lexicon
        state = {}
        app = create_app(owner.settings, service_factory=lambda _: owner)
        app.state.retrieval_service = owner
        async with httpx.AsyncClient(transport=httpx.ASGITransport(app=app),
                base_url='http://fixture', headers={'X-Rita-Service-Key': 'fictional-evaluation-key'}) as client:
            # No lifespan: no startup/model/database hooks run.
            for n, turn in enumerate(case['turns']):
                oracle = oracles[(case['id'], n)]
                retrieval.fixture = oracle.fixture
                owner.answers.available = oracle.fixture != 'catalog_unavailable'
                owner.context.available = oracle.fixture != 'employee_unavailable'
                for field in oracle.null_fields:
                    fields[field] = None
                stamp = 'changed-evaluation-context' if oracle.fixture == 'catalog_changed' else None
                question, state = reduce_question(turn['text'], state, catalog, lexicon, policy=policy, identity_stamp=stamp)
                selected, ambiguous = select_cards(question, catalog, lexicon, policy)
                body = {'text': question, 'hrm_user_id': 7, 'top_k': 5}
                if oracle.fixture == 'feature_disabled':
                    # Model the documented disabled HTTP guard, not fake facts.
                    from dataclasses import replace
                    owner.context.settings = replace(owner.context.settings, enabled=False)
                response = await client.post('/api/v1/answer', json=body)
                value = response.json()
                failures = check_turn(oracle, response.status_code, value,
                                      [] if ambiguous else [c.id for c in selected])
                if response.status_code == 200:
                    state = compact(state, value)
                rows.append(dict(case_id=case['id'], turn=n, passed=not failures, failures=failures,
                                 status=value.get('status', value.get('error', {}).get('code'))))
    failed = sum(not row['passed'] for row in rows)
    if any(hashlib.sha256((root / path).read_bytes()).hexdigest() != expected for path, expected in inputs.items()):
        raise ValueError('evaluation_inputs_changed')
    return {'schema_version': 1, 'mode': 'source-fixture-http-and-reducer', 'independent_acceptance': False,
            'database_verified': False, 'retrieval_metrics': 'not_measured_no_vector_search',
            'cases': len(cases), 'turns': len(rows), 'passed': len(rows)-failed, 'failed': failed,
            'groups': dict(Counter('.'.join(row['case_id'].split('.')[1:-1]) for row in rows if not row['passed'])),
            'inputs': inputs, 'results': rows}
