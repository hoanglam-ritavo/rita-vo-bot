"""Bounded turn recognition. No history text is promoted to source evidence."""
from dataclasses import dataclass, field, replace
from datetime import date, datetime
import re

from src.answering.slots import parse_inputs
from src.type_b.tasks import normalized

ASPECTS = {
    'paperwork': ('ho so', 'giay to', 'can giay to gi', 'can ho so gi', 'ho so gom nhung gi'),
    'amount': ('muc ho tro', 'muc thuong', 'bao nhieu tien', 'muc ho tro bao nhieu'),
    'conditions': ('dieu kien', 'dieu kien ap dung', 'ap dung cho ai'),
    'procedure': ('thu tuc', 'quy trinh', 'cac buoc', 'lam the nao', 'can lam gi', 'phai lam gi'),
    'overview': ('tong quan',),
}
DATE_SLOTS = frozenset(('as_of', 'leave_dates'))
DATE = r'(?:\d{4}-\d{2}-\d{2}|\d{2}/\d{2}/\d{4})'


@dataclass(frozen=True)
class TurnUpdate:
    slots: dict[str, str | float | None] = field(default_factory=dict)
    aspects: tuple[str, ...] = ()
    submission: bool = False
    conflict: bool = False
    family: str | None = None
    add: bool = False
    correction: bool = False
    replaced_slots: dict[str, str | float | None] = field(default_factory=dict)


@dataclass(frozen=True)
class InputUpdate:
    values: dict[str, str] = field(default_factory=dict)
    error: str | None = None


def starts_new_request(query):
    """Recognize an explicit, self-contained request after short follow-ups are checked."""
    match = re.fullmatch(
        r'(?:(?:toi|minh) (?:muon hoi|muon biet)|cho (?:toi|minh) hoi|'
        r'lam sao de|lam the nao de|(?:xin )?huong dan(?: (?:toi|minh))?) (.+)', query)
    if not match:
        match = re.fullmatch(r'(?:(?:toi|minh) )?(?:bi|da|vua|can|muon) (.+?) '
                             r'(?:thi )?(?:phai lam gi|can lam gi|lam sao|xu ly the nao)', query)
    if not match:
        match = re.fullmatch(r'(.+?) thi (?:phai lam gi|can lam gi|xu ly the nao)', query)
    if not match:
        return False
    words = match[1].split()
    short_references = {'lam', 'viec', 'do', 'nay', 'cai', 'phan', 'muc', 'o', 'dau', 'tiep', 'gi',
                        'them', 'sua', 'truoc', 'tren', 'truong', 'hop', 'thu', 'tuc', 'ho', 'so', 'giay', 'to'}
    return len(words) >= 2 and not set(words) <= short_references


def recognize(text, policy):
    """Consume the entire reply, including explicit negative/replacement clauses."""
    # Only an explicit correction with a recognized old and replacement value
    # may consume negation. Ordinary negation must still ask for clarification.
    correction = re.fullmatch(
        r'\s*kh(?:ô|o)ng\s+ph(?:ả|a)i\s+(.+?)(?:\s*[,;]\s*|\s+m(?:à|a)\s+l(?:à|a)\s+)(.+?)\s*[.!?]?\s*',
        text, re.IGNORECASE)
    if correction:
        before, after = (_recognize(part, policy) for part in correction.groups())
        if (before and after and before.slots and after.slots
                and not before.conflict and not after.conflict and not before.aspects
                and set(before.slots) == set(after.slots)
                and before.slots != after.slots):
            platform_only = set(after.slots) == {'system'}
            if platform_only or before.family == after.family:
                return replace(after, correction=True, family=None if platform_only else after.family,
                               replaced_slots=before.slots)
        return None
    return _recognize(text, policy)


def _recognize(text, policy):
    from src.retrieval.policy import folded, rule_matches, DURATION, duration, spans
    query = normalized(text)
    remaining = list(folded(text))
    aspects = []
    for key, aliases in ASPECTS.items():
        if any(spans(''.join(remaining), alias) for alias in aliases):
            aspects.append(key)
    updates, conflict, family = {}, False, None
    if policy is not None and policy.catalog is not None:
        original = ''.join(remaining)
        hits = {name: rule_matches(original, getattr(policy.catalog, name), policy.concepts)
                for name in ('families', 'subtypes', 'systems')}
        families = {row[2] for row in hits['families']}
        if len(families) > 1:
            return None
        family = next(iter(families), None)
        for name, rows in hits.items():
            ids = {row[2] for row in rows}
            if name != 'families':
                conflict |= len(ids) > 1
                if len(ids) == 1:
                    updates['system' if name == 'systems' else 'subtype'] = next(iter(ids))
            for start, end, _ in rows:
                remaining[start:end] = ' ' * (end-start)
        days, sessions, granularity, bad = duration(original)
        conflict |= bad
        if days is not None or sessions is not None:
            updates.update(days=days, sessions=sessions, granularity=granularity)
        for match in DURATION.finditer(original):
            remaining[match.start():match.end()] = ' ' * (match.end()-match.start())
        for rule in policy.catalog.families:
            for term in rule.platform_terms:
                for start, end in spans(original, term):
                    remaining[start:end] = ' ' * (end-start)
    allowed = {'tren', 'toi', 'minh', 'dung', 'dang', 'hien', 'tai', 'su', 'la', 'va',
        'loai', 'he', 'thong', 'nghi', 'muon', 'xin', 'chon', 'nhe', 'nha', 'a', 'vay',
        'thi', 'sao', 'con', 've', 'them', 'doi', 'thanh', 'sang', 'chuyen', 'lai', 'huong', 'dan', 'lam',
        'thu', 'tuc', 'o', 'dau', 'nop', 'gui', 'don', 'can', 'gi', 'tiep', 'the', 'nao',
        'quy', 'trinh', 'cac', 'buoc', 'hoac', 'ho', 'so', 'giay', 'to', 'gom', 'nhung',
        'dieu', 'kien', 'ap', 'cho', 'ai', 'muc', 'tro', 'thuong', 'bao', 'nhieu', 'tien',
        'tong', 'quan', 'neu', 'chuan', 'bi', 'truoc', 'khi'}
    if not set(normalized(''.join(remaining)).split()) <= allowed:
        return None
    submission = any(spans(query, term) for term in ('o dau', 'nop o', 'gui don', 'huong dan tren'))
    if submission or spans(query, 'huong dan') or spans(query, 'lam gi tiep'):
        if 'procedure' not in aspects:
            aspects.append('procedure')
    if not (updates or aspects or conflict):
        return None
    return TurnUpdate(updates, tuple(aspects), submission, conflict, family,
                      query.startswith('them '), any(spans(query, term) for term in ('doi', 'thanh', 'chuyen sang')))


def _dates(value, slot):
    pattern = DATE if slot == 'as_of' else DATE + r'(?:\s+(?:đến|den|tới|toi)\s+' + DATE + r')?'
    value = value.strip()
    if slot == 'leave_dates':
        value = re.sub(r'^(?:từ|tu)\s+', '', value, flags=re.IGNORECASE)
    if not re.fullmatch(pattern, value, flags=re.IGNORECASE):
        return None
    result = []
    for raw in re.findall(DATE, value):
        try:
            result.append(date.fromisoformat(raw) if '-' in raw else datetime.strptime(raw, '%d/%m/%Y').date())
        except ValueError:
            return None
    if len(result) == 2 and result[0] > result[1]:
        return None
    return ' đến '.join(day.isoformat() for day in result)


def input_update(text, pending, existing):
    """Only uniquely targeted dates or fully labelled permitted slots are accepted."""
    allowed = set(pending) | set(existing)
    labelled = parse_inputs(text)
    if labelled:
        # parse_inputs intentionally ignores unknown pieces; a continuation must
        # not silently drop them or accept duplicate assignments.
        parts = [part.strip() for part in re.split(r'[;\n]', text) if part.strip()]
        if (len(labelled) != len(parts) or not set(labelled) <= allowed
                or any(len(parse_inputs(part)) != 1 for part in parts)):
            return InputUpdate(error='Bạn hãy bổ sung riêng thông tin đang được hỏi, kèm nhãn tương ứng.')
        for slot, value in labelled.items():
            if slot in DATE_SLOTS:
                converted = _dates(value, slot)
                if converted is None:
                    return InputUpdate(error='Ngày chưa hợp lệ. Hãy dùng YYYY-MM-DD hoặc DD/MM/YYYY, kèm nhãn ngày cần bổ sung.')
                labelled[slot] = converted
        return InputUpdate(labelled)
    date_pending = set(pending) & DATE_SLOTS
    if date_pending and re.search(r'\d|ngày|ngay|tuần|tuan|thứ|thu', text, re.IGNORECASE):
        if len(date_pending) == 1:
            slot = next(iter(date_pending))
            value = _dates(text.strip().rstrip('.!?'), slot)
            if value is not None:
                return InputUpdate({slot: value})
        return InputUpdate(error='Bạn hãy ghi rõ nhãn ngày và ngày hợp lệ theo YYYY-MM-DD hoặc DD/MM/YYYY.')
    return InputUpdate()
