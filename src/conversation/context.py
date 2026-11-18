"""Conservative, deterministic conversation reduction; history is not evidence."""
import hashlib
import json
import re
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


def identity(catalog, lexicon, policy=None):
    if catalog is None or lexicon is None:
        return 'unavailable'
    return hashlib.sha256(json.dumps([2, catalog.model_dump(), lexicon, getattr(policy, 'fingerprint', None)], sort_keys=True,
                                    ensure_ascii=False).encode()).hexdigest()


def reduce_question(text, previous, catalog, lexicon, *, identity_stamp=None, policy=None):
    stamp = identity_stamp if identity_stamp is not None else identity(catalog, lexicon, policy)
    old = previous if previous.get('schema') == 2 and previous.get('identity') == stamp else {}
    state = {'schema': 2, 'identity': stamp, 'task_id': None, 'candidates': [],
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
        base = old.get('task_question', card.aliases[0])
        state['task_question'] = base
        return base + ' ' + PHRASES[aspect], state
    if policy is not None and policy.catalog is not None:
        # Consume only bounded slot answers/source choices, never arbitrary prose.
        base = old.get('pending_question') or old.get('task_question')
        if base:
            completed = complete_slots(text, base, old, policy)
            if completed is not None:
                text = completed
        resolution = policy.resolve(text)
        if resolution.state in ('clarify', 'unsupported'):
            state.update(pending='slots' if resolution.missing_slots else 'task',
                         pending_question=text[:2000], candidates=list(resolution.candidates),
                         missing_slots=list(resolution.missing_slots))
            query = policy.analyze(text)
            state['slots'] = {key: {'value': getattr(query, key), 'origin': 'user_text'}
                              for key in ('subtype', 'system', 'days', 'sessions', 'granularity')
                              if getattr(query, key) is not None}
            return text, state
        if resolution.state == 'task':
            state.update(task_id=resolution.task_id, task_question=text[:2000])
            query = policy.analyze(text)
            state['slots'] = {key: {'value': getattr(query, key), 'origin': 'user_text'}
                              for key in ('subtype', 'system', 'days', 'sessions', 'granularity')
                              if getattr(query, key) is not None}
            return text, state
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
        state.update(task_id=cards[0].id, task_question=text[:2000])
    return text, state


def complete_slots(text, base, old, policy):
    from src.retrieval.policy import folded, rule_matches
    query = folded(text)
    if old.get('candidates'):
        sources = {b.ref.document_id for b in policy.catalog.bindings
                   if set(b.task_ids).intersection(old['candidates'])}
        if query in {folded(doc) for doc in sources}:
            return base + ' ' + text
    hits = {name: rule_matches(query, getattr(policy.catalog, name), policy.concepts)
            for name in ('subtypes', 'systems')}
    # Never inherit negation, a new business topic or an unrecognized clause.
    remaining = list(query)
    for rows in hits.values():
        for start, end, _ in rows:
            remaining[start:end] = ' ' * (end-start)
    if not any(hits.values()) or not set(''.join(remaining).split()) <= {
            'tren', 'toi', 'dung', 'la', 'va', 'loai', 'he', 'thong', 'nghi', 'muon', 'xin'}:
        return None
    prior = folded(base)
    removed = []
    for name, rows in hits.items():
        if len({row[2] for row in rows}) > 1:
            return None
        if rows:
            removed.extend(rule_matches(prior, getattr(policy.catalog, name), policy.concepts))
    chars = list(prior)
    for start, end, _ in removed:
        chars[start:end] = ' ' * (end-start)
    result = ' '.join(''.join(chars).split()) + ' ' + text
    old_family = policy.analyze(base).family
    if old_family and policy.analyze(result).family is None:
        rule = next((row for row in policy.catalog.families if row.id == old_family), None)
        if rule:
            result += ' ' + policy.concepts[rule.concept_ids[0]]['canonical']
    # Folding slot text must not destroy an explicitly selected source UUID.
    for document_id in re.findall(r'\b[0-9a-fA-F]{8}(?:-[0-9a-fA-F]{4}){3}-[0-9a-fA-F]{12}\b', base):
        if document_id not in result:
            result += ' ' + document_id
    return result


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
