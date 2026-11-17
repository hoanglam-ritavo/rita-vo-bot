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


def checked_relevance(result, *, allow_unassessed=False):
    """Only an explicit server-side opt-out may consume marked unassessed candidates."""
    metadata = result.get('relevance')
    if (not isinstance(metadata, dict) or set(metadata) != {'status', 'warnings'}
            or not isinstance(metadata['status'], str)
            or not isinstance(metadata['warnings'], list)
            or any(not isinstance(warning, str) for warning in metadata['warnings'])):
        raise ValueError('relevance_unavailable')
    status, warnings = metadata['status'], list(metadata['warnings'])
    disabled = 'relevance_filter_disabled' in warnings
    if allow_unassessed:
        valid = disabled and status == ('unassessed' if result['matches'] else 'no_candidates')
    else:
        valid = not disabled and (status == 'relevant_candidates' if result['matches']
                                  else status in {'no_candidates', 'low_relevance'})
    if not valid:
        raise ValueError('relevance_unavailable')
    if 'evidence_completeness_not_assessed' not in warnings:
        warnings.append('evidence_completeness_not_assessed')
    return {'status': status, 'warnings': warnings}


async def evaluate_question(question, retrieval, runtime, *, top_k=5, expected_corpus=None,
                            allow_unassessed=False):
    """One draft/verifier on the same evidence; official evaluation requires assessed evidence."""
    started = time.perf_counter()
    result = await retrieval.retrieve(question, top_k=top_k)
    retrieval_seconds = time.perf_counter() - started
    if result['status'] == 'model_mismatch':
        raise ValueError('model_mismatch')
    if result.get('fallback_reason') == 'relevance_unavailable':
        raise ValueError('relevance_unavailable')
    if expected_corpus is not None and result.get('corpus_fingerprint') != expected_corpus:
        raise ValueError('answer_corpus_changed')
    relevance = checked_relevance(result, allow_unassessed=allow_unassessed)
    row = {'retrieval_seconds': retrieval_seconds, 'corpus_fingerprint': result.get('corpus_fingerprint'),
           'baseline': result['matches'], 'draft': None, 'verdict': None, 'accepted': False,
           'status': 'insufficient_evidence', 'reason': result.get('fallback_reason'),
           'generation_seconds': 0.0, 'verification_seconds': 0.0, 'inference_calls': 0, 'sources': {},
           'relevance': relevance}
    if not result['matches']:
        return row
    sources = evidence_sources(result['matches'])
    row['sources'] = sources
    data = {'question': question, 'evidence': sources}
    started = time.perf_counter()
    raw, usage = await runtime.complete(SYSTEM, data, Draft.model_json_schema(), 1024)
    
    # Debugging line to check the raw draft output
    row.update(draft=raw, generation_usage=usage, inference_calls=1,
               generation_seconds=time.perf_counter() - started)
    
    print("Raw draft generated:", row['draft']) 
    try:
        draft = validate_draft(raw, sources)
    except ValueError:
        row['reason'] = 'invalid_draft'
        return row
    started = time.perf_counter()
    verdict, usage = await runtime.complete(VERIFY, {**data, 'draft': raw}, Verdict.model_json_schema(), 512)
    row.update(verdict=verdict, verification_usage=usage, inference_calls=2,
               verification_seconds=time.perf_counter() - started)
    print("Raw verdict generated:", row['verdict'])
    
    try:
        passed = accepted(draft, verdict)
    except ValueError:
        passed = False
    row.update(accepted=passed, status=draft.status if passed else 'insufficient_evidence',
               reason=None if passed else 'verification_failed')
    return row
