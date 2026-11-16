"""Private source-review bridge for collected scoring features; no model/DB IO."""
import hashlib
import html
import json
import math
from pathlib import Path
from uuid import UUID

from src.ingestion import document_packages as packages
from src.paths import REPOSITORY_ROOT
from .scoring import FEATURES
from .semantic_evaluation import validate_dataset


def private_path(value):
    path = Path(value)
    path = (REPOSITORY_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
    root = (REPOSITORY_ROOT / '.tmp').resolve()
    if path == root or not path.is_relative_to(root):
        raise ValueError('scoring_review_path_outside_tmp')
    return path


def _sha(raw):
    return hashlib.sha256(raw).hexdigest()


def _uuid(value):
    if not isinstance(value, str):
        raise ValueError('invalid_scoring_collection_identity')
    try:
        if str(UUID(value)) != value:
            raise ValueError()
    except (ValueError, TypeError, AttributeError):
        raise ValueError('invalid_scoring_collection_identity') from None
    return value


def _source_identity(value):
    if (not isinstance(value, dict) or type(value.get('chunk_index')) is not int
            or value['chunk_index'] < 0):
        raise ValueError('invalid_scoring_collection_identity')
    return _uuid(value.get('document_id')), _uuid(value.get('revision_id')), value['chunk_index']


def _load(dataset_path, collection_path):
    dataset_path, collection_path = private_path(dataset_path), private_path(collection_path)
    dataset_raw, collection_raw = dataset_path.read_bytes(), collection_path.read_bytes()
    dataset, collection = packages.decode(dataset_raw), packages.decode(collection_raw)
    cases = validate_dataset(dataset, collect_only=True)
    if (collection.get('collect_only') is not True
            or collection.get('database_and_packages_unchanged') is not True
            or collection.get('dataset_sha256') != _sha(dataset_raw)
            or collection.get('feature_names') != list(FEATURES)
            or not isinstance(collection.get('corpus_fingerprint'), str)
            or not collection['corpus_fingerprint']):
        raise ValueError('scoring_collection_binding_mismatch')
    pins = dataset.get('packages')
    if not isinstance(pins, list) or not pins:
        raise ValueError('scoring_source_pins_required')
    sources, snapshots, source_hashes, seen = {}, [], {}, set()
    for pin in pins:
        if not isinstance(pin, dict) or set(pin) != {'path', 'sha256'}:
            raise ValueError('invalid_scoring_source_pin')
        path = Path(pin['path'])
        path = (REPOSITORY_ROOT / path).resolve() if not path.is_absolute() else path.resolve()
        if not path.is_relative_to(REPOSITORY_ROOT.resolve()) or path in seen:
            raise ValueError('invalid_scoring_source_pin')
        seen.add(path)
        record, payload, files = packages.load_package(REPOSITORY_ROOT, path, eligible=True)
        if packages.files_hash(files) != pin['sha256']:
            raise ValueError('scoring_source_pin_changed')
        logical = path.relative_to(REPOSITORY_ROOT).as_posix()
        source_hashes[logical] = pin['sha256']
        snapshots.append((path, files))
        extraction = packages.decode(files['extraction.json'])
        authored = packages.decode(files['chunks.json'])['chunks']
        normalized = payload['chunks']
        if (len(authored) != len(normalized) or any(
                chunk.get('chunk_index') != index or chunk.get('content') != raw.get('content')
                for index, (raw, chunk) in enumerate(zip(authored, normalized)))):
            raise ValueError('scoring_authored_publication_mismatch')
        segments = {segment['segment_id']: segment for segment in extraction['segments']}
        # load_package returns publication-ready chunks, whose source refs have
        # already been normalized into source_location. The reviewed source
        # slices must come from the hash-pinned authored chunks, not that DTO.
        for index, chunk in enumerate(authored):
            identity = record['document_id'], record['revision_id'], index
            if identity in sources:
                raise ValueError('duplicate_scoring_source_identity')
            excerpts = []
            for ref in chunk['source_refs']:
                segment = segments[ref['segment_id']]
                excerpts.append({'source_ref': ref, 'location': segment.get('location'),
                    'text': segment['text'][ref['start']:ref['end']]})
            sources[identity] = {'package': logical, 'content': chunk['content'],
                'source_excerpts': excerpts}
    if seen != {path.resolve() for path in packages.package_paths(REPOSITORY_ROOT)}:
        raise ValueError('whole_corpus_scoring_source_pins_required')
    by_case = {case['id']: case for case in cases}
    for case in cases:
        for group in case['required_groups']:
            if any(_source_identity(ref) not in sources for ref in group):
                raise ValueError('unpinned_scoring_required_source')
    pipelines = collection.get('pipelines')
    if (not isinstance(pipelines, dict) or not {'baseline', 'semantic'} <= set(pipelines)
            or set(pipelines) - {'baseline', 'semantic', 'intent'}):
        raise ValueError('paired_scoring_collection_required')
    arms, unions, uuid_sources, source_uuids = {}, {key: {} for key in by_case}, {}, {}
    for arm, pipeline in sorted(pipelines.items()):
        rows = pipeline.get('candidate_collection')
        if (not isinstance(rows, list) or len(rows) != len(by_case)
                or {row.get('id') for row in rows} != set(by_case)):
            raise ValueError('scoring_collection_cases_mismatch')
        arm_cases = {}
        for row in rows:
            if row['id'] in arm_cases:
                raise ValueError('scoring_collection_cases_mismatch')
            matches, features = row.get('after_rerank'), row.get('scoring_features')
            if not isinstance(matches, list) or len(matches) > 30 or not isinstance(features, dict):
                raise ValueError('invalid_scoring_collection_candidates')
            mapped = {}
            for match in matches:
                identity = _source_identity(match)
                chunk_id = _uuid(match.get('chunk_id'))
                run_id = _uuid(match.get('run_id'))
                if identity not in sources or chunk_id in mapped:
                    raise ValueError('invalid_scoring_collection_candidates')
                full_identity = (*identity, run_id)
                if (chunk_id in uuid_sources and uuid_sources[chunk_id] != full_identity
                        or identity in source_uuids and source_uuids[identity] != chunk_id):
                    raise ValueError('conflicting_scoring_candidate_identity')
                uuid_sources[chunk_id], source_uuids[identity] = full_identity, chunk_id
                vector = features.get(chunk_id)
                if (not isinstance(vector, list) or len(vector) != len(FEATURES)
                        or any(type(v) not in (int, float) or not math.isfinite(v) for v in vector)):
                    raise ValueError('invalid_scoring_collection_features')
                mapped[chunk_id] = {'identity': identity, 'features': vector}
                unions[row['id']][chunk_id] = identity
            if set(features) != set(mapped):
                raise ValueError('scoring_collection_feature_set_mismatch')
            arm_cases[row['id']] = mapped
        arms[arm] = arm_cases
    def verify():
        if (dataset_path.read_bytes() != dataset_raw or collection_path.read_bytes() != collection_raw
                or any(packages.snapshot_files(path) != files for path, files in snapshots)):
            raise ValueError('scoring_review_inputs_changed')
    verify()
    return {'cases': by_case, 'arms': arms, 'unions': unions, 'sources': sources,
        'source_uuids': source_uuids, 'source_package_hashes': source_hashes,
        'dataset_sha256': _sha(dataset_raw), 'collection_sha256': _sha(collection_raw),
        'corpus_fingerprint': collection['corpus_fingerprint'], 'verify': verify}


def worksheet(dataset_path, collection_path):
    """Return unranked source material, an unapproved declaration, and escaped HTML."""
    context = _load(dataset_path, collection_path)
    cases, labels = [], {}
    for case_id, case in sorted(context['cases'].items()):
        candidates = []
        for chunk_id, identity in sorted(context['unions'][case_id].items(), key=lambda item: (*item[1], item[0])):
            candidates.append({'chunk_id': chunk_id, 'document_id': identity[0],
                'revision_id': identity[1], 'chunk_index': identity[2],
                **context['sources'][identity], 'human_label': None})
        required = [[{'reference': ref, **context['sources'][_source_identity(ref)]}
                     for ref in group] for group in case['required_groups']]
        cases.append({'id': case_id, 'question': case['question'], 'split': case['split'],
            'intent_group': case['intent_group'], 'declared_relevant': case['relevant'],
            'required_source_groups': required, 'candidates': candidates})
        labels[case_id] = {candidate['chunk_id']: None for candidate in candidates}
    binding = {key: context[key] for key in
        ('dataset_sha256', 'collection_sha256', 'corpus_fingerprint', 'source_package_hashes')}
    review = {'schema_version': 1, **binding, 'human_approved': False,
        'source_evidence_checked': False, 'candidate_labels_checked': False,
        'split_independence_checked': False, 'case_definitions_checked': False,
        'review_statement': '', 'candidate_labels': labels}
    result = {'schema_version': 1, **binding, 'status': 'unapproved_source_review', 'cases': cases}
    chunks = ['<!doctype html><html lang="en"><meta charset="utf-8"><title>Scoring source review</title>',
        '<h1>Unapproved scoring source review</h1><p>Review every question, source group and candidate. '
        'Unknown labels remain unresolved. No scores, ranks or numeric features are shown.</p>']
    escape = lambda value: html.escape(str(value), quote=True)
    for case in cases:
        chunks.extend([f'<section><h2>{escape(case["id"])}</h2><p>{escape(case["question"])}</p>',
            f'<p>Split: {escape(case["split"])}; family: {escape(case["intent_group"])}; '
            f'declared supported: {escape(case["declared_relevant"])}</p>', '<h3>Required source groups</h3>'])
        for number, group in enumerate(case['required_source_groups'], 1):
            chunks.append(f'<h4>Group {number}</h4>')
            for source in group:
                chunks.append(f'<pre>{escape(json.dumps(source, ensure_ascii=False, indent=2))}</pre>')
        chunks.append('<h3>Candidates, ordered by source identity</h3>')
        for candidate in case['candidates']:
            chunks.append(f'<details><summary>{escape(candidate["chunk_id"])} '
                f'[{escape(candidate["chunk_index"])}]</summary><pre>'
                f'{escape(json.dumps(candidate, ensure_ascii=False, indent=2))}</pre></details>')
        chunks.append('</section>')
    chunks.append('</html>')
    context['verify']()
    return result, review, '\n'.join(chunks)


def export_development(dataset_path, collection_path, review_path):
    """Export only actually declared, hash-bound, complete human source labels."""
    context = _load(dataset_path, collection_path)
    review_path = private_path(review_path)
    review_raw = review_path.read_bytes()
    review = packages.decode(review_raw)
    flags = ('human_approved', 'source_evidence_checked', 'candidate_labels_checked',
             'split_independence_checked', 'case_definitions_checked')
    if (not isinstance(review, dict) or review.get('schema_version') != 1
            or any(review.get(flag) is not True for flag in flags)
            or not isinstance(review.get('review_statement'), str) or not review['review_statement'].strip()):
        raise ValueError('actual_scoring_source_review_required')
    for key in ('dataset_sha256', 'collection_sha256', 'corpus_fingerprint', 'source_package_hashes'):
        if review.get(key) != context[key]:
            raise ValueError('scoring_review_binding_mismatch')
    labels = review.get('candidate_labels')
    if not isinstance(labels, dict) or set(labels) != set(context['cases']):
        raise ValueError('complete_scoring_union_labels_required')
    rows = []
    for case_id, case in sorted(context['cases'].items()):
        if case['split'] != 'regression':
            raise ValueError('scoring_development_cannot_consume_acceptance_split')
        case_labels = labels[case_id]
        if (not isinstance(case_labels, dict) or set(case_labels) != set(context['unions'][case_id])
                or any(type(value) is not bool for value in case_labels.values())):
            raise ValueError('complete_scoring_union_labels_required')
        groups = [[context['source_uuids'].get(_source_identity(ref),
            'source:' + ':'.join(map(str, _source_identity(ref)))) for ref in group]
                  for group in case['required_groups']]
        for arm, cases in sorted(context['arms'].items()):
            if not cases[case_id]:
                # The row-based fitting format cannot represent a case/arm
                # with no feature rows. Never silently omit that hard case.
                raise ValueError('empty_scoring_case_arm_requires_diagnostic')
            for chunk_id, candidate in sorted(cases[case_id].items()):
                relevant = case_labels[chunk_id]
                if (not groups and relevant or any(chunk_id in group for group in groups) and not relevant):
                    raise ValueError('scoring_source_label_evidence_conflict')
                rows.append({'case_id': case_id, 'intent_group': case['intent_group'], 'arm': arm,
                    'chunk_id': chunk_id, 'features': candidate['features'], 'relevant': relevant,
                    'required_groups': groups})
    context['verify']()
    if review_path.read_bytes() != review_raw:
        raise ValueError('scoring_review_inputs_changed')
    return {'schema_version': 1, 'split': 'development', 'reviewed': True,
        'review_notes': review['review_statement'].strip(), 'feature_names': list(FEATURES),
        'dataset_sha256': context['dataset_sha256'], 'collection_sha256': context['collection_sha256'],
        'source_package_hashes': context['source_package_hashes'],
        'corpus_fingerprint': context['corpus_fingerprint'], 'review_sha256': _sha(review_raw), 'rows': rows}
