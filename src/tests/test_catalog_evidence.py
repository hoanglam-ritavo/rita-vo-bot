"""Dependency-local answering and refresh regressions; fictional source bytes."""
import asyncio
from copy import deepcopy
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from src.answering.catalog import Catalog, load_catalog
from src.answering.pipeline import evaluate_question, render, select_cards
from src.artifact_authoring import impact, sources, workflow
from src.tests.test_answering import sample
from src.tests.test_artifact_authoring import authored


def test_retired_environment_values_are_ignored_and_never_logged(tmp_path, monkeypatch, caplog):
    from src.api import config
    monkeypatch.setattr(config, 'REPOSITORY_ROOT', tmp_path)
    monkeypatch.setenv('CHAT_BOT_SERVICE_KEY', 'fictional-service-key')
    (tmp_path/'.env').write_text(
        'SEMANTIC_MODE=off\nANSWER_ENABLED=false\nTYPE_B_ENABLED=false\nRERANK_ENABLED=false\n'
        'E5_MODEL_REVISION='+'a'*40+'\n'+''.join(
            key+'=PRIVATE_RETIRED_VALUE\n' for key in config.RETIRED_KEYS), encoding='utf-8')
    # Register inherited-state restoration before the explicit dotenv loader.
    for key in (*config.RETIRED_KEYS, 'SEMANTIC_MODE','ANSWER_ENABLED','TYPE_B_ENABLED',
                'RERANK_ENABLED','E5_MODEL_REVISION'):
        monkeypatch.setenv(key, '')
        monkeypatch.delenv(key)
    settings = config.load_settings()
    assert not hasattr(settings, 'intent') and not hasattr(settings, 'relevance_filter_enabled')
    assert not hasattr(settings.rerank, 'profile_path')
    assert all('retired_configuration_ignored key='+key in caplog.text for key in config.RETIRED_KEYS)
    assert 'PRIVATE_RETIRED_VALUE' not in caplog.text


def test_known_task_reads_bound_evidence_without_vector_search_or_top_k_truncation(sample):
    async def scenario():
        retrieval = SimpleNamespace(read_evidence=AsyncMock(return_value=sample.result), retrieve=AsyncMock())
        row = await evaluate_question('Sinh con được bao nhiêu?', retrieval, sample.catalog, sample.lexicon, top_k=1)
        assert row['status'] == 'answered'
        assert row['claims'][0]['citations'][0]['quote'] == sample.result['matches'][0]['content']
        retrieval.retrieve.assert_not_awaited()
        assert retrieval.read_evidence.call_args.args[0][0].content_sha256
    asyncio.run(scenario())


@pytest.mark.parametrize('question,status', [
    ('Không sinh con có được hỗ trợ không?', 'needs_clarification'),
    ('Sinh con nhưng chưa có giấy tờ?', 'needs_clarification'),
    ('So sánh các chính sách?', 'needs_clarification'),
    ('Một câu hỏi ngoài phạm vi?', 'insufficient_evidence'),
])
def test_unclear_questions_do_not_fetch_or_expose_nearest_passages(sample, question, status):
    async def scenario():
        retrieval = SimpleNamespace(read_evidence=AsyncMock(), retrieve=AsyncMock())
        row = await evaluate_question(question, retrieval, sample.catalog, sample.lexicon)
        assert row['status'] == status and row['claims'] == [] and row['sources'] == {}
        retrieval.retrieve.assert_not_awaited()
        retrieval.read_evidence.assert_not_awaited()
    asyncio.run(scenario())


def test_explicit_document_lookup_uses_candidate_search_only(sample):
    async def scenario():
        retrieval = SimpleNamespace(read_evidence=AsyncMock(), retrieve=AsyncMock(return_value=sample.result))
        row = await evaluate_question('Tìm tài liệu minh họa?', retrieval, sample.catalog, sample.lexicon, top_k=2)
        retrieval.retrieve.assert_awaited_once_with('Tìm tài liệu minh họa?', top_k=2)
        retrieval.read_evidence.assert_not_awaited()
        assert row['status'] == 'partial' and row['intro']
        assert row['claims'][0]['text'] == sample.result['matches'][0]['content']
        assert row['relevance']['status'] == 'unassessed'
    asyncio.run(scenario())


@pytest.mark.parametrize('field,value', [('source_sha256', 'b'*64), ('revision_id', 'old'),
    ('content', 'Changed fictional policy'), ('applicability', 'Other audience'),
    ('version_label', 'v2'), ('effective_to', '2020-01-01')])
def test_stale_binding_never_falls_back_to_authored_claim_or_nearest_excerpt(sample, field, value):
    sample.result['matches'][0][field] = value
    row = render('Sinh con được bao nhiêu?', sample.result, sample.catalog, sample.lexicon)
    assert row['status'] == 'insufficient_evidence' and not row['claims'] and not row['sources']


def test_missing_unit_only_blocks_dependent_task(sample):
    data = deepcopy(sample.data)
    other = deepcopy(data['cards'][0])
    other.update(id='other-task', aliases=['khác'], concept_ids=[])
    for section in other['sections'].values():
        for unit in section['units']:
            unit['id'] = 'evidence.other'
            unit['refs'][0]['revision_id'] = 'old'
    data['cards'].append(other)
    catalog = Catalog.model_validate(data)
    assert render('Khác bao nhiêu?', sample.result, catalog, sample.lexicon)['claims'] == []
    assert render('Sinh con bao nhiêu?', sample.result, catalog, sample.lexicon)['status'] == 'answered'


def test_impact_is_readonly_and_refresh_relinks_only_identical_unique_evidence(authored):
    root, _, _ = authored
    package = next((root/'chunks').iterdir())
    chunk_path = package/'chunks.json'
    chunks = json.loads(chunk_path.read_bytes())
    chunks['chunks'].reverse()
    chunk_path.write_bytes(sources.encode(chunks))
    before = {p: p.read_bytes() for p in (root/'artifacts').rglob('*.json')}
    report, updates, _, _ = impact.inspect(root)
    assert report['summary'] == {'relinkable': 2} and len(updates) == 2
    assert all(p.read_bytes() == raw for p, raw in before.items())
    impact.refresh(root)
    assert impact.inspect(root)[0]['summary'] == {'unchanged': 2}
    workflow.build(root)
    catalog, _ = load_catalog(root/'artifacts/answers/catalog.json', root/'artifacts/semantics/lexicon.json')
    assert {ref.chunk_index for c in catalog.cards for section in c.sections.values()
            for unit in section.units for ref in unit.refs} == {1}
    assert {c.id for c in catalog.cards} == {'a','z'}


@pytest.mark.parametrize('change,state', [('content','content_changed'), ('revision','context_changed'),
                                       ('duplicate','ambiguous'), ('context','context_changed')])
def test_refresh_reports_review_required_without_silent_rebinding(authored, change, state):
    root, _, _ = authored
    package = next((root/'chunks').iterdir())
    if change in ('content','duplicate'):
        path = package/'chunks.json'
        data = json.loads(path.read_bytes())
        if change == 'content': data['chunks'][0]['content'] += ' Changed condition.'
        else: data['chunks'].append(deepcopy(data['chunks'][0]))
    else:
        path = package/'metadata.json'
        data = json.loads(path.read_bytes())
        data['revision_id' if change == 'revision' else 'applicability'] = 'changed'
    path.write_bytes(sources.encode(data))
    index = json.loads((root/'artifacts/answers/source/index.json').read_bytes())
    card_path = root/'artifacts/answers/source'/index['cards'][0]
    original = json.loads(card_path.read_bytes())
    assert impact.inspect(root)[0]['summary'] == {state:2}
    impact.refresh(root)
    assert json.loads(card_path.read_bytes()) == original


def test_current_catalog_inventory_and_authored_aliases():
    from src.paths import REPOSITORY_ROOT
    catalog, lexicon = load_catalog(REPOSITORY_ROOT/'artifacts/answers/catalog.json',
                                    REPOSITORY_ROOT/'artifacts/semantics/lexicon.json')
    from src.semantics.corpus import corpus_snapshot
    snapshot = corpus_snapshot()
    assert {(row.document_id, row.chunk_index) for row in catalog.coverage} == {
        (c['document_id'], c['chunk_index']) for c in snapshot['chunks']}
    from src.retrieval.policy import RetrievalPolicy
    policy = RetrievalPolicy()
    policy.start()
    assert policy.catalog is not None
    focused_tasks = {route.task_id for route in policy.catalog.routes if route.question_terms and not route.surface}
    workit_tasks = {route.task_id for route in policy.catalog.routes if route.surface}
    routed_only = {card.id for card in catalog.cards if card.routed_only}
    assert focused_tasks <= routed_only <= focused_tasks | workit_tasks
    for card in catalog.cards:
        cards, ambiguous = select_cards(card.aliases[0], catalog, lexicon)
        if card.routed_only:
            assert card.id not in {c.id for c in cards}
        else:
            assert card.id in {c.id for c in cards}
            assert ambiguous or len(cards) == 1
