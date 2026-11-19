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
from src.semantics.corpus import decode_json

logger = logging.getLogger(__name__)
DEFAULT_PATH = REPOSITORY_ROOT / 'artifacts/retrieval/catalog.json'
DEFAULT_LEXICON = REPOSITORY_ROOT / 'artifacts/semantics/lexicon.json'
Family = str
System = str
Subtype = str
Operation = str


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
    prompt: str = 'Bạn cần hướng dẫn nội dung nào và trên hệ thống nào?'


class Catalog(Strict):
    schema_version: Literal[1, 2]
    package_fingerprint: str
    lexicon_sha256: str
    families: list[Rule]
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
                      tuple(b.task_ids), tuple(b.aspects)) for b in self.bindings]
        routes = [(r.family, r.subtype, r.operation, r.system, r.granularity)
                  for r in self.routes if not r.aliases and not r.concept_ids]
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
        for route in self.routes:
            if any(not p.strip() or len(p) > 240 for p in route.aliases + route.requires_any + route.excludes_any):
                raise ValueError('invalid_policy_phrase')
        return self


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
        for route in catalog.routes:
            for aspect, section in cards[route.task_id].sections.items():
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


class RetrievalPolicy:
    def __init__(self, path=DEFAULT_PATH, lexicon_path=DEFAULT_LEXICON):
        self.path, self.lexicon_path = Path(path), Path(lexicon_path)
        self.catalog = None
        self.concepts = {}
        self.fingerprint = None
        self._bindings = {}
        self._generic = []

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
            self._generic = [r for r in catalog.routes if r.aliases or r.concept_ids]
            self.fingerprint = hashlib.sha256(raw).hexdigest()
            logger.info('retrieval_policy state=ready')
        except Exception:
            self.close()
            logger.warning('retrieval_policy state=unavailable')

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
        return bool(self.catalog and any(route.family == family and not route.aliases and not route.concept_ids
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
        resolution = self.resolve(question)
        if resolution.state != 'task':
            return []
        from src.answering.pipeline import requested_aspects
        aspects = set(requested_aspects(question))
        refs = {}
        for binding in self.catalog.bindings:
            if resolution.task_id in binding.task_ids and (not binding.aspects or aspects.intersection(binding.aspects)):
                ref = binding.ref
                refs[(ref.document_id, ref.revision_id, ref.chunk_index)] = ref
        return list(refs.values()) if len(refs) <= 10 else []

    def close(self):
        self.catalog = None
        self.concepts = {}
        self._bindings = {}
        self._generic = []
        self.fingerprint = None
