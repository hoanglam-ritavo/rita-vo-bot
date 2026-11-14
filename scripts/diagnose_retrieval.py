"""Offline diagnostics for a saved benchmark collection; no model or DB access.

Writes a metadata-only diagnostic and a private, unranked source-review worksheet.
It never calibrates, changes labels, emits a profile, or accepts runtime traffic.
"""
import argparse
import hashlib
import json
from pathlib import Path

from src.paths import REPOSITORY_ROOT
from src.ingestion import document_packages as packages


def digest(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def workspace_path(value):
    path = Path(value)
    path = (REPOSITORY_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    if not path.is_relative_to(REPOSITORY_ROOT.resolve()):
        raise ValueError('input_outside_repository')
    return path


def analyze(manifest, collection, *, reference=None):
    """Require exact pinned cases/candidates; never turn absent labels into false."""
    hashes = {}

    def read(path):
        data = path.read_bytes()
        name, sha = str(path.relative_to(REPOSITORY_ROOT)), hashlib.sha256(data).hexdigest()
        if name in hashes and hashes[name] != sha:
            raise ValueError('inputs_changed')
        hashes[name] = sha
        return json.loads(data.decode('utf-8'))

    cases, sources, protected = {}, {}, {manifest, collection}
    for dataset in read(manifest)['datasets']:
        package = workspace_path(dataset['package'])
        case_path = workspace_path(dataset['cases'])
        metadata_path, chunks_path = package / 'metadata.json', package / 'chunks.json'
        extraction_path = package / 'extraction.json'
        protected.update((case_path, metadata_path, chunks_path, extraction_path))
        meta, payload, files = packages.load_package(REPOSITORY_ROOT, package, eligible=True)
        for name, content in files.items():
            path = package / name
            key, sha = str(path.relative_to(REPOSITORY_ROOT)), hashlib.sha256(content).hexdigest()
            if key in hashes and hashes[key] != sha:
                raise ValueError('inputs_changed')
            hashes[key] = sha
            protected.add(path)
        data = read(case_path)
        chunk_hash = packages.canonical_hash(payload['chunks'])
        if data['revision_id'] != meta['revision_id'] or data['chunks_sha256'] != chunk_hash:
            raise ValueError('stale_cases')
        for case in data['cases']:
            if case['id'] in cases:
                raise ValueError('duplicate_case_id')
            cases[case['id']] = (case, meta['revision_id'], package.relative_to(REPOSITORY_ROOT).as_posix())
    # Candidates can come from other packages, not only the case's target source.
    for package in sorted((REPOSITORY_ROOT / 'chunks').iterdir()):
        if not (package / 'metadata.json').is_file():
            continue
        meta, payload = read(package / 'metadata.json'), read(package / 'chunks.json')
        chunks = payload if isinstance(payload, list) else payload['chunks']
        extraction = read(package / 'extraction.json')
        segments = {s['segment_id']: s for s in extraction['segments']}
        protected.update(package / name for name in ('metadata.json', 'chunks.json', 'extraction.json'))
        for index, chunk in enumerate(chunks):
            excerpts = []
            for ref in chunk.get('source_refs', []):
                segment = segments[ref['segment_id']]
                excerpts.append({'source_ref': ref, 'location': segment.get('location'),
                    'text': segment['text'][ref['start']:ref['end']]})
            sources[(meta['revision_id'], index)] = {
                'package': package.relative_to(REPOSITORY_ROOT).as_posix(),
                'content': chunk['content'], 'source_excerpts': excerpts}
    report = read(collection)
    rows = report['cases']
    if (len(rows) != len(cases) or {r['id'] for r in rows} != set(cases)
            or report['manifest_sha256'] != digest(manifest)):
        raise ValueError('collection_case_mismatch')
    cutoff = None
    if reference is not None:
        old = read(reference)
        protected.add(reference)
        if (old.get('fingerprint') != report.get('fingerprint')
                or old.get('corpus_fingerprint') != report.get('corpus_fingerprint')):
            raise ValueError('reference_identity_mismatch')
        cutoff = old.get('calibration', {}).get('threshold')
        if type(cutoff) not in (int, float):
            raise ValueError('reference_threshold_missing')
    worksheet, diagnostics = [], []
    for row in rows:
        case, revision, target_package = cases[row['id']]
        candidates = row['after_rerank']
        ids = [m['chunk_id'] for m in candidates]
        if len(ids) != len(set(ids)):
            raise ValueError('duplicate_candidate')
        before_ids = [m['chunk_id'] for m in row['before_rerank']]
        if len(before_ids) != len(ids) or set(before_ids) != set(ids):
            raise ValueError('candidate_pool_mismatch')
        labels = case.get('candidate_labels', {})
        if labels and (set(labels) != set(ids) or any(type(v) is not bool for v in labels.values())):
            raise ValueError('incomplete_candidate_labels')
        entries = []
        for match in candidates:
            source = sources[(match['revision_id'], match['chunk_index'])]
            entries.append({'chunk_id': match['chunk_id'], 'chunk_index': match['chunk_index'],
                'revision_id': match['revision_id'], **source,
                'previous_label': labels.get(match['chunk_id']),
                'reviewed_label': None, 'review_reason': None, 'user_approved': False})
        # The worksheet intentionally excludes rankings, distances, scores and cutoff.
        worksheet.append({'id': case['id'], 'question': case['question'],
            'split': case['split'], 'intent_group': case['intent_group'],
            'target_package': target_package, 'target_revision_id': revision,
            'proposed_supported': case['relevant'], 'required_groups': case['required_groups'],
            'user_approved': False,
            'candidates': sorted(entries, key=lambda x: (x['package'], x['chunk_index'], x['chunk_id']))})
        targets = []
        for group in case['required_groups']:
            hits = [m for m in candidates if m['revision_id'] == revision and m['chunk_index'] in group]
            targets.append({'required_chunk_indexes': group,
                'hits': [{'chunk_id': m['chunk_id'], 'chunk_index': m['chunk_index'],
                    'before_rank': before_ids.index(m['chunk_id']) + 1,
                    'after_rank': ids.index(m['chunk_id']) + 1,
                    'score': m['rerank_score'],
                    'passes_historical_cutoff': m['rerank_score'] >= cutoff if cutoff is not None else None}
                    for m in hits]})
        negatives = [m for m in candidates if labels.get(m['chunk_id']) is False]
        highest = max(negatives, key=lambda m: m['rerank_score']) if negatives else None
        diagnostics.append({'id': case['id'], 'split': case['split'],
            'target_package': target_package, 'target_revision_id': revision,
            'candidate_count': len(candidates), 'evidence_groups': targets,
            'highest_previous_negative': {k: highest[k] for k in
                ('chunk_id', 'chunk_index', 'revision_id', 'rerank_score')} if highest else None})
    return ({'manifest_sha256': digest(manifest), 'collection_sha256': digest(collection),
        'corpus_fingerprint': report['corpus_fingerprint'], 'fingerprint': report['fingerprint'],
        'historical_cutoff_diagnostic_only': cutoff, 'profile_generated': False,
        'user_approved': False, 'input_hashes': hashes, 'cases': diagnostics},
        {'reviewed': False, 'labels_complete': False,
         'notice': 'Draft source-review worksheet. Null is unresolved, never false. '
                   'No calibration/promotion is authorized by this file. Old holdout is diagnostic only.',
         'cases': worksheet}, protected)


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--manifest', type=Path, required=True)
    parser.add_argument('--collection', type=Path, required=True)
    parser.add_argument('--reference', type=Path, help='Historical report, cutoff for diagnosis only.')
    parser.add_argument('--output', type=Path, required=True, help='New directory under .tmp.')
    args = parser.parse_args(argv)
    try:
        manifest, collection = workspace_path(args.manifest), workspace_path(args.collection)
        reference = workspace_path(args.reference) if args.reference else None
        output = workspace_path(args.output)
        temporary = (REPOSITORY_ROOT / '.tmp').resolve()
        if output == temporary or not output.is_relative_to(temporary) or output.exists():
            raise ValueError('output_requires_new_tmp_directory')
        result, worksheet, protected = analyze(manifest, collection, reference=reference)
        if any(p.is_relative_to(output) for p in protected):
            raise ValueError('output_overlaps_input')
        # Detect source/collection edits during analysis before persisting a result.
        if any(digest(REPOSITORY_ROOT / name) != sha for name, sha in result['input_hashes'].items()):
            raise ValueError('inputs_changed')
        output.mkdir(parents=True, exist_ok=False)
        for name, data in [('diagnostic.json', result), ('source-review.json', worksheet)]:
            with (output / name).open('x', encoding='utf-8') as stream:
                json.dump(data, stream, ensure_ascii=False, indent=2, allow_nan=False)
                stream.write('\n')
        print(json.dumps({'status': 'diagnostic_only', 'cases': len(result['cases']),
                          'profile_generated': False, 'user_approved': False}))
        return 0
    except Exception:
        print('Offline diagnostic failed; no private diagnostic content emitted.')
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
