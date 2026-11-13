"""Read-only technical checks, independent of database publication eligibility."""
import copy
import re
from datetime import date

from src.catalog.inventory import CatalogError
from src.ingestion import document_packages as packages
from src.ingestion import embeddings


def metadata_blockers(record):
    blockers = []
    for field, expected in (('indexing_decision', 'include'), ('lifecycle_status', 'active')):
        if record[field] != expected:
            blockers.append(field + '_requires_' + expected)
    for field in ('applicability', 'access_groups'):
        if not record[field]:
            blockers.append(field + '_required')
    today = date.today().isoformat()
    if record['effective_from'] and record['effective_from'] > today:
        blockers.append('effective_date_not_started')
    if record['effective_to'] and record['effective_to'] < today:
        blockers.append('effective_date_expired')
    return blockers


def check(root, directory, model_path, model_revision, *, check_embeddings=False):
    result = {'status': 'failed', 'code': 'preflight_failed',
              'embedding_check': 'not_run', 'database_checked': False}
    stage = 'package'
    try:
        packages.require(isinstance(model_revision, str) and
                         re.fullmatch('[0-9a-f]{40}', model_revision), 'model_commit_required')
        record, payload, files = packages.load_package(root, directory, eligible=False)
        result.update(document_id=record['document_id'], revision_id=record['revision_id'],
                      chunk_count=len(payload['chunks']), model_name=embeddings.MODEL_NAME,
                      model_revision=model_revision, package_sha256=packages.files_hash(files),
                      metadata_blockers=metadata_blockers(record))
        stage = 'model'
        fingerprint = embeddings.model_fingerprint(model_path)
        result['model_files_sha256'] = fingerprint
        try:
            model = embeddings.load_model(model_path)
            texts, limit, checks = embeddings.prepare_inputs(model, payload['chunks'])
            result.update(token_limit=limit, chunks=checks)
            exceeded = any(c['status'] == 'exceeded' for c in checks)
            result.update(status='rejected' if exceeded else 'processed',
                          code='chunk_exceeds_model_limit' if exceeded else 'preflight_passed')
            if check_embeddings:
                if exceeded:
                    result['embedding_check'] = 'skipped'
                else:
                    result['embedding_check'] = 'failed'
                    embeddings.encode_chunks(model, texts, copy.deepcopy(payload['chunks']))
                    result['embedding_check'] = 'passed'
        finally:
            # Failed model operations must not hide concurrent input changes.
            try:
                unchanged = packages.snapshot_files(directory) == files
            except (OSError, CatalogError, packages.Rejected):
                unchanged = False
            packages.require(unchanged, 'package_changed')
            try:
                unchanged = embeddings.model_fingerprint(model_path) == fingerprint
            except (OSError, CatalogError, packages.Rejected):
                unchanged = False
            packages.require(unchanged, 'model_changed')
    except packages.Rejected as exc:
        code = str(exc)
        result.update(status='failed' if code in {'local_model_missing', 'local_model_empty'} else 'rejected',
                      code=code)
    except CatalogError:
        result.update(status='rejected', code='source_or_model_rejected')
    except Exception:
        result.update(status='failed', code='package_read_failed' if stage == 'package' else 'model_check_failed')
    return result
