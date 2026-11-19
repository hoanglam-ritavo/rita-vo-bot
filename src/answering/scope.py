"""Resolve document scopes against one request-local evidence snapshot."""
from dataclasses import dataclass, replace
import logging
from time import perf_counter

from .pipeline import matches_reference, section_units
from .planning import choice_labels

logger = logging.getLogger(__name__)
MAX_OPTIONS = 5
SYSTEM_LABELS = {'workit': 'Workit', 'eleave': 'E-Leave'}
PART_LABELS = {
    'overview': 'tổng quan', 'amount': 'mức hưởng hoặc hỗ trợ',
    'conditions': 'điều kiện', 'paperwork': 'hồ sơ, giấy tờ', 'procedure': 'thủ tục',
}


@dataclass(frozen=True)
class Scope:
    label: str
    card: object | None
    system: str | None = None

    def references(self, analysis):
        if self.card is None:
            return ()
        return (ref for aspect in analysis.aspects
                for unit in section_units(self.card.sections.get(aspect), analysis)
                for ref in unit.refs)

    def coverage(self, analysis, matches):
        if self.card is None:
            return 'none', 0
        complete, supported = True, 0
        for aspect in analysis.aspects:
            section = self.card.sections.get(aspect)
            units = section_units(section, analysis)
            complete &= bool(units and section.complete)
            for unit in units:
                covered = all((match := matches.get(address(ref))) is not None
                              and matches_reference(ref, match) for ref in unit.refs)
                supported += covered
                complete &= covered
        return ('full' if complete else 'partial' if supported else 'none'), supported


def address(ref):
    return ref.document_id, ref.revision_id, ref.chunk_index


def evidence_limit(analysis, labels):
    requested = ', '.join(PART_LABELS[aspect] for aspect in analysis.aspects)
    return f'Chưa đủ căn cứ hiện hành cho phần {requested} của: ' + ', '.join(labels) + '.'


def duplicate_preference(left, right, analysis, relations):
    """Return the preferred side for identical scopes with verified duplicate revisions."""
    if (left.card is None or right.card is None or left.label != right.label
            or left.system != right.system or left.card.context != right.card.context):
        return None
    directions = set()
    for aspect in analysis.aspects:
        first, second = left.card.sections.get(aspect), right.card.sections.get(aspect)
        if first is None or second is None:
            if first is not second:
                return None
            continue
        if (first.complete, first.limitations) != (second.complete, second.limitations):
            return None
        first_units, second_units = section_units(first, analysis), section_units(second, analysis)
        if len(first_units) != len(second_units):
            return None
        for first_unit, second_unit in zip(first_units, second_units):
            if first_unit.text != second_unit.text or len(first_unit.refs) != len(second_unit.refs):
                return None
            for a, b in zip(first_unit.refs, second_unit.refs):
                if (a.source_sha256, a.content_sha256, a.quote) != (b.source_sha256, b.content_sha256, b.quote):
                    return None
                if any(a.metadata.get(key) != b.metadata.get(key) for key in a.metadata if key != 'source_path'):
                    return None
                if a.revision_id == b.revision_id:
                    continue
                pair = (a.revision_id, b.revision_id)
                forward, reverse = pair in relations, pair[::-1] in relations
                if forward == reverse:
                    return None
                if forward:
                    directions.add('right')
                else:
                    directions.add('left')
    return next(iter(directions)) if len(directions) == 1 else None


def collapse_duplicate_scopes(coverage, analysis, matches):
    relations = {tuple(pair) for match in matches.values() for pair in match.get('duplicate_revision_pairs', ())}
    if not relations:
        return coverage
    levels = {'none': 0, 'partial': 1, 'full': 2}
    distinct = []
    for scope, (status, count) in coverage:
        for index, (kept, (kept_status, kept_count)) in enumerate(distinct):
            preferred = duplicate_preference(kept, scope, analysis, relations)
            if preferred:
                if (levels[status], count, preferred == 'right') > (levels[kept_status], kept_count, preferred == 'left'):
                    distinct[index] = (scope, (status, count))
                break
        else:
            distinct.append((scope, (status, count)))
    return distinct


async def resolve_document_scopes(analysis, catalog, retrieval):
    """Return the final plan and optional evidence to reuse in this answer."""
    if not analysis.check_scopes and not analysis.leave_platforms:
        return analysis, None

    if analysis.leave_platforms:
        cards = {card.id: card for card in catalog.cards}
        scopes = [Scope(SYSTEM_LABELS[system], cards.get(task), system)
                  for system, task in analysis.leave_platforms]
    else:
        scopes = [Scope(card.label, card) for card in analysis.cards]

    by_scope = [(scope, tuple(scope.references(analysis))) for scope in scopes]
    if sum(bool(refs) for _, refs in by_scope) > MAX_OPTIONS:
        return replace(analysis, choice_options=(), clarification=(
            'Có nhiều phạm vi phù hợp. Bạn hãy nêu rõ chính sách, tài liệu hoặc hệ thống cần hỏi.')), None

    refs = {address(ref): ref for _, refs in by_scope for ref in refs}
    started = perf_counter()
    raw = await retrieval.read_evidence(list(refs.values())) if refs else None
    matches = {(m['document_id'], m['revision_id'], m['chunk_index']): m
               for m in raw['matches']} if raw else {}
    coverage = [(scope, scope.coverage(analysis, matches)) for scope in scopes]
    coverage = [(scope, status) for scope, (status, _) in collapse_duplicate_scopes(coverage, analysis, matches)]
    available = [(scope, status) for scope, status in coverage if status != 'none']
    unavailable = tuple(dict.fromkeys(scope.label for scope, status in coverage if status == 'none'))
    logger.info('answer_scope candidates=%d viable=%d evidence_read=%s elapsed_ms=%.1f',
                len(scopes), len(available), bool(refs), (perf_counter() - started) * 1000)

    if not available:
        reason = ('answer_reference_unavailable' if refs else
                  'leave_task_unsupported' if analysis.leave_platforms and not any(s.card for s in scopes)
                  else 'answer_section_unavailable')
        logger.info('answer_scope outcome=%s', reason)
        return replace(analysis, cards=(), ambiguous=False, clarification=None, missing_slots=(),
                       choice_options=(), document_reason=reason,
                       document_message=evidence_limit(analysis, unavailable)), None

    limits = (evidence_limit(analysis, unavailable),) if unavailable else ()
    if len(available) == 1 and not analysis.refresh_choices:
        scope, _ = available[0]
        # Keep the request snapshot and expose only rows for the chosen scope.
        chosen_addresses = {address(ref) for ref in scope.references(analysis)}
        raw = {**raw, 'matches': [m for key, m in matches.items() if key in chosen_addresses]}
        return replace(analysis, cards=(scope.card,), ambiguous=False, clarification=None, missing_slots=(),
                       choice_options=(), general_guidance=True,
                       scope_intro=f'Phạm vi trả lời là {scope.label}.',
                       scope_limits=limits, scope_system=scope.system), raw

    if analysis.leave_platforms:
        partial = [scope.label for scope, status in available if status == 'partial']
        message = analysis.clarification
        if partial:
            message += ' Hướng dẫn chỉ có một phần cho: ' + ', '.join(partial) + '.'
        return replace(analysis, clarification=message), None

    options = choice_labels([scope.card for scope, _ in available], include_filenames=True)
    if not options:
        return replace(analysis, choice_options=(), clarification=(
            'Bạn hãy nêu rõ tài liệu hoặc phạm vi cần hỏi để phân biệt các quy định.')), None
    statuses = {scope.card.id: status for scope, status in available}
    labels = [f'{index}. {label}' + (' (trả lời một phần)' if statuses[key] == 'partial' else '')
              for index, (key, label) in enumerate(options, 1)]
    message = 'Bạn muốn hỏi phạm vi nào? Hãy chọn số hoặc tên: ' + '; '.join(labels)
    if analysis.refresh_choices:
        message = 'Danh sách lựa chọn đã được cập nhật. ' + message
    if limits:
        message += '. ' + limits[0]
    return replace(analysis, cards=tuple(scope.card for scope, _ in available),
                   clarification=message, choice_options=options), None
