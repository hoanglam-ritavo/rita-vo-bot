"""Deterministic source-bound templates; no language-model inference or routing."""
import hashlib
import re
import time
from src.retrieval.semantics import matching_concepts, phrase_matches
from src.retrieval.reranking import query_variants

FIELDS = ('content', 'title', 'applicability', 'effective_from', 'effective_to', 'version_label')
ORIGINS = ('chunk_id', 'revision_id', 'document_id', 'run_id', 'source_path', 'source_spans')
EMPTY = 'Chưa tìm được bằng chứng phù hợp trong các đoạn được cung cấp. Bạn vui lòng nói rõ nội dung cần tìm.'
EXCERPTS = 'Mình tìm thấy các đoạn có thể liên quan dưới đây. Đây là trích đoạn tham khảo, chưa phải kết luận cho trường hợp của bạn.'


def evidence_sources(matches):
    sources, keys = {}, {}
    for match in matches:
        key = tuple(match.get(name) for name in FIELDS)
        origin = {name: match.get(name) for name in ORIGINS}
        if key in keys:
            sources[keys[key]]['origins'].append(origin)
        else:
            source_id = f'S{len(sources) + 1}'
            keys[key] = source_id
            sources[source_id] = {**{name: match.get(name) for name in FIELDS}, 'origins': [origin]}
    return sources


def checked_relevance(result, *, allow_unassessed=False):
    metadata = result.get('relevance')
    if (not isinstance(metadata, dict) or set(metadata) != {'status', 'warnings'}
            or not isinstance(metadata['status'], str) or not isinstance(metadata['warnings'], list)
            or any(not isinstance(w, str) for w in metadata['warnings'])):
        raise ValueError('relevance_unavailable')
    status, warnings = metadata['status'], list(metadata['warnings'])
    disabled = 'relevance_filter_disabled' in warnings
    valid = (disabled and status == ('unassessed' if result['matches'] else 'no_candidates')
             if allow_unassessed else not disabled and
             (status == 'relevant_candidates' if result['matches'] else
              status in {'no_candidates', 'low_relevance'} or
              (status == 'unassessed' and result.get('fallback_reason') == 'relevance_unavailable')))
    if not valid:
        raise ValueError('relevance_unavailable')
    if 'evidence_completeness_not_assessed' not in warnings:
        warnings.append('evidence_completeness_not_assessed')
    return {'status': status, 'warnings': warnings}


def requested_aspects(question):
    terms = {'amount': ('bao nhiêu tiền', 'mức hỗ trợ', 'mức thưởng', 'bao nhiêu', 'số tiền'),
             'conditions': ('điều kiện', 'áp dụng cho', 'đối tượng'),
             'paperwork': ('giấy tờ', 'hồ sơ', 'giấy gì'),
             'procedure': ('cách', 'thủ tục', 'các bước', 'làm thế nào')}
    return [key for key, phrases in terms.items() if any(phrase_matches(p, question) for p in phrases)] or ['overview']


def matches_reference(ref, match):
    return (match.get('document_id') == ref.document_id and match.get('revision_id') == ref.revision_id
            and match.get('chunk_index') == ref.chunk_index
            and hashlib.sha256(match['content'].encode('utf-8')).hexdigest() == ref.content_sha256
            and ref.quote in match['content']
            and all(match.get(key) == value for key, value in ref.metadata.items()))


def render(question, result, catalog, lexicon, *, allow_unassessed=False):
    relevance = checked_relevance(result, allow_unassessed=allow_unassessed)
    row = {'status': 'insufficient_evidence', 'claims': [], 'sources': {}, 'limitations': [EMPTY],
           'reason': result.get('fallback_reason'), 'relevance': relevance, 'intro': ''}
    if not result['matches']:
        return row
    sources = evidence_sources(result['matches'])
    question = query_variants(question)[-1]
    concepts = matching_concepts(lexicon, question)
    cards = [c for c in catalog.cards
             if (any(phrase_matches(p, question) for p in c.aliases) or bool(set(c.concept_ids) & concepts))
             and (not c.requires_any or any(phrase_matches(p, question) for p in c.requires_any))
             and not any(phrase_matches(p, question) for p in c.excludes_any)]
    complex_request = any(phrase_matches(p, question) for p in (
        'tổng', 'so sánh', 'bao nhiêu mục', 'bao nhiêu loại', 'tôi có được',
        'có mấy', 'quản lý trực tiếp của tôi', 'dự án của tôi',
        'trường hợp của tôi', 'tôi thử việc', 'không sinh con', 'không kết hôn'))
    # An exclusion in the second clause must not hide another requested beneficiary.
    clause_cards = set()
    for clause in re.split(r'\bvà\b|;', question, flags=re.IGNORECASE):
        clause_concepts = matching_concepts(lexicon, clause)
        clause_cards.update(c.id for c in catalog.cards
            if (any(phrase_matches(p, clause) for p in c.aliases)
                or bool(set(c.concept_ids) & clause_concepts))
            and (not c.requires_any or any(phrase_matches(p, clause) for p in c.requires_any))
            and not any(phrase_matches(p, clause) for p in c.excludes_any))
    if len(cards) > 1 or len(clause_cards) > 1 or complex_request:
        row.update(status='needs_clarification', reason='needs_clarification', limitations=[
            'Bạn muốn hỏi riêng nội dung nào? Vui lòng nêu rõ quyền lợi hoặc thủ tục và đối tượng cần tìm; mình chưa thể kết luận điều kiện cá nhân hay tính toán tổng hợp.'])
        return row
    if len(cards) == 1:
        card = cards[0]
        aspects = requested_aspects(question)
        claims, complete = [], True
        for aspect in aspects:
            section = card.sections.get(aspect)
            if section is None:
                complete = False
                continue
            complete = complete and section.complete
            for unit in section.units:
                citations = []
                for ref in unit.refs:
                    match = next((m for m in result['matches'] if matches_reference(ref, m)), None)
                    if match is None:
                        break
                    source_id = next(key for key, source in sources.items()
                                     if any(origin['chunk_id'] == match['chunk_id'] for origin in source['origins']))
                    citations.append({'source_id': source_id, 'quote': ref.quote})
                else:
                    claim = {'text': unit.text, 'citations': citations}
                    if claim not in claims:
                        claims.append(claim)
                    continue
                complete = False
        if claims:
            if len(claims) > 12:
                complete = False
            row.update(status='answered' if complete else 'partial', claims=claims[:12], sources=sources,
                       reason=None if complete else 'incomplete_template_evidence', limitations=[] if complete else [
                           'Mình mới xác định được phần dưới đây từ các đoạn được cung cấp; chưa đủ thông tin cho toàn bộ yêu cầu.'])
            return row
    claims = [{'text': source['content'], 'citations': [{'source_id': key, 'quote': source['content']}]}
              for key, source in sources.items() if 0 < len(source['content']) <= 2000][:12]
    if claims:
        row.update(status='partial', claims=claims, sources=sources, intro=EXCERPTS,
                   reason='template_unavailable' if not cards else 'template_evidence_missing', limitations=[
                       'Chưa đủ cơ sở để tổng hợp thành câu trả lời theo mẫu. Bạn có thể nêu rõ quyền lợi, điều kiện, hồ sơ hoặc thao tác cần hỏi.'])
        if len(claims) < len(sources):
            row['limitations'].append('Chỉ hiển thị các trích đoạn nguyên vẹn trong giới hạn trả lời; không khẳng định đây là toàn bộ thông tin.')
    return row


async def evaluate_question(question, retrieval, catalog, lexicon, *, top_k=5, allow_unassessed=False):
    started = time.perf_counter()
    result = await retrieval.retrieve(question, top_k=top_k)
    elapsed = time.perf_counter() - started
    if result['status'] == 'model_mismatch':
        raise ValueError('model_mismatch')
    # An empty filtered fallback remains empty and never reads catalog facts into the answer.
    row = render(question, result, catalog, lexicon, allow_unassessed=allow_unassessed)
    return {**row, 'retrieval_seconds': elapsed, 'generation_seconds': 0.0, 'verification_seconds': 0.0}
