"""Suggest catalog topics with current document evidence; a selection still needs a fresh read."""
import asyncio
from dataclasses import replace
from itertools import zip_longest
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


def bounded_units(units):
    selected, references = [], {}
    for card, unit in units:
        refs = {address(ref): ref for ref in unit.refs}
        if len(references.keys() | refs.keys()) > REFERENCE_LIMIT:
            continue
        references.update(refs)
        selected.append((card, unit))
    return selected, list(references.values())


def ranked_units(units, matches):
    ranked = {(m['document_id'], m['revision_id'], m['chunk_index']): i for i, m in enumerate(matches)}
    candidates = []
    for card, unit in units:
        ranks = [ranked[address(ref)] for ref in unit.refs if address(ref) in ranked]
        if ranks:
            candidates.append((min(ranks), card.id, unit.id, card, unit))
    return [(card, unit) for _, _, _, card, unit in sorted(candidates, key=lambda candidate: candidate[:3])]


async def lookup_options(cards, analysis, retrieval):
    per_card = [section_units(card.sections[analysis.aspects[0]], analysis) for card in cards]
    units, references = bounded_units((card, unit) for group in zip_longest(*per_card)
                                     for card, unit in zip(cards, group) if unit is not None)
    result = await retrieval.discover(analysis.question, references, top_k=LOOKUP_LIMIT)
    chosen, references = bounded_units(ranked_units(units, result.get('matches', ())[:LOOKUP_LIMIT]))
    if not references:
        return ()
    raw = await retrieval.read_evidence(references)
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
