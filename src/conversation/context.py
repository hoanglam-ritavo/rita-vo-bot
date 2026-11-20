"""Structured conversation continuations; user text is never evidence or identity."""
from copy import deepcopy
from dataclasses import replace
import hashlib
import json
import logging
import re

from src.answering.planning import analyze_question, choice_labels, unknown_topic_prompt
from src.answering.slots import SLOT_LABELS, parse_inputs
from src.type_b.tasks import TASKS, normalized
from .continuation import ASPECTS, recognize, input_update, starts_new_request

SCHEMA = 9
LEGACY_SCHEMAS = (7, 8)
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
        if set(request) - {'question', 'slots', 'user_inputs', 'aspects', 'selected_task', 'source_id',
                           'self_reference', 'document_guidance'}:
            return False
        for key in ('selected_task', 'source_id'):
            if key in request and (not isinstance(request[key], str) or not 1 <= len(request[key]) <= 100):
                return False
        if any(type(request[key]) is not bool for key in ('self_reference', 'document_guidance') if key in request):
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
    for key, limit in (('recent', 4), ('candidates', 5), ('scope_task_ids', 5), ('pending_inputs', 32),
                       ('pending_prompts', 32), ('missing_slots', 32), ('requested_aspects', 5)):
        value = previous.get(key, [])
        if not isinstance(value, list) or len(value) > limit or any(not isinstance(v, str) or len(v) > 2000 for v in value):
            return False
    for key in ('task_id', 'input_task_id', 'pending', 'task_binding'):
        if previous.get(key) is not None and not isinstance(previous[key], str):
            return False
    if set(previous.get('pending_inputs', [])) - SLOT_LABELS.keys():
        return False
    if request is None and (previous.get('pending') or previous.get('task_id') or previous.get('candidates')
                            or previous.get('scope_task_ids') or previous.get('pending_inputs')
                            or previous.get('profile_self')):
        return False
    if any(type(previous.get(key, False)) is not bool for key in ('profile_self', 'self_reference')):
        return False
    prompt = previous.get('choice_prompt')
    if prompt is not None:
        if (not isinstance(prompt, dict) or not isinstance(prompt.get('message_id'), str)
                or not isinstance(prompt.get('options'), list) or not 1 <= len(prompt['options']) <= 5
                or previous.get('pending') != 'task'):
            return False
        for index, option in enumerate(prompt['options'], 1):
            if (not isinstance(option, dict) or type(option.get('id')) is not int or option['id'] != index
                    or not isinstance(option.get('label'), str) or not 1 <= len(option['label']) <= 240
                    or not isinstance(option.get('task_id'), str)
                    or option['task_id'] not in previous.get('candidates', [])):
                return False
    claims = previous.get('claim_scopes', [])
    if not isinstance(claims, list) or len(claims) > 12:
        return False
    for scope in claims:
        if scope is not None and (not isinstance(scope, list) or len(scope) != 2
                or not isinstance(scope[0], str) or scope[0] not in ASPECTS
                or not isinstance(scope[1], str) or not 1 <= len(scope[1]) <= 100):
            return False
    return True


def identity(catalog, lexicon, policy=None, *, schema=SCHEMA):
    if catalog is None or lexicon is None:
        return 'unavailable'
    return hashlib.sha256(json.dumps([schema, catalog.model_dump(), lexicon,
        getattr(policy, 'fingerprint', None)], sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _task_binding(card):
    if not hasattr(card, 'model_dump'):
        return None
    return hashlib.sha256(json.dumps(card.model_dump(), sort_keys=True, ensure_ascii=False).encode()).hexdigest()


def _revalidate_task(previous, catalog, lexicon, policy):
    prior = previous.get('request')
    task_id = previous.get('task_id') or previous.get('input_task_id')
    if not prior or not task_id or not previous.get('task_binding') or previous.get('pending') in ('task', 'slots'):
        return None
    request = {key: value for key, value in prior.items() if key != 'selected_task'}
    plan = analyze_question(prior['question'], catalog, lexicon, policy, request=request)
    if plan.unsupported_person or plan.unresolved or (plan.ambiguous and prior.get('selected_task') != task_id):
        return None
    if any(card.id == task_id and _task_binding(card) == previous['task_binding'] for card in plan.cards):
        return {card.id for card in plan.cards}
    return None


def _state(analysis, request, recent, stamp):
    ready = not analysis.clarification and not analysis.unsupported_person
    task = analysis.cards[0].id if ready and len(analysis.cards) == 1 and not analysis.ambiguous else None
    choosing_task = bool(analysis.choice_options) and not analysis.unresolved and not analysis.unsupported_person
    pending = ('slots' if analysis.missing_slots else 'task' if choosing_task else
               'request' if analysis.clarification else 'unsupported' if analysis.document_message else None)
    return dict(schema=SCHEMA, identity=stamp, request=request, task_id=task,
        candidates=[task_id for task_id, _ in analysis.choice_options] if pending == 'task' else [],
        requested_aspects=list(analysis.aspects), pending=pending,
        pending_question=analysis.question if pending else None,
        task_question=analysis.question if task else None,
        missing_slots=list(analysis.missing_slots), recent=recent,
        self_reference=bool(analysis.direct_fields),
        user_inputs={key: {'value': value, 'origin': 'user_input'} for key, value in analysis.user_inputs})


def current_choice(state, choice, text, stamp):
    if (choice is None or not valid_context(state) or state.get('schema') != SCHEMA
            or state.get('identity') != stamp):
        return None
    prompt = state.get('choice_prompt')
    if not prompt or prompt['message_id'] != choice.prompt_message_id:
        return None
    for option in prompt['options']:
        if option['id'] == choice.option_id and option['label'] == text:
            return option['task_id']
    return None


def published_options(analysis, state, message_id, result):
    if state['pending'] != 'task' or result['status'] != 'needs_clarification':
        return []
    options = [{'id': index, 'label': label, 'task_id': task_id}
               for index, (task_id, label) in enumerate(analysis.choice_options, 1)]
    state['choice_prompt'] = {'message_id': message_id, 'options': options}
    return [{'id': item['id'], 'label': item['label']} for item in options]


def _typed_choice(query, candidates, policy, *, allow_number=True):
    choice, separator, tail = query.partition(' hoi them ')
    if not separator:
        choice, separator, tail = query.partition(' toi can ')
    choice = re.sub(r'^(?:(?:toi (?:hoi|chon|muon hoi)(?: ve)?|doi sang|chuyen sang|chon(?: phuong an)?|phuong an|muc so|so) )', '', choice)
    choice = re.sub(r' (?:a|nhe|nha|voi)$', '', choice).strip()
    if not choice:
        return [], ()
    if separator:
        update = recognize(tail, policy)
        if update is None or not update.aspects or update.slots or update.conflict:
            return [], ()
        aspects = update.aspects
    else:
        aspects = ()
    labels = dict(choice_labels(candidates, include_filenames=True))
    return [card for index, card in enumerate(candidates, 1) if choice in {
        *((str(index),) if allow_number else ()), normalized(card.label),
        normalized(labels.get(card.id, '')), *(normalized(alias) for alias in card.aliases)}], aspects


def plan_turn(text, previous, catalog, lexicon, *, identity_stamp=None, legacy_identity_stamps=None,
              policy=None, selected_task=None):
    stamp = identity_stamp if identity_stamp is not None else identity(catalog, lexicon, policy)
    valid = valid_context(previous)
    schema = previous.get('schema') if valid else None
    compatible, revalidated = False, None
    if type(schema) is int and schema in (*LEGACY_SCHEMAS, SCHEMA):
        if schema == SCHEMA:
            expected = stamp
        else:
            expected = (legacy_identity_stamps[schema] if legacy_identity_stamps else
                        identity(catalog, lexicon, policy, schema=schema))
        compatible = previous.get('identity') == expected
        if not compatible:
            revalidated = _revalidate_task(previous, catalog, lexicon, policy)
            compatible = revalidated is not None
    if previous and not compatible:
        logger.info('conversation_context_discard reason=%s', 'incompatible' if valid else 'invalid')
    old = deepcopy(previous) if compatible else {}
    if revalidated is not None:
        old['scope_task_ids'] = [key for key in old.get('scope_task_ids', []) if key in revalidated]
    legacy_prompt = bool(compatible and schema in LEGACY_SCHEMAS and old.get('pending') == 'task')
    if legacy_prompt:
        old.pop('choice_prompt', None)
        old['scope_task_ids'] = old.pop('candidates', [])
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
    chosen, added_aspects = _typed_choice(query, candidates, policy)
    related = [by_id[key] for key in old.get('scope_task_ids', []) if key in by_id]
    if not chosen and selected_task is None and related:
        chosen, added_aspects = _typed_choice(query, related, policy, allow_number=False)
    focus_unit = None
    if selected_task is not None:
        chosen, added_aspects = [card for card in candidates if card.id == selected_task], ()
    active_task = old.get('task_id') or old.get('input_task_id')
    item = re.fullmatch(r'(?:(?:con|ve|giai thich) )?muc (?:thu |so )?(\d+)(?: (?:trong|o|cua) cau tra loi truoc)?(?: thi sao)?', query)
    if prior and len(chosen) == 1:
        request = {**prior, 'selected_task': chosen[0].id}
        if prior.get('document_guidance') and old.get('pending') == 'task':
            request['question'] = chosen[0].label
        if added_aspects:
            request['aspects'] = list(dict.fromkeys([*prior.get('aspects', []), *added_aspects]))
        if active_task != chosen[0].id:
            request.pop('source_id', None)
        continuation, turn_kind = True, 'selection'
    elif prior and related and not candidates and not legacy_prompt and query.isdecimal():
        request = deepcopy(prior)
        continuation, turn_kind = True, 'clarification'
        issue = 'Số thứ tự chỉ dùng khi có danh sách đang chờ. Bạn hãy nêu tên phạm vi muốn đổi sang.'
    elif item and prior:
        position = int(item[1]) if len(item[1]) <= 2 else 0
        claims = old.get('claim_scopes', [])
        request = deepcopy(prior)
        continuation, turn_kind = True, 'answer_reference'
        if (old.get('pending') in ('task', 'slots', 'inputs') or not active_task
                or not 1 <= position <= len(claims) or not claims[position - 1]):
            issue = 'Bạn hãy nêu rõ mục hoặc ý muốn hỏi thêm trong câu trả lời trước.'
        else:
            aspect, focus_unit = claims[position - 1]
            request.update(selected_task=active_task, aspects=[aspect])
    elif prior and (aspect := getattr(policy, 'focused_follow_up', lambda *_: None)(active_task, query)):
        request = {**prior, 'selected_task': active_task, 'aspects': [aspect]}
        continuation, turn_kind = True, 'followup'
    elif prior and not getattr(policy, 'focused_task', lambda _: None)(text):
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
        if text.strip().lower() in sources and not prior.get('document_guidance'):
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
            elif update.aspects and 'procedure' not in request['aspects']:
                slots.pop('intent', None)
            route_changed = changed or slots.get('intent') != prior.get('slots', {}).get('intent')
            if route_changed:
                request.pop('selected_task', None)
                request.pop('source_id', None)
            elif active_task:
                request['selected_task'] = active_task
                if len(update.aspects) == 1 and not update.add and not update.slots:
                    related_task = getattr(by_id.get(active_task), 'related_aspects', {}).get(update.aspects[0])
                    if related_task:
                        request['selected_task'] = related_task
                        request.pop('source_id', None)
            continuation = True
            turn_kind = 'correction' if update.correction else 'followup' if update.aspects else 'slot_update'
            previous_slots = prior.get('slots', {})
            conflicts_with_prior = any(previous_slots.get(key) not in (None, value)
                                       for key, value in update.replaced_slots.items())
            if update.conflict or conflicts_with_prior:
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
                       or analysis.document_lookup or analysis.missing_slots or analysis.recognized_topic
                       or (not issue and starts_new_request(query)))
    retained = False
    ambiguous_pending_target = (candidates and analysis.clarification
        and any(card.id in old['candidates'] for card in analysis.cards))
    if not continuation and prior and (not independent or len(chosen) > 1 or ambiguous_pending_target):
        request, retained, turn_kind = deepcopy(prior), True, 'clarification'
        if old.get('pending') or old.get('pending_inputs'):
            retained_plan = analyze_question(request['question'], catalog, lexicon, policy, request=request)
            analysis = (replace(retained_plan, question=analysis.question)
                        if ambiguous_pending_target and len(chosen) < 2 else retained_plan)
        if query in ('cam on', 'xin cam on', 'cam on ban'):
            issue = 'Bạn có thể hỏi tiếp về chủ đề đang trao đổi.'
            if analysis.clarification or old.get('pending_prompts'):
                issue += ' ' + (' '.join(old.get('pending_prompts', [])) or analysis.clarification)
        else:
            issue = issue or ('Mình vẫn cần làm rõ yêu cầu trước đó. ' + (
                ' '.join(old.get('pending_prompts', [])) or analysis.clarification or
                'Bạn hãy nêu rõ phần muốn hỏi tiếp.'))
    elif not continuation and independent:
        issue = None
    # Switching tasks also invalidates inputs embedded in the original question.
    new_task = analysis.cards[0].id if len(analysis.cards) == 1 and not analysis.ambiguous else None
    if continuation and not issue and active_task and new_task != active_task:
        request['user_inputs'] = {}
        analysis = replace(analysis, user_inputs=())
    if issue:
        analysis = replace(analysis, clarification=issue, clarification_reason='task_needs_clarification')
    elif focus_unit is not None:
        analysis = replace(analysis, focus_unit=focus_unit)
    elif not continuation and not (analysis.parts or analysis.clarification or analysis.unsupported_person):
        analysis = replace(analysis, clarification=unknown_topic_prompt(text, policy), topic_unmatched=True)
    if (legacy_prompt or not compatible) and valid and previous.get('pending') == 'task' and previous.get('request'):
        previous_cards = [by_id[key] for key in previous.get('candidates', []) if key in by_id]
        old_choice, _ = _typed_choice(query, previous_cards, policy)
        old_name, _ = _typed_choice(query, previous_cards, policy, allow_number=False)
        if not continuation and (old_choice or query.isdecimal()):
            previous_request = previous['request']
            question = previous_request['question']
            request = (deepcopy(previous_request) if compatible else
                       dict(question=question, slots={}, user_inputs=parse_inputs(question)))
            refreshed = analyze_question(question, catalog, lexicon, policy, request=request)
            message = 'Danh sách lựa chọn đã thay đổi. Bạn hãy nêu lại phạm vi cần hỏi.'
            analysis = replace(refreshed, clarification=message, clarification_reason='context_needs_restatement',
                               check_scopes=refreshed.check_scopes and not old_name,
                               refresh_choices=True, choice_options=())
            new_task = None
            independent = True
    if previous and not compatible and not independent:
        analysis = replace(analysis, clarification='Ngữ cảnh hội thoại đã thay đổi hoặc không còn hợp lệ. Bạn hãy nêu lại câu hỏi đầy đủ.',
                           clarification_reason='context_needs_restatement', choice_options=())
    if not retained or old.get('pending') or old.get('pending_inputs'):
        request['aspects'] = list(analysis.aspects)
    if policy is not None and policy.catalog is not None and not analysis.unsupported_person:
        root = policy.analyze(request['question'])
        slots = {key: value for key, value in vars(root).items()
                 if key in ('subtype', 'system', 'days', 'sessions') and value is not None}
        if root.days is not None or root.sessions is not None:
            slots['granularity'] = root.granularity
        request['slots'] = {**slots, **request.get('slots', {})}
    state = _state(analysis, request, recent, stamp)
    if (analysis.check_scopes or analysis.refresh_choices) and len(analysis.cards) <= 5:
        state['scope_task_ids'] = [card.id for card in analysis.cards]
    elif prior and (continuation or retained) and related:
        state['scope_task_ids'] = [card.id for card in related]
    state['input_task_id'] = new_task if not issue else active_task
    bound_card = by_id.get(state['input_task_id'])
    if bound_card is not None:
        state['task_binding'] = _task_binding(bound_card)
    if prior and active_task and (retained or (continuation and new_task == active_task)):
        state['claim_scopes'] = old.get('claim_scopes', [])
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


def finalize_scope(state, analysis):
    """Persist the options actually offered and any scope used to answer."""
    request = deepcopy(state['request'])
    if analysis.discovery_options and request is not None:
        request['document_guidance'] = True
        request['aspects'] = list(analysis.aspects)
    if analysis.scope_intro and request is not None:
        request['selected_task'] = analysis.cards[0].id
        if analysis.scope_system:
            request['slots']['system'] = analysis.scope_system
    state = {**state, **_state(analysis, request, state['recent'], state['identity'])}
    if analysis.scope_intro:
        state['input_task_id'] = analysis.cards[0].id
        state['task_binding'] = _task_binding(analysis.cards[0])
    return state


def compact(state, result, *, analysis=None):
    state = {**state, 'recent': list(state['recent'])}
    state['outcome'] = result['status']
    if result.get('claims'):
        aspects = {}
        if analysis is not None and len(analysis.cards) == 1 and state.get('task_id'):
            for name, section in getattr(analysis.cards[0], 'sections', {}).items():
                if name in analysis.aspects:
                    for unit in section.units:
                        aspects.setdefault(unit.text, set()).add((name, unit.id))
        document_ids = {item['id'] for item in result.get('evidence', []) if item['kind'] == 'document'}
        claim_scopes = []
        for claim in result['claims']:
            scopes = aspects.get(claim['text'], ())
            refs = claim.get('evidence_refs', [])
            claim_scopes.append(list(next(iter(scopes))) if len(scopes) == 1 and refs
                                and document_ids.issuperset(refs) else None)
        state['claim_scopes'] = claim_scopes
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
        logger.info('conversation_context_discard reason=budget_exceeded')
        return {'schema': SCHEMA, 'identity': 'context_budget_exceeded', 'recent': []}
    return state
