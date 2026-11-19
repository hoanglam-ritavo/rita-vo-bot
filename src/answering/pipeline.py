"""Deterministic source-bound templates; no language-model inference or routing."""
import hashlib
import re
import time
import logging
from src.retrieval.semantics import matching_concepts, phrase_matches
from src.retrieval.reranking import query_variants

FIELDS = ('content', 'title', 'applicability', 'effective_from', 'effective_to', 'version_label')
ORIGINS = ('chunk_id', 'revision_id', 'document_id', 'run_id', 'source_path', 'source_spans')
EMPTY = 'Chưa tìm được bằng chứng phù hợp trong các đoạn được cung cấp. Bạn vui lòng nói rõ nội dung cần tìm.'
EXCERPTS = 'Mình tìm thấy các đoạn có thể liên quan dưới đây. Đây là trích đoạn tham khảo, chưa phải kết luận cho trường hợp của bạn.'
logger = logging.getLogger(__name__)


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


def checked_relevance(result):
    metadata = result.get('relevance')
    if (not isinstance(metadata, dict) or metadata.get('status') not in ('unassessed', 'no_candidates')
            or not isinstance(metadata.get('warnings'), list)
            or any(not isinstance(w, str) for w in metadata['warnings'])):
        raise ValueError('invalid_evidence_metadata')
    warnings = list(dict.fromkeys([*metadata['warnings'], 'evidence_completeness_not_assessed']))
    return {'status': metadata['status'], 'warnings': warnings}


def requested_aspects(question):
    from src.retrieval.language import positive_question
    from src.type_b.tasks import normalized, phrase
    query = normalized(positive_question(question))
    duration = bool(re.search(
        r'\b(?:bao lau|bao nhieu (?:ngay|buoi|tuan|thang|nam|gio|phut)|'
        r'(?:thoi gian|thoi han|thoi luong)\b.*\bbao nhieu)\b', query))
    terms = {'amount': ('bao nhiêu tiền', 'mức hỗ trợ', 'mức thưởng', 'số tiền'),
             'conditions': ('điều kiện', 'áp dụng cho', 'đối tượng'),
             'paperwork': ('giấy tờ', 'hồ sơ', 'giấy gì'),
             'procedure': ('cách', 'thủ tục', 'các bước', 'làm thế nào', 'cần làm gì', 'phải làm gì')}
    terms['procedure'] += ('làm sao',)
    if not duration:
        terms['amount'] += ('bao nhiêu',)
    aspects = [key for key, phrases in terms.items() if any(phrase(query, normalized(p)) for p in phrases)]
    if duration and 'amount' in aspects:
        aspects.append('overview')
    return aspects or (['procedure'] if any(phrase(query, p) for p in ('quy trinh', 'dang ky')) else ['overview'])


def matches_reference(ref, match):
    return (match.get('document_id') == ref.document_id and match.get('revision_id') == ref.revision_id
            and match.get('source_sha256') == ref.source_sha256
            and match.get('chunk_index') == ref.chunk_index
            and hashlib.sha256(match['content'].encode('utf-8')).hexdigest() == ref.content_sha256
            and ref.quote in match['content']
            and all(match.get(key) == value for key, value in ref.metadata.items()))


def select_cards(question, catalog, lexicon, policy=None, *, contextual=False):
    from src.retrieval.language import positive_question
    question = positive_question(question)
    question = query_variants(question)[-1]
    concepts = matching_concepts(lexicon, question)
    cards = [c for c in catalog.cards
             if (any(phrase_matches(p, question) for p in c.aliases) or bool(set(c.concept_ids) & concepts))
             and (not c.requires_any or any(phrase_matches(p, question) for p in c.requires_any))
             and not any(phrase_matches(p, question) for p in c.excludes_any)]
    complex_terms = ('tổng', 'so sánh', 'bao nhiêu mục', 'bao nhiêu loại', 'có mấy')
    if not contextual:
        complex_terms += ('tôi có được', 'quản lý trực tiếp của tôi', 'dự án của tôi',
                          'trường hợp của tôi', 'tôi thử việc', 'không sinh con', 'không kết hôn')
    complex_request = any(phrase_matches(p, question) for p in complex_terms)
    # Negation outside an explicitly authored negative task must not turn a
    # positive policy/procedure into an answer about the opposite situation.
    negative = any(phrase_matches(p, question) for p in ('không', 'chưa', 'ngoại trừ'))
    if negative and cards and not any(
            phrase_matches(alias, question) and any(phrase_matches(p, alias) for p in ('không', 'chưa', 'ngoại trừ'))
            for card in cards for alias in card.aliases):
        complex_request = True
    # An exclusion in the second clause must not hide another requested beneficiary.
    clause_cards = set()
    clauses = re.split(r'\bvà\b|;', question, flags=re.IGNORECASE)
    for clause in clauses if len(clauses) > 1 else []:
        clause_concepts = matching_concepts(lexicon, clause)
        clause_cards.update(c.id for c in catalog.cards
            if (any(phrase_matches(p, clause) for p in c.aliases)
                or bool(set(c.concept_ids) & clause_concepts))
            and (not c.requires_any or any(phrase_matches(p, clause) for p in c.requires_any))
            and not any(phrase_matches(p, clause) for p in c.excludes_any))
    if policy is not None:
        resolution = resolve_policy(question, catalog, lexicon, policy)
        if resolution is not None and resolution.state == 'task':
            return [c for c in catalog.cards if c.id == resolution.task_id], complex_request
        if resolution is not None and resolution.state in ('clarify','unsupported'):
            candidates = getattr(resolution, 'candidates', ())
            return [c for c in catalog.cards if c.id in candidates], resolution.state == 'clarify'
    return cards, len(cards) > 1 or len(clause_cards) > 1 or complex_request


def resolve_policy(question, catalog, lexicon, policy):
    resolution = policy.resolve(question)
    if resolution.state in ('clarify','unsupported') and not policy.analyze(question).ambiguous:
        # A known narrow task (e.g. E-Leave mandatory fields) does not need
        # registration-only slots. Preserve that existing explicit contract.
        cards, ambiguous = select_cards(question, catalog, lexicon)
        routed = {r.task_id for r in policy.catalog.routes} if policy.catalog is not None else set()
        if len(cards)==1 and not ambiguous and cards[0].id not in routed:
            return None
    return resolution


def is_document_lookup(question):
    from src.type_b.tasks import normalized, phrase
    if any(phrase_matches(p, question) for p in (
            'tìm tài liệu', 'tra cứu tài liệu', 'tìm văn bản', 'trích đoạn tài liệu', 'tìm trong tài liệu')):
        return True
    query = normalized(question)
    if any(phrase(query, term) for term in ('cua toi', 'cua minh', 'cua dong nghiep', 'cua nguoi khac')):
        return False
    return bool(re.match(r'^(?:(?:cho (?:toi|minh)|vui long|xin|(?:toi|minh) muon) )?'
                         r'(?:xem|doc|mo) tai lieu ', query))


def section_units(section, analysis):
    if section is None:
        return ()
    if analysis.focus_unit is None:
        return section.units
    return [unit for unit in section.units if unit.id == analysis.focus_unit]


def render(question, result, catalog, lexicon, policy=None, *, contextual=False, analysis=None):
    if analysis is None:
        from .planning import analyze_question
        analysis = analyze_question(question, catalog, lexicon, policy)
    relevance = checked_relevance(result)
    row = {'status': 'insufficient_evidence', 'claims': [], 'sources': {}, 'limitations': [EMPTY],
           'reason': result.get('fallback_reason'), 'relevance': relevance, 'intro': ''}
    if analysis.unsupported_person or analysis.clarification:
        row.update(status='unsupported' if analysis.unsupported_person else 'needs_clarification',
            reason='other_employee_unsupported' if analysis.unsupported_person else analysis.clarification_reason,
            limitations=['Chỉ sử dụng hồ sơ của người đang đăng nhập.' if analysis.unsupported_person else analysis.clarification])
        return row
    if analysis.document_message:
        row.update(reason=analysis.document_reason, limitations=[analysis.document_message])
        return row
    if analysis.document_lookup:
        sources = evidence_sources(result['matches'])
        claims = [{'text': source['content'], 'citations': [{'source_id': key, 'quote': source['content']}]}
                  for key, source in sources.items() if 0 < len(source['content']) <= 2000][:12]
        row.update(status='partial' if claims else 'insufficient_evidence', claims=claims, sources=sources,
                   intro=EXCERPTS if claims else '', reason='document_lookup', limitations=[
                       'Các trích đoạn chưa được xác nhận về độ liên quan hoặc tính đầy đủ.'])
        return row
    cards, ambiguous = analysis.cards, analysis.ambiguous
    if ambiguous:
        row.update(status='needs_clarification', reason='needs_clarification', limitations=[
            'Bạn muốn hỏi riêng nội dung nào? Vui lòng nêu rõ chủ đề, đối tượng và thông tin cần tìm.'])
        return row
    if not cards:
        logger.info('document_answer state=not_requested reason=task_unavailable')
        row.update(reason='task_unavailable', limitations=[
            'Mình chưa xác định được tác vụ phù hợp. Bạn hãy nêu rõ chủ đề và thông tin cần hỏi; '
            'nếu muốn tìm nguồn, hãy yêu cầu tìm tài liệu.'])
        return row
    sources = evidence_sources(result['matches'])
    if len(cards) == 1:
        card = cards[0]
        aspects = analysis.aspects
        claims, complete = [], True
        limitations, reasons = [], set()
        for aspect in aspects:
            section = card.sections.get(aspect)
            if section is None:
                logger.info('document_answer state=incomplete reason=section_missing')
                complete = False
                reasons.add('section_missing')
                limitations.append('Chưa có hướng dẫn có nguồn cho phần thông tin bạn yêu cầu.')
                continue
            complete = complete and section.complete
            if not section.complete:
                reasons.add('section_incomplete')
                limitations.extend(section.limitations or [
                    'Nguồn hiện có chỉ hỗ trợ một phần nội dung này; chưa đủ để hướng dẫn toàn bộ yêu cầu.'])
            for unit in section_units(section, analysis):
                citations = []
                for ref in unit.refs:
                    match = next((m for m in result['matches'] if matches_reference(ref, m)), None)
                    if match is None:
                        logger.info('document_answer state=incomplete reason=reference_unavailable')
                        reasons.add('reference_unavailable')
                        limitations.append('Một phần căn cứ hiện hành không còn khả dụng; chưa thể xác nhận đầy đủ hướng dẫn.')
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
                reasons.add('output_bounded')
            if not complete and len(claims) > 3:
                reasons.add('output_bounded')
                limitations.append('Lượt này chỉ hiển thị một phần nội dung; bạn có thể hỏi riêng từng phần để xem tiếp.')
            # Partial broad topics stay concise; every displayed unit retains
            # its own conditions and citations. Never shorten a complete result
            # while continuing to label it complete.
            logger.info('document_answer complete=%s section_missing=%s section_incomplete=%s reference_unavailable=%s bounded=%s',
                        complete, 'section_missing' in reasons, 'section_incomplete' in reasons,
                        'reference_unavailable' in reasons, 'output_bounded' in reasons)
            row.update(status='answered' if complete else 'partial', claims=claims[:12 if complete else 3], sources=sources,
                       reason=None if complete else 'incomplete_template_evidence',
                       limitations=[] if complete else list(dict.fromkeys(limitations)))
            return row
    if contextual and len(cards) == 1:
        aspect_labels = {'paperwork': 'hồ sơ, giấy tờ', 'conditions': 'điều kiện',
                         'amount': 'mức hưởng hoặc hỗ trợ', 'procedure': 'thủ tục', 'overview': 'tổng quan'}
        requested = ', '.join(aspect_labels[aspect] for aspect in analysis.aspects)
        message = f'Mình hiểu bạn đang hỏi về {requested} của chủ đề “{cards[0].label}”.'
        if all(aspect not in cards[0].sections for aspect in analysis.aspects):
            available = ', '.join(aspect_labels[aspect] for aspect in cards[0].sections)
            row.update(reason='answer_section_unavailable', limitations=[
                f'{message} Tài liệu đã duyệt hiện chưa đủ để hướng dẫn phần này. '
                f'Bạn có thể hỏi về: {available}.'])
        else:
            row.update(reason='answer_reference_unavailable', limitations=[
                f'{message} Căn cứ đã khai báo cho phần này hiện không khả dụng.'])
        return row
    row.update(reason='template_evidence_missing', limitations=[cards[0].clarification,
        'Chưa đủ bằng chứng hiện hành để trả lời theo mẫu.'])
    return row


async def evidence_result(question, retrieval, catalog, lexicon, top_k=5, *, contextual=False, analysis=None):
    from src.retrieval.service import validate_request
    from src.retrieval.reranking import relevance
    question = validate_request(question, top_k)
    if analysis is None:
        from .planning import analyze_question
        analysis = analyze_question(question, catalog, lexicon, getattr(retrieval, 'policy', None))
    if analysis.clarification or analysis.unsupported_person or analysis.document_message:
        return {'matches': [], 'fallback_reason': None, 'relevance': relevance([])}
    if analysis.document_lookup:
        logger.info('document_evidence state=requested mode=lookup')
        result = await retrieval.retrieve(analysis.document_question or question, top_k=top_k)
        if result['status'] == 'model_mismatch':
            raise ValueError('model_mismatch')
        return result
    cards, ambiguous = analysis.cards, analysis.ambiguous
    if ambiguous:
        logger.info('document_evidence state=not_requested reason=ambiguous_request')
        return {'matches': [], 'fallback_reason': None, 'relevance': relevance([])}
    aspects = analysis.aspects
    refs = [ref for card in cards for aspect in aspects
            for unit in section_units(card.sections.get(aspect), analysis) for ref in unit.refs]
    if not refs:
        logger.info('document_evidence state=not_requested reason=%s', 'task_unavailable' if not cards else 'section_missing')
        return {'matches': [], 'fallback_reason': None, 'relevance': relevance([])}
    logger.info('document_evidence state=requested mode=references')
    return await retrieval.read_evidence(refs)


async def evaluate_question(question, retrieval, catalog, lexicon, *, top_k=5, analysis=None):
    started = time.perf_counter()
    if analysis is None:
        from .planning import analyze_question
        analysis = analyze_question(question, catalog, lexicon, getattr(retrieval, 'policy', None))
    result = await evidence_result(question, retrieval, catalog, lexicon, top_k, analysis=analysis)
    row = render(question, result, catalog, lexicon, getattr(retrieval, 'policy', None), analysis=analysis)
    return {**row, 'retrieval_seconds': time.perf_counter() - started,
            'generation_seconds': 0.0, 'verification_seconds': 0.0}
