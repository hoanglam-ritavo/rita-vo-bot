"""One grounded draft and at most one independent verification, shared by API and study."""
import time

from .contracts import Draft, Verdict, accepted, validate_draft
from .runtime import SYSTEM, VERIFY


def evidence_sources(matches):
    """Deduplicate exact content only with matching applicability/version/effective metadata."""
    sources, keys = {}, {}
    fields = ('content', 'title', 'applicability', 'effective_from', 'effective_to', 'version_label')
    provenance = ('chunk_id', 'revision_id', 'document_id', 'run_id', 'source_path', 'source_spans')
    for match in matches:
        key = tuple(match.get(name) for name in fields)
        origin = {name: match.get(name) for name in provenance}
        if key in keys:
            sources[keys[key]]['origins'].append(origin)
            continue
        source_id = f'S{len(sources) + 1}'
        keys[key] = source_id
        sources[source_id] = {**{name: match.get(name) for name in fields}, 'origins': [origin]}
    return sources


async def evaluate_question(question, retrieval, runtime, *, top_k=5, expected_corpus=None):
    """Exactly one generation and at most one verifier call on the SAME accepted evidence."""
    started = time.perf_counter()
    result = await retrieval.retrieve(question, top_k=top_k)
    retrieval_seconds = time.perf_counter() - started
    if result['status'] == 'model_mismatch':
        raise ValueError('model_mismatch')
    if result.get('fallback_reason') == 'relevance_unavailable':
        raise ValueError('relevance_unavailable')
    if expected_corpus is not None and result.get('corpus_fingerprint') != expected_corpus:
        raise ValueError('answer_corpus_changed')
    if ((result['matches'] and result.get('relevance', {}).get('status') != 'relevant_candidates')
            or 'relevance_filter_disabled' in result.get('relevance', {}).get('warnings', [])):
        raise ValueError('relevance_unavailable')
    row = {'retrieval_seconds': retrieval_seconds, 'corpus_fingerprint': result.get('corpus_fingerprint'),
           'baseline': result['matches'], 'draft': None, 'verdict': None, 'accepted': False,
           'status': 'insufficient_evidence', 'reason': result.get('fallback_reason'),
           'generation_seconds': 0.0, 'verification_seconds': 0.0, 'inference_calls': 0, 'sources': {}}
    if not result['matches']:
        return row
    sources = evidence_sources(result['matches'])
    row['sources'] = sources
    data = {'question': question, 'evidence': sources}
    started = time.perf_counter()
    raw, usage = await runtime.complete(SYSTEM, data, Draft.model_json_schema(), 1024)
    row.update(draft=raw, generation_usage=usage, inference_calls=1,
               generation_seconds=time.perf_counter() - started)
    try:
        draft = validate_draft(raw, sources)
    except ValueError:
        row['reason'] = 'invalid_draft'
        return row
    started = time.perf_counter()
    verdict, usage = await runtime.complete(VERIFY, {**data, 'draft': raw}, Verdict.model_json_schema(), 512)
    row.update(verdict=verdict, verification_usage=usage, inference_calls=2,
               verification_seconds=time.perf_counter() - started)
    try:
        passed = accepted(draft, verdict)
    except ValueError:
        passed = False
    row.update(accepted=passed, status=draft.status if passed else 'insufficient_evidence',
               reason=None if passed else 'verification_failed')
    return row
