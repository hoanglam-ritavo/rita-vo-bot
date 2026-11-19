"""Real Type A evaluation with SELECT-only runtime paths; no conversation writes."""
import math
import time
import hashlib

from src.evaluation.questions import check_turn, group_counts, load_dataset, turn_metadata


def p95(values):
    return sorted(values)[max(0, math.ceil(len(values)*.95)-1)] if values else None


async def run_readonly(root, *, retrieval_limit=40, questions_dir=None):
    from src.api.config import load_settings
    from src.api.answer import format_answer
    from src.answering.catalog import load_catalog
    from src.answering.pipeline import evaluate_question, select_cards, requested_aspects, matches_reference
    from src.conversation.context import plan_turn, compact
    from src.database import close_database
    from src.retrieval import RetrievalService
    from src.type_b.tasks import route_question

    cases, oracles, inputs = load_dataset(root, questions_dir=questions_dir)
    settings = load_settings()
    service = RetrievalService(model_path=settings.model_path, model_revision=settings.model_revision,
        device='cpu', rerank=settings.rerank, semantic=settings.semantic)
    lexical_path = settings.semantic.artifact_path or service.policy.lexicon_path
    catalog, lexicon = load_catalog(settings.answer.catalog_path, lexical_path)
    observed = {path: path.read_bytes() for path in (settings.answer.catalog_path, lexical_path,
                                                   service.policy.path)}
    rows, benchmarks, sort_ms = [], [], []
    sampled = set()
    try:
        await service.start()
        if service.policy.catalog is None:
            raise ValueError('evaluation_policy_unavailable')
        # Round-robin across declared business families, not the first software
        # cases in file order. At most one retrieval sample per selected task.
        families = {route.task_id: route.family for route in service.policy.catalog.routes}
        buckets = {}
        for oracle in oracles.values():
            if oracle.claims == 'required' and oracle.fixture == 'current':
                for task in oracle.task_ids_any:
                    buckets.setdefault(families.get(task, 'other'), set()).add(task)
        buckets = {key: sorted(values) for key,values in sorted(buckets.items())}
        sample_tasks = set()
        while any(buckets.values()) and len(sample_tasks) < retrieval_limit:
            for values in buckets.values():
                if values and len(sample_tasks) < retrieval_limit:
                    sample_tasks.add(values.pop(0))
        for case in cases:
            state = {}
            for n, turn in enumerate(case['turns']):
                oracle = oracles[(case['id'], n)]
                key = dict(case_id=case['id'], turn=n)
                # Fixture-only source failures and personnel are not exercised
                # against the application DB or represented as passing checks.
                document_task = service.policy.resolve_generic(turn['text']).state == 'task'
                route = route_question(turn['text'], document_task=document_task)
                if (oracle.fixture != 'current' or oracle.employee_fields or route.source != 'type_a'
                        or 'mixed' in case['tags'] or 'type-b' in case['tags']):
                    rows.append({**key, 'state': 'deferred', 'reason': 'requires_isolated_fixture'})
                    continue
                analysis, state = plan_turn(turn['text'], state, catalog, lexicon, policy=service.policy)
                question = analysis.question
                cards, ambiguous = analysis.cards, analysis.ambiguous
                refs = [ref for card in cards for aspect in analysis.aspects
                        for unit in (card.sections[aspect].units if aspect in card.sections else []) for ref in unit.refs]
                if oracle.claims == 'required' and refs:
                    evidence = await service.read_evidence(refs)
                    if not any(matches_reference(ref, row) for ref in refs for row in evidence['matches']):
                        rows.append({**key, 'state': 'deferred', 'reason': 'eligible_publication_missing_or_changed'})
                        continue
                started = time.perf_counter()
                rendered = await evaluate_question(question, service, catalog, lexicon, analysis=analysis)
                response = format_answer(rendered, started).model_dump(mode='json')
                selected = () if ambiguous else cards
                failures = check_turn(oracle, 200, response, [c.id for c in selected], aspects=analysis.aspects)
                state = compact(state, response)
                rows.append({**key, 'state': 'failed' if failures else 'passed', 'failures': failures,
                             **turn_metadata(response, selected, analysis.aspects)})
                sample_key = tuple(c.id for c in cards)
                if (oracle.claims != 'required' or not refs or not sample_tasks.intersection(sample_key)
                        or sample_key in sampled or len(benchmarks) >= retrieval_limit):
                    continue
                sampled.add(sample_key)
                captures = []
                original = service.policy.reorder
                def capture(text, matches):
                    before = [dict(row) for row in matches]
                    t = time.perf_counter()
                    after = original(text, matches)
                    captures.append((before, [dict(row) for row in after]))
                    sort_ms.append((time.perf_counter()-t)*1000)
                    return after
                service.policy.reorder = capture
                try:
                    result = await service._retrieve(question, 5, assessment=False)
                finally:
                    service.policy.reorder = original
                if not captures:
                    continue
                before, after = captures[-1]
                expected = {(ref.document_id,ref.revision_id,ref.chunk_index) for ref in refs}
                def recalled(matches):
                    return {(m['document_id'],m['revision_id'],m['chunk_index']) for m in matches} & expected
                first = lambda matches: bool(matches and recalled(matches[:1]))
                benchmarks.append({**key, 'expected_addresses': len(expected), 'recalled_at_30': len(recalled(after)),
                    'recalled_at_5': len(recalled(after[:5])), 'before_expected_first': first(before),
                    'after_expected_first': first(after), 'candidate_count': len(after),
                    'corpus_fingerprint': result.get('corpus_fingerprint')})
        counts = {state: sum(r['state'] == state for r in rows) for state in ('passed','failed','deferred')}
        if any(path.read_bytes() != raw for path, raw in observed.items()) or any(
                hashlib.sha256((root / path).read_bytes()).hexdigest() != expected for path, expected in inputs.items()):
            raise ValueError('evaluation_inputs_changed')
        return dict(schema_version=1, mode='readonly-type-a', database_writes=False, independent_acceptance=False,
            cases=len(cases), turns=len(rows), **counts, inputs=inputs, results=rows, group_counts=group_counts(rows),
            retrieval=dict(samples=len(benchmarks), rows=benchmarks, ordering_p95_ms=p95(sort_ms),
                before_expected_first=sum(r['before_expected_first'] for r in benchmarks),
                after_expected_first=sum(r['after_expected_first'] for r in benchmarks),
                recall_at_30=(sum(r['recalled_at_30'] for r in benchmarks)/sum(r['expected_addresses'] for r in benchmarks)
                              if benchmarks else None),
                baseline='Same augmented pool before metadata ordering; not an old-code or vector-only baseline'),
            policy_sha256=service.policy.fingerprint)
    finally:
        try:
            await service.close()
        finally:
            await close_database()
