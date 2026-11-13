"""Read-only package-pinned retrieval evaluation; never generates answers."""
import argparse
from contextlib import redirect_stdout, redirect_stderr
from datetime import date
import json
import math
import os
from pathlib import Path
import time
from uuid import UUID

from sqlalchemy import select, text
from src.database import session_scope, close_database
from src.database.models import IngestionRun, DocumentChunk, DocumentRevision, DocumentAccessRule
from src.database.models import EvidenceScope, EvidenceItem, EvidenceValue
from src.database.runtime import run_async
from src.ingestion import document_packages as packages
from src.ingestion.approved_publication import configuration
from src.ingestion.embeddings import model_fingerprint
from src.paths import REPOSITORY_ROOT
from src.retrieval import RetrievalService
from src.retrieval.service import validate_request
from src.retrieval.config import add_arguments, from_arguments
from src.cli.publish_documents import model_commit


def output_path(path):
    resolved = path.resolve()
    root = (REPOSITORY_ROOT / '.tmp').resolve()
    if not resolved.is_relative_to(root) or resolved == root:
        raise ValueError('output_outside_tmp')
    return resolved


def load_cases(path, record, payload):
    data = json.loads(path.read_text(encoding='utf-8'))
    if (data.get('revision_id') != record['revision_id'] or
            data.get('chunks_sha256') != packages.canonical_hash(payload['chunks'])):
        raise ValueError('stale_cases')
    cases = data.get('cases')
    if not isinstance(cases, list) or not cases:
        raise ValueError('empty_cases')
    seen = set()
    for case in cases:
        if not isinstance(case.get('id'), str) or not case['id'] or case['id'] in seen:
            raise ValueError('invalid_case_id')
        seen.add(case['id'])
        validate_request(case.get('question'), 5)
        if case.get('category') not in {'detail', 'condition', 'aggregate', 'unsupported'}:
            raise ValueError('invalid_category')
        if type(case.get('critical', False)) is not bool:
            raise ValueError('invalid_critical')
        groups = case.get('required_groups')
        if not isinstance(groups, list) or (not groups and case['category'] != 'unsupported'):
            raise ValueError('invalid_evidence_groups')
        for group in groups:
            if (not isinstance(group, list) or not group or
                    any(type(i) is not int or not 0 <= i < len(payload['chunks']) for i in group)):
                raise ValueError('invalid_chunk_index')
    return cases


def grade(case, matches, revision_id, run_id):
    ranks = {m['chunk_index']: rank for rank, m in reversed(list(enumerate(matches, 1)))
             if m['revision_id'] == revision_id and m['run_id'] == run_id}
    group_ranks = [min((ranks[i] for i in group if i in ranks), default=None)
                   for group in case['required_groups']]
    return {'id': case['id'], 'category': case['category'], 'critical': case.get('critical', False),
            'group_ranks': group_ranks,
            'evidence_complete': all(r is not None for r in group_ranks) if case['category'] != 'unsupported' else None,
            'manual_review_required': case['category'] == 'unsupported'}


def summarize(results):
    primary = [r for r in results if r['category'] in {'detail', 'condition'}]
    passed = sum(r['evidence_complete'] is True for r in primary)
    critical = [r for r in results if r['critical'] and r['category'] != 'unsupported']
    rate = passed / len(primary) if primary else None
    aggregates = [r for r in results if r['category'] == 'aggregate']
    return {'primary_passed': passed, 'primary_total': len(primary), 'primary_rate': rate,
            'critical_passed': sum(r['evidence_complete'] is True for r in critical),
            'critical_total': len(critical),
            'aggregate_passed': sum(r['evidence_complete'] is True for r in aggregates),
            'aggregate_total': len(aggregates),
            'target_met': rate is not None and rate >= .9 and all(r['evidence_complete'] for r in critical)}


def check_run(run, expected, count):
    if (run is None or not run.is_active or run.status != 'succeeded' or run.chunk_count != count
            or run.embedding_dimensions != 384 or run.model_name != expected['model_name']
            or run.model_revision != expected['model_revision']
            or any(run.configuration.get(k) != v for k, v in expected.items())):
        raise ValueError('active_run_mismatch')


async def database_state(record, payload, expected):
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION ISOLATION LEVEL REPEATABLE READ, READ ONLY'))
        revision = await db.get(DocumentRevision, UUID(record['revision_id']))
        today = date.today()
        groups = set(await db.scalars(select(DocumentAccessRule.group_name).where(
            DocumentAccessRule.revision_id == UUID(record['revision_id']))))
        if (revision is None or str(revision.document_id) != record['document_id']
                or not revision.is_catalog_current or revision.indexing_decision != 'include'
                or revision.lifecycle_status != 'active' or 'public' not in groups
                or (revision.effective_from and revision.effective_from > today)
                or (revision.effective_to and revision.effective_to < today)):
            raise ValueError('revision_not_eligible')
        run = await db.scalar(select(IngestionRun).where(
            IngestionRun.revision_id == revision.id, IngestionRun.is_active.is_(True)))
        check_run(run, expected, len(payload['chunks']))
        stored = list(await db.scalars(select(DocumentChunk).where(
            DocumentChunk.run_id == run.id).order_by(DocumentChunk.chunk_index)))
        if len(stored) != len(payload['chunks']):
            raise ValueError('stored_count_mismatch')
        norms = []
        if 'structured' in payload:
            for key, model in [('scopes', EvidenceScope), ('items', EvidenceItem), ('values', EvidenceValue)]:
                rows = list(await db.scalars(select(model).where(model.run_id == run.id)))
                if {r.author_id: r.data for r in rows} != {r['id']: r for r in payload['structured'][key]}:
                    raise ValueError('stored_structure_mismatch')
        for actual, wanted in zip(stored, payload['chunks']):
            if actual.author_id != wanted.get('author_id') or actual.role != wanted.get('role'):
                raise ValueError('stored_identity_mismatch')
            if any(getattr(actual, k) != wanted[k] for k in
                   ('chunk_index', 'content', 'source_location', 'extraction_method')):
                raise ValueError('stored_content_mismatch')
            vector = actual.embedding
            if vector is None or len(vector) != 384 or not all(math.isfinite(float(v)) for v in vector):
                raise ValueError('stored_vector_invalid')
            norm = math.hypot(*vector)
            if not math.isclose(norm, 1, abs_tol=1e-5):
                raise ValueError('stored_vector_not_normalized')
            norms.append(norm)
        run_ids = sorted(str(x) for x in await db.scalars(select(IngestionRun.id)))
        chunk_ids = sorted(str(x) for x in await db.scalars(select(DocumentChunk.id)))
        return {'run_id': str(run.id), 'chunk_count': len(stored),
                'norm_min': min(norms), 'norm_max': max(norms),
                'run_ids_sha256': packages.canonical_hash(run_ids),
                'chunk_ids_sha256': packages.canonical_hash(chunk_ids),
                'runs': len(run_ids), 'chunks': len(chunk_ids)}


async def evaluate(args):
    record, payload, files = packages.load_package(REPOSITORY_ROOT, args.package, eligible=True)
    cases = load_cases(args.cases, record, payload)
    cases_hash = packages.sha(args.cases.read_bytes())
    fingerprint = model_fingerprint(args.model_path)
    expected = configuration(payload, args.model_revision)
    expected.update(package_sha256=packages.files_hash(files), model_files_sha256=fingerprint,
                    metadata_sha256=packages.sha(files['metadata.json']))
    service = RetrievalService(model_path=args.model_path, model_revision=args.model_revision,
                               rerank=from_arguments(args))
    try:
        before = await database_state(record, payload, expected)
        start = time.perf_counter()
        await service.start()
        report = {'revision_id': record['revision_id'], 'package_sha256': expected['package_sha256'],
                  'model_files_sha256': fingerprint, 'cases_sha256': cases_hash,
                  'model_load_seconds': time.perf_counter() - start, 'top_k': args.top_k, 'cases': []}
        for case in cases:
            start = time.perf_counter()
            result = await service.retrieve(case['question'], top_k=args.top_k)
            row = grade(case, result['matches'], record['revision_id'], before['run_id'])
            row.update(status=result['status'], seconds=time.perf_counter() - start,
                       candidate_grade=grade(case, result.get('candidate_order', result['matches']), record['revision_id'], before['run_id']),
                       before_rerank=result.get('candidate_order', []),
                       relevance=result.get('relevance'), rerank_seconds=result.get('rerank_seconds', 0.0),
                       reranker_fingerprint=result.get('reranker_fingerprint'),
                       matches=[{k: m[k] for k in ('chunk_index', 'revision_id', 'run_id', 'cosine_distance')}
                                for m in result['matches']])
            for entry, match in zip(row['matches'], result['matches']):
                entry['rerank_score'] = match.get('rerank_score')
            report['cases'].append(row)
        after = await database_state(record, payload, expected)
        if (before != after or packages.snapshot_files(args.package) != files
                or model_fingerprint(args.model_path) != fingerprint
                or packages.sha(args.cases.read_bytes()) != cases_hash):
            raise ValueError('evaluation_inputs_changed')
        report.update(summary=summarize(report['cases']), storage_verification=after,
                      package_unchanged=True, database_ids_unchanged=True,
                      notice='Evidence retrieval only; unsupported cases require agent review, not empty-result assertions.')
        return report
    finally:
        try:
            await service.close()
        finally:
            await close_database()


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--package', required=True, type=Path)
    parser.add_argument('--cases', required=True, type=Path)
    parser.add_argument('--model-path', required=True, type=Path)
    parser.add_argument('--model-revision', required=True, type=model_commit)
    parser.add_argument('--top-k', type=int, choices=range(1, 21), default=5)
    parser.add_argument('--output', type=Path)
    add_arguments(parser)
    args = parser.parse_args(argv)
    if args.output:
        try:
            args.output = output_path(args.output)
            rerank = from_arguments(args)
            if (args.output == args.cases.resolve() or args.output.is_relative_to(args.package.resolve())
                    or args.output.is_relative_to(args.model_path.resolve())
                    or (rerank.enabled and (args.output.is_relative_to(rerank.model_path.resolve())
                        or (rerank.profile_path and args.output == rerank.profile_path.resolve())))):
                raise ValueError('output_overwrites_input')
        except ValueError as exc:
            parser.error(str(exc))
    try:
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            report = run_async(evaluate(args))
        if args.output:
            args.output.parent.mkdir(parents=True, exist_ok=True)
            args.output.write_text(json.dumps(report, ensure_ascii=False, indent=2) + '\n', encoding='utf-8')
    except Exception:
        print('Read-only retrieval evaluation failed; no diagnostic content emitted.')
        return 2
    print(json.dumps(report, ensure_ascii=True))
    return 0 if report['summary']['target_met'] else 1


if __name__ == '__main__':
    raise SystemExit(main())
