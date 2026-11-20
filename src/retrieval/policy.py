"""Source-bound task hints, not learned relevance or answer authorization."""
from dataclasses import dataclass, replace
import hashlib
import logging
from pathlib import Path
import re
from typing import Literal
import unicodedata
from functools import lru_cache

from pydantic import Field, model_validator
from src.answering.catalog import Reference, METADATA
from src.answering.contracts import Strict
from src.paths import REPOSITORY_ROOT
from .office import OfficeRoute, resolve_office
from src.semantics.corpus import decode_json

logger = logging.getLogger(__name__)
DEFAULT_PATH = REPOSITORY_ROOT / 'artifacts/retrieval/catalog.json'
DEFAULT_LEXICON = REPOSITORY_ROOT / 'artifacts/semantics/lexicon.json'
Family = str
System = str
Subtype = str
Operation = str
Surface = Literal['web', 'mobile']
SURFACE_LABELS = {'web': 'Workit Web', 'mobile': 'Workit Mobile'}
SURFACE_PROMPT = 'Bạn đang dùng Workit Web hay ứng dụng Workit Mobile?'


def folded(text):
    text = unicodedata.normalize('NFD', text.casefold()).replace('đ', 'd')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^a-z0-9/.,]+', ' ', text).split())


@lru_cache(maxsize=8192)
def phrase_pattern(phrase):
    # Cache authored patterns, never questions, outcomes or personnel values.
    return re.compile(r'(?<!\w)' + re.escape(folded(phrase)) + r'(?!\w)')


def spans(text, phrase):
    return [m.span() for m in phrase_pattern(phrase).finditer(text)]


class Rule(Strict):
    id: str = Field(pattern=r'^[a-z0-9.-]{1,100}$')
    concept_ids: list[str] = Field(min_length=1)
    platform_terms: list[str] = Field(default_factory=list, max_length=40)
    execution_terms: list[str] = Field(default_factory=list, max_length=40)


class ClarificationRule(Strict):
    requires_all: list[list[str]] = Field(min_length=1, max_length=5)
    prompt: str = Field(min_length=1, max_length=500)

    @model_validator(mode='after')
    def valid_terms(self):
        if not self.prompt.strip() or any(
                not 1 <= len(group) <= 20 or any(not folded(term) or len(term) > 100 for term in group)
                for group in self.requires_all):
            raise ValueError('invalid_clarification_rule')
        return self


class FamilyRule(Rule):
    clarifications: list[ClarificationRule] = Field(default_factory=list, max_length=10)


class DurationRange(Strict):
    minimum: float = Field(ge=0, allow_inf_nan=False)
    maximum: float | None = Field(default=None, ge=0, allow_inf_nan=False)

    @model_validator(mode='after')
    def ordered(self):
        if self.maximum is not None and self.maximum < self.minimum:
            raise ValueError('invalid_duration_range')
        return self


class Binding(Strict):
    id: str = Field(pattern=r'^[a-z0-9.-]{1,100}$')
    family: Family
    role: Literal['general_procedure', 'policy', 'system_procedure', 'session_case', 'special_case']
    operation: Operation
    system: System | None = None
    subtype: Subtype | None = None
    surface: Surface | None = None
    duration_ranges: list[DurationRange] = Field(default_factory=list)
    task_ids: list[str] = Field(default_factory=list)
    aspects: list[Literal['overview', 'amount', 'conditions', 'paperwork', 'procedure']] = Field(default_factory=list)
    ref: Reference

    @model_validator(mode='after')
    def source_shape(self):
        path = self.ref.metadata.get('source_path')
        if (set(self.ref.metadata) != set(METADATA) or not self.ref.quote.strip()
                or not isinstance(path, str) or not path.strip()):
            raise ValueError('invalid_policy_reference')
        return self


@lru_cache(maxsize=8192)
def question_term(term):
    from src.type_b.tasks import normalized

    return ' ' + normalized(term) + ' '


class QuestionTerms(Strict):
    requires_all: list[list[str]] = Field(min_length=1, max_length=6)

    @model_validator(mode='after')
    def valid_groups(self):
        if any(not 1 <= len(group) <= 24 or any(not folded(term) or len(term) > 140 for term in group)
               for group in self.requires_all):
            raise ValueError('invalid_question_terms')
        return self


class Route(Strict):
    id: str = Field(pattern=r'^[a-z0-9.-]{1,100}$')
    family: str = 'leave'
    subtype: Subtype | None = None
    operation: Operation | None = None
    system: System | None = None
    granularity: Literal['day','session'] | None = None
    task_id: str = Field(pattern=r'^[a-z0-9.-]{1,100}$')
    aliases: list[str] = Field(default_factory=list)
    concept_ids: list[str] = Field(default_factory=list)
    requires_any: list[str] = Field(default_factory=list)
    excludes_any: list[str] = Field(default_factory=list)
    required_slots: list[Literal['system', 'subtype']] = Field(default_factory=list)
    question_terms: list[QuestionTerms] = Field(default_factory=list, max_length=20)
    surface: Surface | None = None
    workit_action: str | None = Field(default=None, pattern=r'^[a-z0-9.-]{1,80}$')
    surface_limited: bool = False
    office: OfficeRoute | None = None
    follow_up_terms: list[str] = Field(default_factory=list, max_length=24)
    follow_up_aspects: dict[str, Literal['amount', 'conditions', 'paperwork', 'procedure']] = Field(
        default_factory=dict, max_length=24)
    prompt: str = 'Bạn cần hướng dẫn nội dung nào và trên hệ thống nào?'


class Catalog(Strict):
    schema_version: Literal[1, 2]
    package_fingerprint: str
    lexicon_sha256: str
    families: list[FamilyRule]
    systems: list[Rule]
    subtypes: list[Rule]
    operations: list[Rule]
    bindings: list[Binding]
    routes: list[Route]

    @model_validator(mode='after')
    def unique(self):
        for name in ('families', 'systems', 'subtypes', 'operations', 'bindings', 'routes'):
            rows = getattr(self, name)
            if len({r.id for r in rows}) != len(rows):
                raise ValueError('duplicate_policy_id')
        for rule in [*self.families, *self.systems, *self.subtypes, *self.operations]:
            if any(not term.strip() or len(term) > 240 for term in rule.platform_terms + rule.execution_terms):
                raise ValueError('invalid_platform_term')
        addresses = [(b.ref.document_id, b.ref.revision_id, b.ref.chunk_index, b.family,
                      tuple(b.task_ids), tuple(b.aspects), b.surface) for b in self.bindings]
        routes = [(r.family, r.subtype, r.operation, r.system, r.granularity)
                  for r in self.routes if not r.aliases and not r.concept_ids and not r.question_terms]
        if len(set(addresses)) != len(addresses) or len(set(routes)) != len(routes):
            raise ValueError('ambiguous_policy_binding')
        allowed = {name: {r.id for r in getattr(self, name)}
                   for name in ('families', 'systems', 'subtypes', 'operations')}
        if self.schema_version == 1:
            for name, values in {'families': {'leave', 'resignation', 'attendance'},
                    'systems': {'workit', 'eleave'},
                    'subtypes': {'annual', 'unpaid', 'maternity', 'sick', 'personal'},
                    'operations': {'request', 'policy', 'approval', 'cancel', 'support'}}.items():
                allowed[name].update(values)
        for row in [*self.bindings, *self.routes]:
            if (row.family not in allowed['families'] or
                    row.system is not None and row.system not in allowed['systems'] or
                    row.subtype is not None and row.subtype not in allowed['subtypes'] or
                    row.operation is not None and row.operation not in allowed['operations']):
                raise ValueError('invalid_policy_rule')
        if any(binding.surface and binding.system != 'workit' for binding in self.bindings):
            raise ValueError('surface_requires_workit')
        focused_tasks, workit_actions = set(), set()
        for route in self.routes:
            if route.office and (not route.question_terms or route.surface or route.system or route.subtype
                                 or route.required_slots):
                raise ValueError('invalid_office_route')
            if route.surface:
                key = (route.workit_action, route.surface)
                if (route.system != 'workit' or not route.workit_action or not route.question_terms
                        or key in workit_actions or route.required_slots or route.subtype):
                    raise ValueError('invalid_workit_surface_route')
                workit_actions.add(key)
            elif route.workit_action or route.surface_limited:
                raise ValueError('missing_workit_surface')
            follow_ups = [*route.follow_up_terms, *route.follow_up_aspects]
            if any(question_term(term) == '  ' or len(term) > 100 for term in follow_ups) or len(
                    {question_term(term) for term in follow_ups}) != len(follow_ups):
                raise ValueError('invalid_focused_follow_up')
            if any(not p.strip() or len(p) > 240 for p in route.aliases + route.requires_any + route.excludes_any):
                raise ValueError('invalid_policy_phrase')
            if route.question_terms and not (route.surface or route.office):
                if route.task_id in focused_tasks or route.required_slots or route.system or route.subtype:
                    raise ValueError('invalid_focused_policy_route')
                focused_tasks.add(route.task_id)
            elif not route.question_terms and follow_ups:
                raise ValueError('focused_follow_up_without_route')
        return self


def clarification_for(catalog, question, family):
    if catalog is None:
        return None
    text = folded(question)
    prompts = {entry.prompt for rule in catalog.families if family is None or rule.id == family
               for entry in rule.clarifications
               if all(any(spans(text, term) for term in group) for group in entry.requires_all)}
    return next(iter(prompts)) if len(prompts) == 1 else None


def validate_catalog(data, lexicon, lexical_hash, answers=None):
    catalog = Catalog.model_validate(data)
    concepts = {c['id'] for c in lexicon['concepts']}
    if catalog.lexicon_sha256 != lexical_hash:
        raise ValueError('retrieval_lexicon_mismatch')
    for collection in ('families','systems','subtypes','operations'):
        if any(not set(rule.concept_ids) <= concepts for rule in getattr(catalog, collection)):
            raise ValueError('unknown_policy_concept')
    if any(not set(route.concept_ids) <= concepts for route in catalog.routes):
        raise ValueError('unknown_policy_concept')
    if answers is not None:
        cards = {c.id: c for c in answers.cards}
        if any(r.task_id not in cards for r in catalog.routes) or any(
                not set(b.task_ids) <= cards.keys() for b in catalog.bindings):
            raise ValueError('unknown_policy_task')
        surface_refs = {(b.surface, b.ref.document_id, b.ref.revision_id, b.ref.chunk_index,
                         b.ref.content_sha256, b.ref.source_sha256) for b in catalog.bindings if b.surface}
        for route in catalog.routes:
            card = cards[route.task_id]
            if route.surface:
                if 'overview' not in card.sections or any(
                        (route.surface, ref.document_id, ref.revision_id, ref.chunk_index,
                         ref.content_sha256, ref.source_sha256) not in surface_refs
                        for section in card.sections.values() for unit in section.units for ref in unit.refs):
                    raise ValueError('invalid_workit_surface_evidence')
            elif route.office:
                if ('overview' not in card.sections or (route.office.prefer_workit and
                        route.office.prefer_workit not in cards)):
                    raise ValueError('invalid_office_route_binding')
            elif route.question_terms and (not card.routed_only or card.context is None
                                           or card.context.mode != 'none' or 'overview' not in card.sections):
                raise ValueError('invalid_focused_policy_card')
            if not set(route.follow_up_aspects.values()) <= card.sections.keys():
                raise ValueError('focused_follow_up_section_unavailable')
            for aspect, section in card.sections.items():
                addresses = {(r.document_id, r.revision_id, r.chunk_index)
                             for unit in section.units for r in unit.refs}
                if len(addresses) > 10:
                    raise ValueError('retrieval_bundle_too_large')
    return catalog


@dataclass(frozen=True)
class Query:
    family: str | None = None
    subtype: str | None = None
    system: str | None = None
    operation: str = 'request'
    days: float | None = None
    sessions: float | None = None
    granularity: str = 'day'
    ambiguous: bool = False
    involves_leave: bool = False
    platform_requested: bool = False


@dataclass(frozen=True)
class Resolution:
    state: str = 'not_applicable'
    task_id: str | None = None
    message: str = ''
    candidates: tuple[str, ...] = ()
    missing_slots: tuple[str, ...] = ()
    requires_restatement: bool = False


def rule_matches(text, rules, concepts):
    hits = []
    for rule in rules:
        for identity in rule.concept_ids:
            c = concepts[identity]
            if (c['requires_any'] and not any(spans(text, p) for p in c['requires_any'])) or any(
                    spans(text, p) for p in c['excludes_any']):
                continue
            for phrase in dict.fromkeys([c['canonical'], *c['aliases']]):
                hits.extend((start, end, rule.id) for start, end in spans(text, phrase))
    # A specific overlapping phrase supersedes its contained generic phrase;
    # separate clauses never suppress each other's families/types.
    return [hit for hit in hits if not any(
        other[0] <= hit[0] and other[1] >= hit[1] and other[1]-other[0] > hit[1]-hit[0]
        for other in hits)]


NUMBERS = {folded(word): number for number, word in enumerate((
    'không', 'một', 'hai', 'ba', 'bốn', 'năm', 'sáu', 'bảy', 'tám', 'chín', 'mười'))}
NUMBERS.update({'nua': .5})
for _number in range(11, 32):
    _tens, _ones = divmod(_number, 10)
    _name = ('muoi' if _tens == 1 else {2:'hai',3:'ba'}[_tens]+' muoi')
    if _ones:
        _name += ' ' + {1:'mot',2:'hai',3:'ba',4:'bon',5:'lam',6:'sau',7:'bay',8:'tam',9:'chin'}[_ones]
    NUMBERS[_name] = _number
AMOUNT = r'(?:\d+(?:[.,]\d+)?|[.,]\d+|'+'|'.join(re.escape(n) for n in sorted(NUMBERS, key=len, reverse=True))+')'
DURATION = re.compile(r'(?<![\w/.,])('+AMOUNT+r')\s+(ngay|hom|buoi)\b')


def duration(text):
    days, sessions = [], []
    invalid = bool(re.search(r'\b\d+\s+\d+\s+(ngay|hom|buoi)\b', text))
    for match in DURATION.finditer(text):
        prefix = text[:match.start()].split()[-4:]
        if 'truoc' in prefix and ('nghi' not in prefix or prefix.index('nghi') < prefix.index('truoc')):
            # Notice period is not requested leave duration. "Trước khi xin
            # nghỉ hai ngày" still describes a requested two-day absence.
            continue
        if any(word in prefix[-2:] for word in ('hon','den','tu','nhat','da')):
            invalid = True
            continue
        token, unit = match.groups()
        value = NUMBERS[token] if token in NUMBERS else float(token.replace(',', '.'))
        if value <= 0 or value > 366:
            invalid = True
            continue
        (sessions if unit == 'buoi' else days).append(value)
    session_words = any(spans(text, p) for p in ('buoi sang','buoi chieu','nghi sang','nghi chieu','nua ngay','theo buoi'))
    session_words = session_words or bool(re.search(
        r'\b(?:sang|chieu)\s+(?:thu\s+(?:[2-7]|hai|ba|tu|nam|sau|bay)|ngay\b|nay\b|mai\b)', text))
    ambiguous = invalid or len(set(days)) > 1 or len(set(sessions)) > 1 or bool(days and sessions)
    return (days[0] if days else None, sessions[0] if sessions else None,
            'session' if session_words or sessions else 'day', ambiguous)


def workit_surfaces(text):
    # A browser on a phone is the Web interface. The app must be mentioned
    # separately to make that wording a request for both interfaces.
    browser = bool(re.search(r'(?<!quy )\btrinh duyet\b', text) or spans(text, 'browser'))
    web = browser or any(spans(text, term) for term in ('web', 'may tinh', 'desktop', 'laptop'))
    app = any(spans(text, term) for term in ('mobile', 'app', 'ung dung workit', 'android', 'iphone', 'ios'))
    phone = any(spans(text, term) for term in ('tren dien thoai', 'bang dien thoai'))
    return tuple(surface for surface, present in (('web', web), ('mobile', app or phone and not browser))
                 if present)


def workit_surface_reply(text):
    terms = {
        'web': ('web', 'workit web', 'may tinh', 'trinh duyet', 'browser', 'tren may tinh', 'tren web',
                'tren trinh duyet', 'trinh duyet tren dien thoai'),
        'mobile': ('app', 'mobile', 'workit mobile', 'ung dung', 'dien thoai', 'tren app', 'tren mobile',
                   'tren dien thoai', 'ung dung workit', 'tren ung dung', 'bang dien thoai'),
    }
    correction = re.fullmatch(r'khong phai (.+?) (?:(?:toi|minh) dung|ma la) (.+)', text)
    if correction:
        before, after = correction.groups()
        return next((surface for surface, aliases in terms.items()
                     if after in aliases and any(before in other for key, other in terms.items() if key != surface)),
                    None)
    return next((surface for surface, aliases in terms.items() if text in aliases), None)


@dataclass(frozen=True)
class WorkitResolution:
    action: str | None
    routes: tuple[Route, ...] = ()
    surface: Surface | None = None
    clarification: str | None = None
    unavailable: tuple[Surface, ...] = ()


def _route_score(route, text):
    if any(question_term(term) in text for term in route.excludes_any):
        return 0
    scores = []
    for rule in route.question_terms:
        lengths = [max((len(term) for term in group if question_term(term) in text), default=0)
                   for group in rule.requires_all]
        if all(lengths):
            scores.append(sum(lengths))
    return max(scores, default=0)


class RetrievalPolicy:
    def __init__(self, path=DEFAULT_PATH, lexicon_path=DEFAULT_LEXICON):
        self.path, self.lexicon_path = Path(path), Path(lexicon_path)
        self.catalog = None
        self.concepts = {}
        self.fingerprint = None
        self._bindings = {}
        self._generic = []
        self._focused = {}
        self._workit = {}
        self._office = ()

    def start(self):
        self.close()
        try:
            if self.path.stat().st_size > 8_000_000 or self.lexicon_path.stat().st_size > 8_000_000:
                raise ValueError('policy_too_large')
            raw, lexical = self.path.read_bytes(), self.lexicon_path.read_bytes()
            from .semantics import validate_artifact
            lexicon = validate_artifact(decode_json(lexical))
            catalog = validate_catalog(decode_json(raw), lexicon, hashlib.sha256(lexical).hexdigest())
            if self.path.read_bytes() != raw or self.lexicon_path.read_bytes() != lexical:
                raise ValueError('policy_changed')
            self.catalog = catalog
            self.concepts = {c['id']: c for c in lexicon['concepts']}
            for binding in catalog.bindings:
                self._bindings.setdefault((binding.ref.document_id, binding.ref.revision_id,
                                           binding.ref.chunk_index), []).append(binding)
            self._generic = [r for r in catalog.routes if (r.aliases or r.concept_ids) and not r.question_terms]
            self._focused = {r.task_id: r for r in catalog.routes if r.question_terms and not (r.surface or r.office)}
            self._office = tuple(route for route in catalog.routes if route.office)
            for route in catalog.routes:
                if route.surface:
                    self._workit.setdefault(route.workit_action, {})[route.surface] = route
            self.fingerprint = hashlib.sha256(raw).hexdigest()
            logger.info('retrieval_policy state=ready')
        except Exception:
            self.close()
            logger.warning('retrieval_policy state=unavailable')

    def focused_task(self, question):
        from src.type_b.tasks import normalized

        text = ' ' + normalized(question) + ' '
        matches = {task_id: score for task_id, route in self._focused.items()
                   if (score := _route_score(route, text))}
        if not matches:
            return None
        best = max(matches.values())
        winners = [task_id for task_id, score in matches.items() if score == best]
        return winners[0] if len(winners) == 1 else None

    def is_focused_task(self, task_id):
        return task_id in self._focused

    def focused_follow_up(self, task_id, normalized_question):
        route = self._focused.get(task_id)
        if route is None:
            return None
        phrase = f' {normalized_question} '
        for term, aspect in route.follow_up_aspects.items():
            if question_term(term) == phrase:
                return aspect
        return 'overview' if any(question_term(term) == phrase for term in route.follow_up_terms) else None

    def resolve_office(self, question, *, slots=None):
        return resolve_office(self._office, question, slots)

    def resolve_workit(self, question, *, slots=None):
        if not self._workit:
            return None
        from src.type_b.tasks import normalized

        text = normalized(question)
        slots = slots or {}
        if slots.get('system') != 'workit' and not (spans(text,'workit') or spans(text,'work it')):
            return None
        supplied = workit_surfaces(text)
        system_hits = {hit[2] for hit in rule_matches(folded(question), self.catalog.systems, self.concepts)}
        if (system_hits - {'workit'} or slots.get('system') not in (None, 'workit')
                or ('workit' not in system_hits and not (slots.get('system') == 'workit' and (
                    supplied or slots.get('workit_action'))))):
            return None
        surface = slots.get('surface')
        if not surface and len(supplied) == 1:
            surface = supplied[0]
        if any(spans(text, term) for term in ('thay toi', 'cho toi biet mat khau', 'mat khau hien tai',
                'tu duyet', 'duyet giup', 'tu cap quyen', 'bo qua quyen', 'toan bo phieu',
                'tao va gui ngay', 'tao va giao ngay', 'system prompt', 'ignore', 'xem phieu that',
                'liet ke ngay', 'tai gia', 'hsm key cua')):
            if surface:
                return WorkitResolution(None, clarification=(
                    'Mình có thể hướng dẫn thao tác Workit theo quyền của bạn. Bạn muốn xem hướng dẫn bước nào?'))
            return None
        preferred = slots.get('workit_action') if surface else None
        if preferred:
            if preferred not in self._workit:
                return WorkitResolution(preferred, surface=surface, unavailable=(surface,))
            action = preferred
        else:
            padded = f' {text} '
            scores = {}
            for key, choices in self._workit.items():
                scores[key] = max(_route_score(route, padded) for route in choices.values())
            best = max(scores.values(), default=0)
            winners = [key for key, score in scores.items() if score == best] if best else []
            if len(winners) != 1:
                if surface or len(supplied) > 1:
                    return WorkitResolution(None, surface=surface, clarification=(
                        'Bạn cần hướng dẫn thao tác nào trên Workit? Hãy nêu tên chức năng hoặc việc cần làm.'))
                return None
            action = winners[0]
        choices = self._workit[action]
        if surface:
            route = choices.get(surface)
            return WorkitResolution(action, (route,) if route else (), surface,
                                    unavailable=() if route else (surface,))
        if len(supplied) > 1:
            if any(spans(text, term) for term in ('so sanh', 'khac gi', 'khac nhau', 'ca hai', 'va', 'voi')):
                return WorkitResolution(action, tuple(choices.values()),
                                        unavailable=tuple(key for key in supplied if key not in choices))
            return WorkitResolution(action, clarification=SURFACE_PROMPT)
        guides = tuple(route for route in choices.values() if not route.surface_limited)
        if len(guides) > 1:
            return WorkitResolution(action, clarification=SURFACE_PROMPT)
        return WorkitResolution(action, guides or tuple(choices.values()))

    def analyze(self, question):
        if self.catalog is None:
            return Query()
        from .language import positive_question
        from .reranking import query_variants
        question = query_variants(question)[-1]
        text = folded(positive_question(question))
        hits = {name: rule_matches(text, getattr(self.catalog, name), self.concepts)
                for name in ('families','systems','subtypes','operations')}
        # Business topics and software platforms are separate axes. A longer
        # software-specific phrase must not erase the underlying leave family.
        legacy_hits = rule_matches(text, [r for r in self.catalog.families
                                   if r.id in {'leave', 'resignation', 'attendance'}], self.concepts)
        if legacy_hits:
            hits['families'] = legacy_hits
        values = {name: {hit[2] for hit in rows} for name, rows in hits.items()}
        families, systems, subtypes, operations = (values[n] for n in ('families','systems','subtypes','operations'))
        days, sessions, granularity, bad_duration = duration(text)
        # Ignore negation inside an explicitly recognized unpaid-leave phrase.
        remaining = text
        for start, end, _ in sorted(hits['subtypes'], reverse=True):
            remaining = remaining[:start]+' '*(end-start)+remaining[end:]
        negative = bool(spans(remaining, 'khong') or spans(remaining, 'chua') or spans(remaining, 'ngoai tru'))
        unclear_resignation = bool(spans(text,'nghi viec') and not spans(text,'thoi viec') and 'personal' not in subtypes and not any(
            identity=='resignation' and end-start>len('nghi viec') for start,end,identity in hits['families']))
        ambiguous = (len(families)>1 or len(systems)>1 or len(subtypes)>1 or bad_duration or negative
                     or unclear_resignation
                     or any(spans(text,p) for p in ('tong','so sanh','toi co duoc','con bao nhieu','bao nhieu loai')))
        # Asking policy/approval/cancellation is more specific than incidental
        # "xin nghỉ" wording. Conflicting non-request operations ask for detail.
        explicit = operations-{'request'}
        ambiguous = ambiguous or len(explicit)>1
        platform_requested = bool(systems) or any(spans(text, term) for rule in self.catalog.families
            if rule.id in families for term in rule.platform_terms)
        return Query(next(iter(families)) if len(families)==1 else None,
                     next(iter(subtypes)) if len(subtypes)==1 else None,
                     next(iter(systems)) if len(systems)==1 else None,
                     next(iter(explicit)) if len(explicit)==1 else 'request',
                     days,sessions,granularity,ambiguous,'leave' in families or unclear_resignation,platform_requested)

    def resolve(self, question, *, slots=None):
        if self.catalog is not None:
            query = self.analyze(question)
            if any(spans(folded(question), term) for rule in self.catalog.families
                   if rule.id == query.family for term in rule.execution_terms):
                return Resolution('clarify', message='Mình chỉ tra cứu và hướng dẫn, không gửi hoặc phê duyệt thay bạn. Bạn muốn xem quy định hay các bước thực hiện?')
        if slots and self.analyze(question).involves_leave and (
                slots.get('intent') or any(key in slots and slots[key] != getattr(self.analyze(question), key)
                                          for key in ('subtype', 'system', 'days', 'sessions'))):
            return self.resolve_leave(question, slots=slots)
        generic = self.resolve_generic(question, slots=slots)
        if generic.state != 'not_applicable':
            return generic
        return self.resolve_leave(question, slots=slots)

    @staticmethod
    def apply_slots(query, slots):
        if not slots:
            return query
        query = replace(query, **{key: value for key, value in slots.items()
            if key in ('system', 'subtype', 'days', 'sessions', 'granularity')})
        intent = slots.get('intent')
        if intent in ('submission', 'procedure'):
            query = replace(query, operation='request', platform_requested=True)
        elif intent == 'policy':
            query = replace(query, operation='policy', platform_requested=False)
        elif slots.get('system'):
            query = replace(query, platform_requested=True)
        return query

    def structured_family(self, family):
        return bool(self.catalog and any(route.family == family and not route.aliases and not route.concept_ids and not route.question_terms
                                        for route in self.catalog.routes))

    def resolve_generic(self, question, *, slots=None):
        if self.catalog is None:
            return Resolution()
        from .language import positive_question
        from .reranking import query_variants
        question = query_variants(question)[-1]
        text = folded(positive_question(question))
        hits = []
        for route in self._generic:
            if route.requires_any and not any(spans(text, p) for p in route.requires_any):
                continue
            if any(spans(text, p) for p in route.excludes_any):
                continue
            phrases = list(route.aliases)
            for key in route.concept_ids:
                concept = self.concepts[key]
                if concept['requires_any'] and not any(spans(text, p) for p in concept['requires_any']):
                    continue
                if any(spans(text, p) for p in concept['excludes_any']):
                    continue
                phrases.extend([concept['canonical'], *concept['aliases']])
            for phrase in phrases:
                hits.extend((start, end, route) for start, end in spans(text, phrase))
        hits = [hit for hit in hits if not any(other[0] < hit[1] and other[1] > hit[0]
                and other[1]-other[0] > hit[1]-hit[0] for other in hits)]
        routes = {hit[2].task_id: hit[2] for hit in hits}
        if not routes:
            return Resolution()
        original = self.analyze(question)
        replacement_system = (slots or {}).get('system')
        if replacement_system and replacement_system != original.system:
            # A platform tied to the original wording cannot authorize its
            # instructions on the replacement platform. Use the route or its
            # source bindings to check platform-specific coverage.
            bound_systems = {key: set() for key in routes}
            for binding in self.catalog.bindings:
                if binding.system:
                    for key in bound_systems.keys() & set(binding.task_ids):
                        bound_systems[key].add(binding.system)
            routes = {key: route for key, route in routes.items() if (
                replacement_system in ({route.system} if route.system else bound_systems[key])
                or not (original.system or route.system or bound_systems[key]))}
            if not routes:
                return Resolution('unsupported', message='Chưa có hướng dẫn tương ứng trên hệ thống bạn vừa chọn. '
                                  'Bạn hãy nêu rõ thao tác hoặc yêu cầu tìm tài liệu của hệ thống đó.')
        # Only a literal source identifier may disambiguate otherwise identical
        # tasks. Neither filename age nor a clarification memo chooses policy.
        selected = [r for r in routes.values() if any(
            ref.ref.document_id in question or ref.ref.metadata['source_path'] in question
            for ref in self.catalog.bindings if r.task_id in ref.task_ids)]
        if len(selected) == 1:
            routes = {selected[0].task_id: selected[0]}
        remaining = list(text)
        for start, end, _ in hits:
            remaining[start:end] = ' ' * (end-start)
        remaining = ''.join(remaining)
        unsafe = any(spans(remaining, p) for p in ('khong', 'chua', 'ngoai tru', 'so sanh',
            'tong cong', 'toi co duoc', 'truong hop cua toi', 'toi thu viec',
            'lam giup toi', 'gui giup toi', 'duyet giup toi', 'hay bo qua', 'ignore',
            'tinh chinh xac', 'hay tinh', 'tao va gui ngay', 'thay toi', 'tu ket luan'))
        unmatched_platforms = rule_matches(remaining, self.catalog.systems, self.concepts)
        unsafe = unsafe or bool(unmatched_platforms and (spans(text,'va') or ';' in question))
        if unmatched_platforms:
            platforms = {hit[2] for hit in rule_matches(text, self.catalog.systems, self.concepts)}
            unsafe = unsafe or len(platforms) > 1
        unsafe = unsafe or any(spans(text,p) for p in ('tu gui', 'noi dung email', 'mat khau hien tai',
                                                       'bo qua quyen', 'tu cap quyen'))
        if unsafe or len(routes) != 1:
            return Resolution('clarify', message='Bạn muốn hỏi riêng nội dung hoặc nguồn nào? '
                              'Vui lòng nêu rõ đối tượng, thao tác và nguồn cần dùng.',
                              candidates=tuple(sorted(routes)), requires_restatement=unsafe)
        route = next(iter(routes.values()))
        query = self.apply_slots(original, slots)
        if route.family == 'leave' and (len({hit[2] for hit in rule_matches(text, self.catalog.subtypes, self.concepts)}) > 1
                                        or duration(text)[3]):
            return Resolution('clarify', message='Bạn hãy nêu riêng loại nghỉ và khoảng thời gian cần hỏi.',
                              candidates=(route.task_id,), requires_restatement=True)
        missing = tuple(slot for slot in route.required_slots if getattr(query, slot) is None)
        if missing:
            return Resolution('clarify', message=route.prompt, candidates=(route.task_id,), missing_slots=missing)
        return Resolution('task', route.task_id)

    def resolve_leave(self, question, *, slots=None):
        query = self.analyze(question)
        query = self.apply_slots(query, slots)
        # General guidance does not depend on choosing a software platform.
        if query.operation == 'request' and query.system is None and not query.platform_requested:
            query = replace(query, operation='policy')
        if not query.involves_leave:
            return Resolution()
        if query.ambiguous:
            return Resolution('clarify', message='Bạn đang hỏi thủ tục xin nghỉ, đi trễ/về sớm hay thôi việc? '
                              'Vui lòng nêu riêng nội dung và trường hợp cần hướng dẫn.')
        duration_text = f' trong {query.days:g} ngày' if query.days is not None else (
            f' trong {query.sessions:g} buổi' if query.sessions is not None else '')
        if query.subtype is None:
            message = 'Bạn muốn đăng ký nghỉ phép hay nghỉ không lương'+duration_text+'?'
            return Resolution('clarify', message=message, missing_slots=('subtype',))
        if query.system is None and query.operation != 'policy':
            return Resolution('clarify', message='Bạn đang dùng Workit hay E-Leave để đăng ký nghỉ'+duration_text+'?',
                              missing_slots=('system',))
        routes = [r for r in self.catalog.routes if not r.aliases and not r.concept_ids
                  and r.family == 'leave' and r.subtype==query.subtype and r.operation==query.operation
                  and r.system==(None if query.operation=='policy' else query.system)]
        routes = [r for r in routes if r.granularity==query.granularity] or [r for r in routes if r.granularity is None]
        if len(routes)!=1:
            return Resolution('unsupported', message='Mình đã nhận ra yêu cầu xin nghỉ, nhưng chưa có mẫu đủ '
                              'bằng chứng cho loại nghỉ hoặc thao tác này trên hệ thống bạn chọn. '
                              'Bạn hãy nêu rõ thao tác cần hỏi hoặc yêu cầu tìm tài liệu tương ứng.')
        return Resolution('task', routes[0].task_id)

    def reorder(self, question, matches):
        scope = self.resolve_workit(question)
        if scope and scope.surface and scope.routes:
            from src.answering.pipeline import matches_reference
            def surface_key(match):
                address = (match.get('document_id'), match.get('revision_id'), match.get('chunk_index'))
                bindings = [b for b in self._bindings.get(address, ()) if b.surface and matches_reference(b.ref, match)]
                if any(b.surface == scope.surface and scope.routes[0].task_id in b.task_ids for b in bindings):
                    return 0
                return 1 if any(b.surface == scope.surface for b in bindings) else 2
            return sorted(matches, key=surface_key)
        query = self.analyze(question)
        generic = self.resolve_generic(question)
        if generic.state == 'task':
            from src.answering.pipeline import matches_reference, requested_aspects
            aspects = set(requested_aspects(question))
            def generic_key(match):
                bindings = self._bindings.get((match.get('document_id'), match.get('revision_id'), match.get('chunk_index')), [])
                if not isinstance(bindings, list):
                    bindings = [bindings]
                valid = [b for b in bindings if matches_reference(b.ref, match)]
                relevant = [b for b in valid if generic.task_id in b.task_ids]
                if relevant:
                    roles = ({'system_procedure': 0, 'general_procedure': 1, 'policy': 2, 'session_case': 3, 'special_case': 4}
                             if 'procedure' in aspects else
                             {'policy': 0, 'general_procedure': 1, 'system_procedure': 2, 'session_case': 3, 'special_case': 4})
                    return min((0, 0 if not b.aspects or aspects.intersection(b.aspects) else 1,
                                0 if b.system == query.system else 1, roles[b.role]) for b in relevant)
                return (2 if valid else 1, 0, 0, 0)
            return sorted(matches, key=generic_key)
        if generic.state != 'not_applicable' or self.catalog is None or query.family != 'leave' or query.ambiguous:
            return matches
        from src.answering.pipeline import matches_reference

        def key(match):
            binding = self._bindings.get((match.get('document_id'),match.get('revision_id'),match.get('chunk_index')))
            if isinstance(binding, list):
                binding = next((b for b in binding if b.family == query.family and matches_reference(b.ref, match)),
                               binding[0] if binding else None)
            if binding is None or not matches_reference(binding.ref, match):
                return (1,0,0,0,0,0)
            if binding.family != query.family:
                return (2,0,0,0,0,0)
            subtype = 1 if query.subtype and binding.subtype and binding.subtype!=query.subtype else 0
            operation = 0 if (binding.operation==query.operation or
                              binding.role=='general_procedure' and query.operation in ('request','policy')) else 1
            system = (0 if binding.system==query.system else 1 if binding.system is None else 2) if query.system else 0
            if query.granularity=='session':
                role = {'session_case':0,'system_procedure':1,'general_procedure':2,'policy':3,'special_case':4}[binding.role]
            elif query.system and query.operation!='policy':
                role = {'system_procedure':0,'general_procedure':1,'policy':2,'session_case':3,'special_case':4}[binding.role]
            else:
                role = {'general_procedure':0,'policy':1,'system_procedure':2,'session_case':3,'special_case':4}[binding.role]
            duration_match = 0 if query.days is not None and any(
                r.minimum <= query.days and (r.maximum is None or query.days <= r.maximum)
                for r in binding.duration_ranges) else 1
            return (0,subtype,operation,system,role,duration_match)

        # Stable sort keeps raw-reranker/vector order within an equal bucket.
        return sorted(matches, key=key)

    def references(self, question):
        """Declared, bounded bundles only; no broad-topic evidence injection."""
        scope = self.resolve_workit(question)
        surface = None
        if scope and scope.surface:
            if not scope.routes:
                return []
            surface, task = scope.surface, scope.routes[0].task_id
        else:
            resolution = self.resolve(question)
            if resolution.state != 'task':
                return []
            task = resolution.task_id
        from src.answering.pipeline import requested_aspects
        aspects = set(requested_aspects(question))
        refs = {}
        for binding in self.catalog.bindings:
            if (task in binding.task_ids and (not surface or binding.surface == surface)
                    and (not binding.aspects or aspects.intersection(binding.aspects))):
                ref = binding.ref
                refs[(ref.document_id, ref.revision_id, ref.chunk_index)] = ref
        return list(refs.values()) if len(refs) <= 10 else []

    def close(self):
        self.catalog = None
        self.concepts = {}
        self._bindings = {}
        self._generic = []
        self.fingerprint = None
        self._focused = {}
        self._workit = {}
        self._office = ()
