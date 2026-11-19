"""Check and publish one manifest using the existing package publisher."""

from uuid import UUID

from src.catalog.inventory import CatalogError
from src.paths import workspace
from . import corpus_manifest, document_packages as packages, embeddings
from .preflight import metadata_blockers
from .publication_batch import dependency_waves, publication_result


def prepare(root, model_path, model_revision, report, *, expected_manifest=None):
    entries, report['manifest_sha256'] = corpus_manifest.read(root)
    packages.require(expected_manifest is None or report['manifest_sha256'] == expected_manifest,
                     'manifest_changed')
    report['unattempted'] = [entry['document_id'] for entry in entries]
    report['model_revision'] = model_revision
    fingerprint = embeddings.model_fingerprint(model_path)
    report['model_files_sha256'] = fingerprint
    model = embeddings.load_model(model_path)

    records = {}
    selected = {}
    for entry in entries:
        path = workspace(root) / entry['document_id']
        result = {'document_id': entry['document_id'], 'revision_id': entry['revision_id'],
                  'status': 'failed', 'code': 'preflight_failed'}
        report['preflight'].append(result)
        try:
            record, payload, files = packages.load_package(root, path, eligible=True)
            packages.require(corpus_manifest.describe(record, payload, files) == entry, 'manifest_package_mismatch')
            result['metadata_blockers'] = metadata_blockers(record)
            packages.require(not result['metadata_blockers'], 'publication_metadata_blocked')
            _, limit, checks = embeddings.prepare_inputs(model, payload['chunks'])
            result['token_limit'] = limit
            result['overlength_chunks'] = [check['chunk_index'] for check in checks if check['status'] == 'exceeded']
            packages.require(not result['overlength_chunks'], 'chunk_exceeds_model_limit')
            packages.require(packages.snapshot_files(path) == files, 'package_changed')
        except packages.Rejected as exc:
            result.update(status='rejected', code=str(exc))
            raise
        except CatalogError:
            result.update(status='rejected', code='corpus_source_rejected')
            raise
        result.update(status='processed', code='preflight_passed', chunk_count=entry['chunk_count'])
        records[path] = record
        selected[path] = entry
    packages.require(embeddings.model_fingerprint(model_path) == fingerprint, 'model_changed')

    candidates = [(path, entry['revision_id']) for path, entry in selected.items()]
    waves, cyclic = dependency_waves(candidates, records)
    packages.require(not cyclic, 'cyclic_batch_relations')
    report['waves'] = [[selected[path]['document_id'] for path, _ in wave] for wave in waves]
    revision_ids = {entry['revision_id'] for entry in entries}
    external_required = sorted({relation['revision_id'] for record in records.values()
                                for relation in record['relations']
                                if relation['type'] != 'supersedes' and relation['revision_id'] not in revision_ids})
    report['required_external_revisions'] = external_required
    return waves, selected


async def missing_external_revisions(revision_ids):
    from sqlalchemy import select, text
    from src.database import session_scope
    from src.database.models import DocumentRevision

    if not revision_ids:
        return []
    async with session_scope() as db:
        await db.execute(text('SET TRANSACTION READ ONLY'))
        found = await db.scalars(select(DocumentRevision.id).where(
            DocumentRevision.id.in_([UUID(revision_id) for revision_id in revision_ids])))
        existing = {str(revision_id) for revision_id in found}
    return sorted(set(revision_ids) - existing)


async def apply(root, model_path, model_revision, waves, selected, report):
    from src.database import close_database

    try:
        report['phase'] = 'relations'
        missing = await missing_external_revisions(report['required_external_revisions'])
        report['missing_external_revisions'] = missing
        if missing:
            return 'rejected', 'relation_target_missing'
        report['database_checks'] = 'per_wave'

        for wave_number, wave in enumerate(waves, 1):
            # Finish every read-only preview in this wave before its first write.
            for dry_run in (True, False):
                phase = 'dry_run' if dry_run else 'publish'
                report['phase'] = phase
                for path, revision_id in wave:
                    entry = selected[path]
                    document_id = entry['document_id']
                    if not dry_run:
                        report['unattempted'].remove(document_id)
                    result = await publication_result(
                        root, path, revision_id, model_revision, model_path=model_path, dry_run=dry_run,
                        expected_package_sha256=entry['package_sha256'],
                        expected_model_files_sha256=report['model_files_sha256'])
                    report['results'].append(dict(result, document_id=document_id, phase=phase, wave=wave_number))
                    if result['status'] in ('rejected', 'failed'):
                        return result['status'], result['code']
        report['phase'] = 'complete'
        return 'completed', 'corpus_applied'
    finally:
        await close_database()
