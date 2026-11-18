"""Structured conversation continuations; user text is never evidence or identity."""
from dataclasses import replace
import hashlib
import json

from src.answering.planning import analyze_question
from src.answering.slots import parse_inputs
from src.type_b.tasks import TASKS, normalized

SCHEMA = 5
ASPECTS = {
    'paperwork': ('ho so', 'giay to', 'can giay to gi', 'can ho so gi', 'ho so gom nhung gi'),
    'amount': ('muc ho tro', 'muc thuong', 'bao nhieu tien', 'muc ho tro bao nhieu'),
    'conditions': ('dieu kien', 'dieu kien ap dung', 'ap dung cho ai'),
    'procedure': ('thu tuc', 'quy trinh', 'cac buoc', 'lam the nao', 'can lam gi', 'phai lam gi'),
    'overview': ('tong quan',),
}


def identity(catalog, lexicon, policy=None):
    if catalog is None or lexicon is None:
        return 'unavailable'
    return hashlib.sha256(json.dumps([SCHEMA, catalog.model_dump(), lexicon,
        getattr(policy, 'fingerprint', None)], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def slot_update(text, policy):
    """Only named, bounded platform/type replies may update an existing request."""
    if policy is None or policy.catalog is None:
        return None
    from src.retrieval.policy import folded, rule_matches
    query = folded(text)
    hits = {name: rule_matches(query, getattr(policy.catalog, name), policy.concepts)
            for name in ('subtypes', 'systems')}
    remaining = list(query)
    updates = {}
    for name, rows in hits.items():
        ids = {row[2] for row in rows}
        if len(ids) > 1:
            return None
        if ids:
            updates['system' if name == 'systems' else 'subtype'] = next(iter(ids))
        for start, end, _ in rows:
            remaining[start:end] = ' ' * (end-start)
    words = set(normalized(''.join(remaining)).split())
    if not updates or not words <= {'tren', 'toi', 'minh', 'dung', 'dang', 'hien', 'tai',
            'su', 'la', 'va', 'loai', 'he', 'thong', 'nghi', 'muon', 'xin'}:
        return None
    return updates


def _state(analysis, request, recent, stamp):
    ready = not analysis.clarification and not analysis.unsupported_person
    task = analysis.cards[0].id if ready and len(analysis.cards) == 1 and not analysis.ambiguous else None
    pending = ('slots' if analysis.missing_slots else 'task' if analysis.ambiguous else
               'request' if analysis.clarification else 'unsupported' if analysis.document_message else None)
    return dict(schema=SCHEMA, identity=stamp, request=request, task_id=task,
        candidates=[card.id for card in analysis.cards][:5] if analysis.ambiguous else [],
        requested_aspects=list(analysis.aspects), pending=pending,
        pending_question=analysis.question if pending else None,
        task_question=analysis.question if task else None,
        missing_slots=list(analysis.missing_slots), recent=recent,
        self_reference=bool(analysis.direct_fields),
        user_inputs={key: {'value': value, 'origin': 'user_input'} for key, value in analysis.user_inputs})


def plan_turn(text, previous, catalog, lexicon, *, identity_stamp=None, policy=None):
    stamp = identity_stamp if identity_stamp is not None else identity(catalog, lexicon, policy)
    compatible = previous.get('schema') == SCHEMA and previous.get('identity') == stamp
    old = previous if compatible else {}
    recent = old.get('recent', [])[-3:] + [text]
    query = normalized(text)
    request = dict(question=text, slots={}, user_inputs=parse_inputs(text))
    prior = old.get('request')
    continuation = False
    if query in ('huy cau hoi', 'huy yeu cau', 'bo qua cau hoi nay', 'khong hoi nua'):
        analysis = replace(analyze_question(text, catalog, lexicon, policy),
            clarification='Đã bỏ yêu cầu đang chờ. Bạn muốn hỏi nội dung nào tiếp theo?')
        state = _state(analysis, request, recent, stamp)
        state.update(pending=None, request=None)
        return analysis, state
    by_id = {card.id: card for card in catalog.cards} if catalog is not None else {}
    candidates = [by_id[key] for key in old.get('candidates', []) if key in by_id]
    chosen = [card for index, card in enumerate(candidates, 1) if query in {
        str(index), normalized(card.label), normalized(card.id), *(normalized(alias) for alias in card.aliases)}]
    if prior and len(chosen) == 1:
        request = {**prior, 'selected_task': chosen[0].id}
        continuation = True
    elif prior:
        inputs = parse_inputs(text)
        followup = query
        add_aspect = followup.startswith('them ')
        for prefix in ('con ', 've ', 'them '):
            if followup.startswith(prefix):
                followup = followup[len(prefix):]
        for suffix in (' thi sao', ' the nao'):
            if followup.endswith(suffix):
                followup = followup[:-len(suffix)]
        aspect = next((key for key, aliases in ASPECTS.items() if followup in aliases), None)
        updates = slot_update(text, policy)
        sources = {ref.document_id for card in candidates for section in getattr(card, 'sections', {}).values()
                   for unit in section.units for ref in unit.refs}
        if text.strip().lower() in sources:
            request = {**prior, 'source_id': text.strip().lower()}
            continuation = True
        elif inputs and set(inputs) <= (set(old.get('pending_inputs', [])) | set(prior.get('user_inputs', {}))):
            request = {**prior, 'user_inputs': {**prior.get('user_inputs', {}), **inputs}}
            continuation = True
        elif aspect and old.get('task_id'):
            aspects = list(dict.fromkeys([*prior.get('aspects', []), aspect])) if add_aspect else [aspect]
            request = {**prior, 'selected_task': old['task_id'], 'aspects': aspects}
            continuation = True
        elif updates:
            request = {**prior, 'slots': {**prior.get('slots', {}), **updates}}
            request.pop('selected_task', None)
            continuation = True
    profile_query = query
    for prefix in ('con ', 've '):
        if profile_query.startswith(prefix):
            profile_query = profile_query[len(prefix):]
    for suffix in (' thi sao', ' the nao'):
        if profile_query.endswith(suffix):
            profile_query = profile_query[:-len(suffix)]
    if not continuation and old.get('profile_self') and any(profile_query in task.aliases for task in TASKS):
        request['self_reference'] = True
    analysis = analyze_question(request['question'], catalog, lexicon, policy, request=request)
    retained = False
    if not continuation and prior and (old.get('pending') or old.get('pending_inputs')) and not (
            analysis.parts or analysis.unsupported_person):
        request = dict(prior)
        retained = True
        analysis = analyze_question(request['question'], catalog, lexicon, policy, request=request)
        prompts = old.get('pending_prompts', [])
        if not analysis.clarification:
            analysis = replace(analysis, clarification='Mình vẫn cần làm rõ yêu cầu trước đó. ' +
                (' '.join(prompts) if prompts else 'Bạn hãy chọn nội dung hoặc cung cấp thông tin đang được hỏi.'))
    if previous and not compatible and not (analysis.cards or analysis.direct_fields or analysis.document_lookup):
        analysis = replace(analysis, clarification='Ngữ cảnh hội thoại đã thay đổi phiên bản. Bạn hãy nêu lại câu hỏi đầy đủ.',
                           clarification_reason='context_needs_restatement')
    request['aspects'] = list(analysis.aspects)
    state = _state(analysis, request, recent, stamp)
    if retained and old.get('pending_inputs'):
        state.update(pending='inputs', pending_inputs=old['pending_inputs'], pending_prompts=old.get('pending_prompts', []))
    return analysis, state


def reduce_question(text, previous, catalog, lexicon, *, identity_stamp=None, policy=None):
    """Compatibility reader: executing callers must consume plan_turn's plan."""
    analysis, state = plan_turn(text, previous, catalog, lexicon, identity_stamp=identity_stamp, policy=policy)
    return analysis.question, state


def compact(state, result):
    state = dict(state)
    state['outcome'] = result['status']
    state['profile_self'] = state.get('self_reference', False) and result.get('application', {}).get('state') in ('applied', 'partial')
    missing = result.get('application', {}).get('missing_inputs', [])
    if missing or result['status'] != 'needs_clarification':
        state['pending_inputs'] = [item['slot'] for item in missing]
        state['pending_prompts'] = [item['prompt'] for item in missing]
    if missing:
        state['pending'] = 'inputs'
    state['evidence_refs'] = [{key: origin[key] for key in ('document_id', 'revision_id', 'chunk_id') if key in origin}
        for source in result.get('sources', []) for origin in source.get('origins', [])][:60]
    while len(json.dumps(state, ensure_ascii=False).encode()) > 48 * 1024 and state['recent']:
        state['recent'].pop(0)
    return state
