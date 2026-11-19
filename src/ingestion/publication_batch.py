"""Shared dependency ordering and per-package publication results."""

from src.catalog.inventory import CatalogError
from . import document_packages as packages


def dependency_waves(candidates, records):
    """Return the ready waves and any remainder blocked by a cycle."""
    remaining = list(candidates)
    waves = []
    while remaining:
        revision_ids = {revision_id for _, revision_id in remaining}
        ready = [(path, revision_id) for path, revision_id in remaining
                 if not any(relation['revision_id'] in revision_ids
                            for relation in records[path]['relations'])]
        if not ready:
            break
        waves.append(ready)
        ready_set = set(ready)
        remaining = [candidate for candidate in remaining if candidate not in ready_set]
    return waves, remaining


async def publication_result(root, path, revision_id, model_revision, **options):
    from .approved_publication import publish
    from .attempts import AttemptUncertain
    from .publish import PublishRejected

    try:
        return await publish(root, path, model_revision, **options)
    except packages.Rejected as exc:
        status, code = 'rejected', str(exc)
        attempt_id = getattr(exc, 'attempt_id', None)
    except (PublishRejected, CatalogError) as exc:
        status, code = 'rejected', 'metadata_or_source_rejected'
        attempt_id = getattr(exc, 'attempt_id', None)
    except Exception as exc:
        status = 'failed'
        code = 'publication_outcome_unknown' if isinstance(exc, AttemptUncertain) else 'publication_failed'
        attempt_id = getattr(exc, 'attempt_id', None)
    result = {'status': status, 'code': code, 'revision_id': revision_id}
    if attempt_id is not None:
        result['attempt_id'] = attempt_id
    return result
