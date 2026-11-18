"""Structured conversation continuations; user text is never evidence or identity."""
from dataclasses import replace
import hashlib
import json
import logging
import re

from src.answering.planning import analyze_question
from src.answering.slots import parse_inputs
from src.type_b.tasks import TASKS, normalized

SCHEMA = 6
logger = logging.getLogger(__name__)
ASPECTS = {
    'paperwork': ('ho so', 'giay to', 'can giay to gi', 'can ho so gi', 'ho so gom nhung gi'),
    'amount': ('muc ho tro', 'muc thuong', 'bao nhieu tien', 'muc ho tro bao nhieu'),
    'conditions': ('dieu kien', 'dieu kien ap dung', 'ap dung cho ai'),
    'procedure': ('thu tuc', 'quy trinh', 'cac buoc', 'lam the nao', 'can lam gi', 'phai lam gi'),
    'overview': ('tong quan',),
}


def identity(catalog, lexicon, policy=None, *, schema=SCHEMA):
    if catalog is None or lexicon is None:
        return 'unavailable'
    return hashlib.sha256(json.dumps([schema, catalog.model_dump(), lexicon,
        getattr(policy, 'fingerprint', None)], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def analyze_continuation(text, policy):
    """Recognize bounded updates/aspects; unconsumed content is a new request.

    Registry topics and platform/type phrases use the same recognizer as routing.
    Discourse words alone never authorize a continuation or an employee read.
    """
    query = normalized(text)
    aspect = next((key for key, aliases in ASPECTS.items()
                   if any(re.search(r'(?<!\w)' + re.escape(alias) + r'(?!\w)', query) for alias in aliases)), None)
    updates, conflict = {}, False
    remaining = text
    family = None
    if policy is not None and policy.catalog is not None:
        from src.retrieval.policy import folded, rule_matches, DURATION, duration, spans
        remaining = list(folded(text))
        hits = {name: rule_matches(''.join(remaining), getattr(policy.catalog, name), policy.concepts)
                for name in ('families', 'subtypes', 'systems')}
        families = {row[2] for row in hits['families']}
        family = next(iter(families)) if len(families) == 1 else None
        if len(families) > 1:
            return None
        for name, rows in hits.items():
            ids = {row[2] for row in rows}
            if name != 'families':
                conflict |= len(ids) > 1
                if len(ids) == 1:
                    updates['system' if name == 'systems' else 'subtype'] = next(iter(ids))
            for start, end, _ in rows:
                remaining[start:end] = ' ' * (end-start)
        folded_text = folded(text)
        days, sessions, granularity, bad_duration = duration(folded_text)
        conflict |= bad_duration
        if days is not None or sessions is not None:
            updates.update(days=days, sessions=sessions, granularity=granularity)
        for match in DURATION.finditer(folded_text):
            remaining[match.start():match.end()] = ' ' * (match.end()-match.start())
        for rule in policy.catalog.families:
            for term in rule.platform_terms:
                for start, end in spans(folded_text, term):
                    remaining[start:end] = ' ' * (end-start)
        remaining = ''.join(remaining)
    words = set(normalized(remaining).split())
    allowed = {'tren', 'toi', 'minh', 'dung', 'dang', 'hien', 'tai', 'su', 'la', 'va',
        'loai', 'he', 'thong', 'nghi', 'muon', 'xin', 'chon', 'nhe', 'nha', 'a', 'vay',
        'thi', 'sao', 'con', 've', 'them', 'doi', 'thanh', 'lai', 'huong', 'dan', 'lam',
        'thu', 'tuc', 'o', 'dau', 'nop', 'gui', 'don', 'can', 'gi', 'tiep', 'the', 'nao',
        'quy', 'trinh', 'cac', 'buoc', 'hoac', 'ho', 'so', 'giay', 'to', 'gom', 'nhung', 'dieu',
        'kien', 'ap', 'cho', 'ai', 'muc', 'tro', 'thuong', 'bao', 'nhieu', 'tien', 'tong', 'quan'}
    if not words <= allowed:
        return None
    submission = any(term in query for term in ('o dau', 'nop o', 'gui don', 'huong dan tren'))
    if submission or 'huong dan' in query or 'lam gi tiep' in query:
        aspect = 'procedure'
    if not (updates or aspect or conflict):
        return None
    return dict(slots=updates, aspect=aspect, submission=submission,
                conflict=conflict, family=family, add=query.startswith('them '))


def slot_update(text, policy):
    """Compatibility reader for bounded registry slots."""
    if policy is None or policy.catalog is None:
        return None
    update = analyze_continuation(text, policy)
    return update['slots'] if update and not update['conflict'] else None


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
    if previous.get('schema') == 5 and previous.get('identity') == identity(catalog, lexicon, policy, schema=5):
        # Only identical catalog/lexicon/policy bindings can migrate without replay.
        compatible = isinstance(previous.get('request'), (dict, type(None)))
    old = previous if compatible else {}
    recent = old.get('recent', [])[-3:] + [text]
    query = normalized(text)
    request = dict(question=text, slots={}, user_inputs=parse_inputs(text))
    prior = old.get('request')
    continuation = False
    turn_kind = 'new_request'
    conflict = False
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
        turn_kind = 'selection'
    elif prior:
        inputs = parse_inputs(text)
        update = analyze_continuation(text, policy)
        root_family = policy.analyze(prior['question']).family if policy is not None and policy.catalog is not None else None
        if update and update['family'] and update['family'] != root_family:
            update = None
        sources = {ref.document_id for card in candidates for section in getattr(card, 'sections', {}).values()
                   for unit in section.units for ref in unit.refs}
        if text.strip().lower() in sources:
            request = {**prior, 'source_id': text.strip().lower()}
            continuation = True
            turn_kind = 'selection'
        elif inputs and set(inputs) <= (set(old.get('pending_inputs', [])) | set(prior.get('user_inputs', {}))):
            request = {**prior, 'user_inputs': {**prior.get('user_inputs', {}), **inputs}}
            continuation = True
            turn_kind = 'input_update'
        elif update and (update['slots'] and root_family or update['aspect'] and old.get('task_id') or update['submission'] and root_family or update['conflict']):
            request = {**prior, 'slots': {**prior.get('slots', {}), **update['slots']}}
            conflict = update['conflict']
            aspect = update['aspect']
            if aspect:
                request['aspects'] = list(dict.fromkeys([*prior.get('aspects', []), aspect])) if update['add'] else [aspect]
            if update['submission']:
                request['slots']['intent'] = 'submission'
            elif aspect == 'procedure' and request['slots'].get('system'):
                request['slots']['intent'] = 'procedure'
            elif aspect in ('conditions', 'overview'):
                request['slots']['intent'] = 'policy'
            if root_family and policy.structured_family(root_family) and (update['slots'] or update['submission'] or aspect):
                request.pop('selected_task', None)
            elif old.get('task_id'):
                request['selected_task'] = old['task_id']
            continuation = True
            turn_kind = 'correction' if any(word in query.split() for word in ('doi', 'thanh')) else 'followup' if aspect else 'slot_update'
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
    if conflict:
        analysis = replace(analysis, clarification='Bạn đang đưa ra nhiều giá trị cho cùng thông tin. Bạn muốn chọn giá trị nào?',
                           clarification_reason='task_needs_clarification')
        request = dict(prior)
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
    if policy is not None and policy.catalog is not None and not analysis.unsupported_person:
        root = policy.analyze(request['question'])
        slots = {key: value for key, value in vars(root).items()
                 if key in ('subtype', 'system', 'days', 'sessions', 'granularity') and value is not None}
        request['slots'] = {**slots, **request.get('slots', {})}
    state = _state(analysis, request, recent, stamp)
    state['turn_kind'] = turn_kind
    if conflict:
        state['pending'] = 'request'
    logger.info('conversation_plan kind=%s inherited=%s conflict=%s route_changed=%s',
                turn_kind, continuation, conflict, old.get('task_id') != state['task_id'])
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
