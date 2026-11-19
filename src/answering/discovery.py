"""Suggest catalog topics with current document evidence; a selection still needs a fresh read."""
import asyncio
from dataclasses import replace
import logging
from time import perf_counter

from src.retrieval import RetrievalError
from src.type_b.tasks import normalized, phrase, route_question

from .pipeline import matches_reference, section_units
from .planning import choice_labels
from .scope import Scope, address, collapse_duplicate_scopes

logger = logging.getLogger(__name__)
LOOKUP_LIMIT = 8
OPTION_LIMIT = 3
REFERENCE_LIMIT = 10
DISCOVERY_SECONDS = 5


def eligible_for_discovery(analysis):
    if not analysis.topic_unmatched or analysis.user_inputs or len(analysis.aspects) != 1:
        return False
    query = normalized(analysis.question)
    restricted = ('va', 'dong thoi', 'kem', 'cua toi', 'cua minh', 'dong nghiep', 'nguoi khac',
                  'toi bi', 'toi co duoc', 'toi duoc', 'cua tai khoan', 'ho so nhan su',
                  'so sanh', 'tu gui', 'tu duyet', 'khong', 'chua', 'ngoai tru')
    return (';' not in analysis.question and '\n' not in analysis.question
            and not any(phrase(query, term) for term in restricted)
            and route_question(analysis.question).source == 'type_a')


def matching_cards(catalog, question, aspect):
    query = normalized(question)
    return [card for card in catalog.cards if aspect in card.sections
            and any(phrase(query, normalized(term)) for term in card.discovery_terms)
            and all(not phrase(query, normalized(term)) for term in card.excludes_any)
            and (not card.requires_any or any(phrase(query, normalized(term)) for term in card.requires_any))]


def candidate_units(cards, analysis, matches):
    ranked = {(m['document_id'], m['revision_id'], m['chunk_index']): i for i, m in enumerate(matches)}
    candidates = []
    for card in cards:
        for unit in section_units(card.sections[analysis.aspects[0]], analysis):
            ranks = [ranked[address(ref)] for ref in unit.refs if address(ref) in ranked]
            if ranks:
                candidates.append((min(ranks), card.id, unit.id, card, unit))
    return sorted(candidates, key=lambda candidate: candidate[:3])


async def lookup_options(cards, analysis, retrieval):
    result = await retrieval.retrieve(analysis.question, top_k=LOOKUP_LIMIT)
    matches = result.get('matches', ())[:LOOKUP_LIMIT]
    candidates = candidate_units(cards, analysis, matches)
    chosen, references = [], {}
    for _, _, _, card, unit in candidates:
        refs = {address(ref): ref for ref in unit.refs}
        if len(references.keys() | refs.keys()) > REFERENCE_LIMIT:
            continue
        references.update(refs)
        chosen.append((card, unit))
    if not references:
        return ()
    raw = await retrieval.read_evidence(list(references.values()))
    current = {(m['document_id'], m['revision_id'], m['chunk_index']): m for m in raw['matches']}
    supported = {}
    for card, unit in chosen:
        if all((match := current.get(address(ref))) is not None and matches_reference(ref, match) for ref in unit.refs):
            supported.setdefault(card.id, card)
    scopes = [(Scope(card.label, card), ('partial', 1)) for card in supported.values()]
    distinct = collapse_duplicate_scopes(scopes, analysis, current)
    return choice_labels([scope.card for scope, _ in distinct[:OPTION_LIMIT]], include_filenames=True)


async def suggest_document_topics(analysis, catalog, retrieval):
    if not eligible_for_discovery(analysis):
        return analysis, 0.
    cards = matching_cards(catalog, analysis.question, analysis.aspects[0])
    if not cards:
        return analysis, 0.
    started = perf_counter()
    try:
        async with asyncio.timeout(DISCOVERY_SECONDS):
            options = await lookup_options(cards, analysis, retrieval)
    except (RetrievalError, TimeoutError):
        logger.info('topic_discovery state=unavailable elapsed_ms=%.1f', (perf_counter() - started) * 1000)
        return analysis, perf_counter() - started
    elapsed = perf_counter() - started
    logger.info('topic_discovery state=%s options=%d elapsed_ms=%.1f',
                'offered' if options else 'no_eligible_topic', len(options), elapsed * 1000)
    if not options:
        return analysis, elapsed
    labels = '; '.join(f'{index}. {label}' for index, (_, label) in enumerate(options, 1))
    return replace(analysis, topic_unmatched=False, discovery_options=True, choice_options=options,
                   clarification=f'Mình tìm được chủ đề có thể phù hợp. Bạn muốn xem nội dung nào? {labels}'), elapsed
