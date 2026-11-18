"""Conservative, deterministic conversation reduction; history is not evidence."""
import hashlib
import json
from src.answering.pipeline import select_cards
from src.type_b.tasks import normalized, TASKS, route_question

ASPECTS = {
    'paperwork': ('ho so', 'giay to', 'can giay to gi', 'can ho so gi', 'ho so gom nhung gi'),
    'amount': ('muc ho tro', 'muc thuong', 'bao nhieu tien', 'muc ho tro bao nhieu'),
    'conditions': ('dieu kien', 'dieu kien ap dung', 'ap dung cho ai'),
    'procedure': ('thu tuc', 'quy trinh', 'cac buoc', 'lam the nao'),
    'overview': ('tong quan',),
}
PHRASES = {'paperwork': 'hồ sơ giấy tờ', 'amount': 'bao nhiêu tiền',
           'conditions': 'điều kiện', 'procedure': 'thủ tục', 'overview': 'tổng quan'}


def identity(catalog, lexicon):
    if catalog is None or lexicon is None:
        return 'unavailable'
    return hashlib.sha256(json.dumps([catalog.model_dump(), lexicon], sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()


def reduce_question(text, previous, catalog, lexicon, *, identity_stamp=None):
    stamp = identity_stamp if identity_stamp is not None else identity(catalog, lexicon)
    old = previous if previous.get('schema') == 1 and previous.get('identity') == stamp else {}
    state = {'schema': 1, 'identity': stamp, 'task_id': None, 'candidates': [],
             'pending': None, 'aspect': None, 'recent': old.get('recent', [])[-3:] + [text]}
    route = route_question(text)
    state['self_reference'] = bool(route.fields) or route.reason == 'ambiguous_employment_date'
    query = normalized(text)
    # Only exact bounded follow-ups can inherit a topic. Never rewrite negation,
    # named subjects or personal eligibility clauses into a positive policy query.
    followup = query
    for prefix in ('con ', 've '):
        if followup.startswith(prefix):
            followup = followup[len(prefix):]
    for suffix in (' thi sao', ' the nao'):
        if followup.endswith(suffix):
            followup = followup[:-len(suffix)]
    if old.get('type_b_self'):
        tasks = [task for task in TASKS if followup in task.aliases]
        if len(tasks) == 1:
            state['self_reference'] = True
            return followup + ' của tôi', state
    if catalog is None or lexicon is None:
        return text, state
    by_id = {card.id: card for card in catalog.cards}
    aspect = next((key for key, aliases in ASPECTS.items() if followup in aliases), None)
    card = by_id.get(old.get('task_id'))
    if aspect and card is not None:
        state.update(task_id=card.id, aspect=aspect)
        return card.aliases[0] + ' ' + PHRASES[aspect], state
    candidates = [by_id[key] for key in old.get('candidates', []) if key in by_id]
    chosen = [card for card in candidates if query in {normalized(card.label), normalized(card.id),
                                                     *(normalized(alias) for alias in card.aliases)}]
    if len(chosen) == 1:
        state.update(task_id=chosen[0].id)
        return chosen[0].aliases[0], state
    cards, ambiguous = select_cards(text, catalog, lexicon)
    if ambiguous:
        state.update(candidates=[card.id for card in cards][:5], pending='task')
    elif len(cards) == 1:
        state.update(task_id=cards[0].id)
    return text, state


def compact(state, result):
    state = dict(state)
    state['outcome'] = result['status']
    if result.get('source_type') in ('type_b', 'mixed'):
        state['type_b_self'] = state.get('self_reference', False) and result.get('employee_evidence', {}).get('status') in ('found', 'needs_clarification')
    state['evidence_refs'] = [
        {key: origin[key] for key in ('document_id', 'revision_id', 'chunk_id') if key in origin}
        for source in result.get('sources', []) for origin in source.get('origins', [])][:60]
    # Never cache employee facts or assistant prose as an authoritative source.
    while len(json.dumps(state, ensure_ascii=False).encode()) > 48 * 1024 and state['recent']:
        state['recent'].pop(0)
    return state
