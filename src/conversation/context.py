"""Structured conversation continuations; user text is never evidence or identity."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import logging

from src.answering.planning import analyze_question
from src.answering.slots import SLOT_LABELS, parse_inputs
from src.type_b.tasks import TASKS, normalized
from .continuation import ASPECTS, recognize, input_update

SCHEMA = 7
MAX_CONTEXT_BYTES = 48 * 1024
logger = logging.getLogger(__name__)


def valid_context(previous):
    """Reject incompatible shapes atomically; never salvage selected fields."""
    if not isinstance(previous, dict):
        return False
    try:
        if len(json.dumps(previous, ensure_ascii=False, allow_nan=False).encode()) > MAX_CONTEXT_BYTES:
            return False
    except (ValueError, TypeError, RecursionError):
        return False
    request = previous.get('request')
    if request is not None:
        if not isinstance(request, dict) or not isinstance(request.get('question'), str) or not 1 <= len(request['question']) <= 2000:
            return False
        if set(request) - {'question', 'slots', 'user_inputs', 'aspects', 'selected_task', 'source_id', 'self_reference'}:
            return False
        for key in ('selected_task', 'source_id'):
            if key in request and (not isinstance(request[key], str) or not 1 <= len(request[key]) <= 100):
                return False
        if 'self_reference' in request and type(request['self_reference']) is not bool:
            return False
        slots = request.get('slots', {})
        if not isinstance(slots, dict) or set(slots) - {'system', 'subtype', 'days', 'sessions', 'granularity', 'intent'}:
            return False
        for key, value in slots.items():
            if key in ('days', 'sessions'):
                if value is not None and (type(value) not in (int, float) or not 0 < value <= 366):
                    return False
            elif value is not None and (not isinstance(value, str) or not 1 <= len(value) <= 100):
                return False
        if slots.get('days') is not None and slots.get('sessions') is not None:
            return False
        if slots.get('granularity') not in (None, 'day', 'session') or slots.get('intent') not in (None, 'policy', 'submission', 'procedure'):
            return False
        inputs = request.get('user_inputs', {})
        if not isinstance(inputs, dict) or set(inputs) - SLOT_LABELS.keys() or any(
                not isinstance(v, str) or not 1 <= len(v) <= 240 for v in inputs.values()):
            return False
        aspects = request.get('aspects', [])
        if not isinstance(aspects, list) or len(aspects) > len(ASPECTS) or any(
                not isinstance(a, str) or a not in ASPECTS for a in aspects):
            return False
    for key, limit in (('recent', 4), ('candidates', 5), ('pending_inputs', 32),
                       ('pending_prompts', 32), ('missing_slots', 32), ('requested_aspects', 5)):
        value = previous.get(key, [])
        if not isinstance(value, list) or len(value) > limit or any(not isinstance(v, str) or len(v) > 2000 for v in value):
            return False
    for key in ('task_id', 'input_task_id', 'pending'):
        if previous.get(key) is not None and not isinstance(previous[key], str):
            return False
    if set(previous.get('pending_inputs', [])) - SLOT_LABELS.keys():
        return False
    if request is None and (previous.get('pending') or previous.get('task_id') or previous.get('candidates')
                            or previous.get('pending_inputs') or previous.get('profile_self')):
        return False
    if any(type(previous.get(key, False)) is not bool for key in ('profile_self', 'self_reference')):
        return False
    return True


def identity(catalog, lexicon, policy=None, *, schema=SCHEMA):
    if catalog is None or lexicon is None:
        return 'unavailable'
    return hashlib.sha256(json.dumps([schema, catalog.model_dump(), lexicon,
        getattr(policy, 'fingerprint', None)], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _state(analysis, request, recent, stamp):
    ready = not analysis.clarification and not analysis.unsupported_person
    task = analysis.cards[0].id if ready and len(analysis.cards) == 1 and not analysis.ambiguous else None
    choosing_task = analysis.ambiguous and analysis.cards and not analysis.unresolved and not analysis.unsupported_person
    pending = ('slots' if analysis.missing_slots else 'task' if choosing_task else
               'request' if analysis.clarification else 'unsupported' if analysis.document_message else None)
    return dict(schema=SCHEMA, identity=stamp, request=request, task_id=task,
        candidates=[card.id for card in analysis.cards][:5] if pending == 'task' else [],
        requested_aspects=list(analysis.aspects), pending=pending,
        pending_question=analysis.question if pending else None,
        task_question=analysis.question if task else None,
        missing_slots=list(analysis.missing_slots), recent=recent,
        self_reference=bool(analysis.direct_fields),
        user_inputs={key: {'value': value, 'origin': 'user_input'} for key, value in analysis.user_inputs})


def plan_turn(text, previous, catalog, lexicon, *, identity_stamp=None, policy=None):
    stamp = identity_stamp if identity_stamp is not None else identity(catalog, lexicon, policy)
    compatible = (valid_context(previous) and previous.get('schema') == SCHEMA
                  and previous.get('identity') == stamp)
    old = deepcopy(previous) if compatible else {}
    recent = old.get('recent', [])[-3:] + [text]
    query = normalized(text)
    request = dict(question=text, slots={}, user_inputs=parse_inputs(text))
    prior = old.get('request')
    continuation, turn_kind, issue = False, 'new_request', None
    if query in ('huy cau hoi', 'huy yeu cau', 'bo qua cau hoi nay', 'khong hoi nua'):
        analysis = replace(analyze_question(text, catalog, lexicon, policy),
            clarification='Đã bỏ yêu cầu đang chờ. Bạn muốn hỏi nội dung nào tiếp theo?')
        state = _state(analysis, request, recent, stamp)
        state.update(pending=None, request=None, turn_kind='cancel')
        return analysis, state
    by_id = {card.id: card for card in catalog.cards} if catalog is not None else {}
    candidates = [by_id[key] for key in old.get('candidates', []) if key in by_id]
    chosen = [card for index, card in enumerate(candidates, 1) if query in {
        str(index), normalized(card.label), normalized(card.id), *(normalized(alias) for alias in card.aliases)}]
    active_task = old.get('task_id') or old.get('input_task_id')
    if prior and len(chosen) == 1:
        request = {**prior, 'selected_task': chosen[0].id}
        if active_task != chosen[0].id:
            request.pop('source_id', None)
        continuation, turn_kind = True, 'selection'
    elif prior:
        update = recognize(text, policy)
        inputs = input_update(text, old.get('pending_inputs', []), prior.get('user_inputs', {}))
        root_family = policy.analyze(prior['question']).family if policy is not None and policy.catalog is not None else None
        if update and update.family and update.family != root_family:
            update = None
        if update and {'days', 'sessions', 'subtype'} & update.slots.keys() and not (
                root_family and policy.structured_family(root_family)):
            update = None
        sources = {ref.document_id for card in candidates for section in getattr(card, 'sections', {}).values()
                   for unit in section.units for ref in unit.refs}
        if text.strip().lower() in sources:
            request = {**prior, 'source_id': text.strip().lower()}
            continuation, turn_kind = True, 'selection'
        elif inputs.values:
            request = {**prior, 'user_inputs': {**prior.get('user_inputs', {}), **inputs.values}}
            continuation, turn_kind = True, 'input_update'
        elif update and (update.slots and root_family or update.aspects and active_task or update.conflict and root_family):
            request = deepcopy(prior)
            slots = request.setdefault('slots', {})
            changed = any(slots.get(key) != value for key, value in update.slots.items())
            slots.update(update.slots)
            if update.aspects:
                request['aspects'] = list(dict.fromkeys([*prior.get('aspects', []), *update.aspects])) if update.add else list(update.aspects)
            if update.submission and root_family:
                slots['intent'] = 'submission'
            elif 'procedure' in update.aspects and update.slots.get('system'):
                slots['intent'] = 'procedure'
            route_changed = changed or slots.get('intent') != prior.get('slots', {}).get('intent')
            if route_changed:
                request.pop('selected_task', None)
                request.pop('source_id', None)
            elif active_task:
                request['selected_task'] = active_task
            continuation = True
            turn_kind = 'correction' if update.correction else 'followup' if update.aspects else 'slot_update'
            if update.conflict:
                request = deepcopy(prior)
                issue = 'Mình chưa xác định được một giá trị phù hợp. Bạn hãy nêu rõ một lựa chọn.'
        elif inputs.error:
            issue = inputs.error
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
    independent = bool(analysis.parts or analysis.unsupported_person or analysis.cards
                       or analysis.document_lookup or analysis.missing_slots)
    retained = False
    if not continuation and prior and not independent and (old.get('pending') or old.get('pending_inputs') or issue):
        request, retained, turn_kind = deepcopy(prior), True, 'clarification'
        analysis = analyze_question(request['question'], catalog, lexicon, policy, request=request)
        issue = issue or 'Mình vẫn cần làm rõ yêu cầu trước đó. ' + (
            ' '.join(old.get('pending_prompts', [])) or analysis.clarification or
            'Bạn hãy chọn nội dung hoặc cung cấp thông tin đang được hỏi.')
    elif not continuation and independent:
        issue = None
    # Switching tasks also invalidates inputs embedded in the original question.
    new_task = analysis.cards[0].id if len(analysis.cards) == 1 and not analysis.ambiguous else None
    if continuation and not issue and active_task and new_task != active_task:
        request['user_inputs'] = {}
        analysis = replace(analysis, user_inputs=())
    if issue:
        analysis = replace(analysis, clarification=issue, clarification_reason='task_needs_clarification')
    elif not continuation and not (analysis.parts or analysis.clarification or analysis.unsupported_person):
        analysis = replace(analysis, clarification='Bạn hãy nêu rõ chủ đề, đối tượng và thông tin cần hỏi.')
    if previous and not compatible and not independent:
        analysis = replace(analysis, clarification='Ngữ cảnh hội thoại đã thay đổi hoặc không còn hợp lệ. Bạn hãy nêu lại câu hỏi đầy đủ.',
                           clarification_reason='context_needs_restatement')
    request['aspects'] = list(analysis.aspects)
    if policy is not None and policy.catalog is not None and not analysis.unsupported_person:
        root = policy.analyze(request['question'])
        slots = {key: value for key, value in vars(root).items()
                 if key in ('subtype', 'system', 'days', 'sessions') and value is not None}
        if root.days is not None or root.sessions is not None:
            slots['granularity'] = root.granularity
        request['slots'] = {**slots, **request.get('slots', {})}
    state = _state(analysis, request, recent, stamp)
    state['input_task_id'] = new_task if not issue else active_task
    state['turn_kind'] = turn_kind
    logger.info('conversation_plan kind=%s inherited=%s clarification=%s route_changed=%s',
                turn_kind, continuation, bool(analysis.clarification), old.get('task_id') != state['task_id'])
    if (retained or issue) and old.get('pending_inputs'):
        state.update(pending='inputs', pending_inputs=old['pending_inputs'], pending_prompts=old.get('pending_prompts', []))
    return analysis, state


def reduce_question(text, previous, catalog, lexicon, *, identity_stamp=None, policy=None):
    """Compatibility reader: executing callers must consume plan_turn's plan."""
    analysis, state = plan_turn(text, previous, catalog, lexicon, identity_stamp=identity_stamp, policy=policy)
    return analysis.question, state


def compact(state, result):
    state = {**state, 'recent': list(state['recent'])}
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
    while len(json.dumps(state, ensure_ascii=False).encode()) > MAX_CONTEXT_BYTES and state['recent']:
        state['recent'].pop(0)
    if len(json.dumps(state, ensure_ascii=False).encode()) > MAX_CONTEXT_BYTES:
        # Keep an invalidation marker rather than a partially retained request.
        return {'schema': SCHEMA, 'identity': 'context_budget_exceeded', 'recent': []}
    return state
