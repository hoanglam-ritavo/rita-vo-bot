"""Model-free semantic authoring inventory, coverage checks and review-gated export."""
import json
from pathlib import Path
import re

from src.paths import REPOSITORY_ROOT
from .corpus import SemanticPreparationError, corpus_snapshot, digest, read_json

DISPOSITIONS = {'pending', 'covered', 'administrative', 'duplicate', 'ambiguous', 'unsupported'}
ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9._-]{0,119}')


def private_path(value):
    candidate = Path(value)
    if not candidate.is_absolute():
        candidate = REPOSITORY_ROOT / candidate
    resolved = candidate.resolve()
    if not resolved.is_relative_to((REPOSITORY_ROOT / '.tmp').resolve()):
        raise SemanticPreparationError('private_path_required')
    return resolved


def write_new(path, value):
    path = private_path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    try:
        with path.open('x', encoding='utf-8', newline='\n') as stream:
            json.dump(value, stream, ensure_ascii=False, indent=2, allow_nan=False)
            stream.write('\n')
    except FileExistsError:
        raise SemanticPreparationError('output_exists') from None
    except OSError:
        raise SemanticPreparationError('output_unavailable') from None
    return path


def inventory(root=None):
    snapshot = corpus_snapshot(root)
    return {'schema_version': 1, 'reviewed': False, 'review_notes': '',
        'package_fingerprint': snapshot['package_fingerprint'], 'packages': snapshot['packages'],
        'concepts': [], 'units': [], 'chunks': [{**chunk, 'disposition': 'pending',
        'unit_ids': [], 'reason': ''} for chunk in snapshot['chunks']]}


def _nonblank(value):
    return isinstance(value, str) and bool(value.strip())


def _id(value):
    return isinstance(value, str) and ID.fullmatch(value) is not None


def validate_manifest(data, root=None):
    """Structural coverage only, never a claim of source fidelity/human review."""
    errors, warnings = [], []
    snapshot = corpus_snapshot(root)
    expected = {'schema_version', 'reviewed', 'review_notes', 'package_fingerprint',
                'packages', 'concepts', 'units', 'chunks'}
    if not isinstance(data, dict) or set(data) != expected or type(data.get('schema_version')) is not int or data['schema_version'] != 1:
        return {'status': 'invalid', 'errors': ['invalid_semantic_manifest'], 'warnings': [],
                'review_required': True, 'export_allowed': False}
    if type(data['reviewed']) is not bool or not isinstance(data['review_notes'], str):
        errors.append('invalid_review_fields')
    if data['package_fingerprint'] != snapshot['package_fingerprint'] or data['packages'] != snapshot['packages']:
        errors.append('package_drift')
    if any(not isinstance(data[key], list) for key in ('concepts', 'units', 'chunks')):
        return {'status': 'invalid', 'errors': errors + ['invalid_collections'], 'warnings': [],
                'review_required': True, 'export_allowed': False}
    # The runtime schema is the single owner of lexical rules; importing it
    # loads neither models nor the database.
    from src.retrieval.semantics import normalized, validate_artifact
    try:
        validate_artifact({key: data[key] for key in ('schema_version', 'reviewed', 'review_notes',
            'package_fingerprint', 'concepts')}, require_reviewed=False)
    except ValueError:
        errors.append('invalid_semantic_concepts')
    alias_targets = {}
    for concept in data['concepts']:
        if not isinstance(concept, dict) or not isinstance(concept.get('canonical'), str) or not isinstance(concept.get('aliases'), list):
            continue
        for alias in concept['aliases']:
            if isinstance(alias, str):
                alias_targets.setdefault(normalized(alias).casefold(), set()).add(normalized(concept['canonical']).casefold())
    ambiguous_aliases = sum(len(targets) > 1 for targets in alias_targets.values())
    if ambiguous_aliases:
        warnings.append('ambiguous_aliases_suppressed_at_runtime')
    concept_ids = {c.get('id') for c in data['concepts'] if isinstance(c, dict) and _id(c.get('id'))}
    actual_chunks = {(c['document_id'], c['chunk_index']): c for c in snapshot['chunks']}
    authored_chunks, units, declarations = {}, {}, {}
    for i, chunk in enumerate(data['chunks']):
        code = f'chunks[{i}]'
        if not isinstance(chunk, dict):
            errors.append(code + ':invalid_chunk')
            continue
        key = (chunk.get('document_id'), chunk.get('chunk_index'))
        if not isinstance(key[0], str) or type(key[1]) is not int or key not in actual_chunks or key in authored_chunks:
            errors.append(code + ':invalid_chunk_identity')
            continue
        authored_chunks[key] = chunk
        current = actual_chunks[key]
        if set(chunk) != set(current) | {'disposition', 'unit_ids', 'reason'} or any(chunk.get(k) != v for k, v in current.items()):
            errors.append(code + ':chunk_drift')
        disposition = chunk.get('disposition')
        ids = chunk.get('unit_ids')
        if not isinstance(disposition, str) or disposition not in DISPOSITIONS or not isinstance(ids, list) or any(not _id(v) for v in ids) or len(ids) != len(set(ids)):
            errors.append(code + ':invalid_disposition')
            continue
        if disposition == 'covered' and not ids:
            errors.append(code + ':missing_units')
        if disposition not in ('covered', 'pending') and not _nonblank(chunk.get('reason')):
            errors.append(code + ':missing_disposition_reason')
        declarations[key] = set(ids)
    if set(authored_chunks) != set(actual_chunks):
        errors.append('incomplete_chunk_inventory')
    for i, unit in enumerate(data['units']):
        code = f'units[{i}]'
        fields = {'id', 'concept_ids', 'summary', 'conditions', 'evidence', 'questions', 'review_status', 'review_notes'}
        if not isinstance(unit, dict) or set(unit) != fields or not _id(unit.get('id')) or unit['id'] in units:
            errors.append(code + ':invalid_unit')
            continue
        units[unit['id']] = unit
        concepts = unit['concept_ids']
        if not isinstance(concepts, list) or not concepts or any(not isinstance(v, str) or v not in concept_ids for v in concepts):
            errors.append(code + ':invalid_concept_reference')
        if not _nonblank(unit['summary']) or not isinstance(unit['conditions'], list) or any(not _nonblank(c) for c in unit['conditions']):
            errors.append(code + ':missing_meaning_or_conditions')
        questions = unit['questions']
        if not isinstance(questions, dict) or set(questions) != {'direct', 'paraphrase', 'contrast'} or any(not isinstance(v, str) for v in questions.values()):
            errors.append(code + ':invalid_questions')
        evidence = unit['evidence']
        if not isinstance(evidence, list) or not evidence:
            errors.append(code + ':missing_evidence')
        else:
            for ref in evidence:
                if not isinstance(ref, dict) or set(ref) != {'document_id', 'chunk_index', 'source_refs'}:
                    errors.append(code + ':invalid_evidence')
                    continue
                key = (ref['document_id'], ref['chunk_index'])
                if not isinstance(key[0], str) or type(key[1]) is not int or key not in actual_chunks:
                    errors.append(code + ':unknown_evidence')
                    continue
                refs = ref['source_refs']
                if (not isinstance(refs, list) or not refs or any(not isinstance(r, dict)
                        or r not in actual_chunks[key]['source_refs'] for r in refs)
                        or len({digest(r) for r in refs}) != len(refs)):
                    errors.append(code + ':source_reference_drift')
                if unit['id'] not in declarations.get(key, set()):
                    errors.append(code + ':missing_reverse_reference')
        if unit['review_status'] not in ('draft', 'reviewed') or not isinstance(unit['review_notes'], str):
            errors.append(code + ':invalid_review')
        if unit['review_status'] == 'reviewed' and (not _nonblank(unit['review_notes']) or not isinstance(questions, dict)
                or any(not _nonblank(questions.get(k)) for k in ('direct', 'paraphrase'))):
            errors.append(code + ':incomplete_review')
        elif unit['review_status'] == 'reviewed' and normalized(questions['direct']).casefold() == normalized(questions['paraphrase']).casefold():
            errors.append(code + ':duplicate_question_wording')
    for ids in declarations.values():
        if ids - units.keys():
            errors.append('unknown_unit_reference')
    for key, ids in declarations.items():
        for unit_id in ids & units.keys():
            evidence = units[unit_id].get('evidence')
            if not isinstance(evidence, list) or not any(isinstance(ref, dict)
                    and ref.get('document_id') == key[0] and ref.get('chunk_index') == key[1]
                    for ref in evidence):
                errors.append('missing_forward_evidence_reference')
    pending = sum(c.get('disposition') == 'pending' for c in authored_chunks.values())
    unreviewed = sum(u['review_status'] != 'reviewed' for u in units.values())
    review_required = (data['reviewed'] is not True or not _nonblank(data['review_notes']) or pending > 0 or unreviewed > 0)
    if data['reviewed'] is True and review_required:
        errors.append('incomplete_review_claim')
    if review_required:
        warnings.append('human_source_review_required')
    if not units:
        warnings.append('semantic_units_not_authored')
    used = {c for u in units.values() if isinstance(u.get('concept_ids'), list) for c in u['concept_ids'] if isinstance(c, str)}
    if concept_ids - used:
        warnings.append('unused_concepts')
    return {'status': 'invalid' if errors else 'valid', 'errors': sorted(set(errors)), 'warnings': warnings,
        'review_required': review_required, 'export_allowed': not errors and not review_required and bool(units),
        'package_fingerprint': snapshot['package_fingerprint'], 'package_count': snapshot['package_count'],
        'chunk_count': snapshot['chunk_count'], 'inventoried_chunks': len(authored_chunks),
        'pending_chunks': pending, 'unit_count': len(units), 'unreviewed_units': unreviewed,
        'concept_count': len(concept_ids), 'ambiguous_alias_count': ambiguous_aliases,
        'coverage_kind': 'structural_inventory_only', 'semantic_completeness_assessed': False,
        'dispositions': {d: sum(c.get('disposition') == d for c in authored_chunks.values()) for d in sorted(DISPOSITIONS)}}


def export_artifact(data, root=None):
    report = validate_manifest(data, root)
    if report['errors']:
        raise SemanticPreparationError('invalid_semantic_manifest')
    if not report['export_allowed']:
        raise SemanticPreparationError('human_source_review_required')
    # Chunk IDs/source excerpts never enter the runtime lexicon.
    result = {key: data[key] for key in ('schema_version', 'reviewed', 'review_notes', 'package_fingerprint', 'concepts')}
    from src.retrieval.semantics import validate_artifact
    validate_artifact(result)
    return result


def main(argv=None):
    import argparse
    parser = argparse.ArgumentParser(description='Model-free, review-gated corpus semantics preparation.')
    commands = parser.add_subparsers(dest='command', required=True)
    for name in ('inventory', 'validate', 'export'):
        command = commands.add_parser(name)
        command.add_argument('--chunks-root', type=Path, default=REPOSITORY_ROOT / 'chunks')
        command.add_argument('--json', action='store_true')
        if name != 'inventory':
            command.add_argument('--manifest', required=True)
        if name != 'validate':
            command.add_argument('--output', required=True)
    args = parser.parse_args(argv)
    try:
        if args.command == 'inventory':
            data = inventory(args.chunks_root)
            path = write_new(args.output, data)
            result = {'status': 'draft_created', 'package_count': len(data['packages']),
                'chunk_count': len(data['chunks']), 'review_required': True,
                'package_fingerprint': data['package_fingerprint'], 'output': str(path)}
        else:
            data = read_json(private_path(args.manifest))
            result = validate_manifest(data, args.chunks_root)
            if args.command == 'export':
                artifact = export_artifact(data, args.chunks_root)
                path = write_new(args.output, artifact)
                result = {'status': 'reviewed_lexicon_exported', 'output': str(path),
                    'artifact_hash': digest(artifact), 'calibration_required': True}
        print(json.dumps(result, ensure_ascii=True))
        return 1 if result.get('errors') else 0
    except (SemanticPreparationError, ValueError) as exc:
        code = str(exc) if isinstance(exc, SemanticPreparationError) else 'invalid_semantic_artifact'
        print(json.dumps({'status': 'failed', 'error': code}))
        return 2

