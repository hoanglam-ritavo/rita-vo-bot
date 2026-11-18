"""Source-bound task hints, not learned relevance or answer authorization."""
from dataclasses import dataclass
import hashlib
import logging
from pathlib import Path
import re
from typing import Literal
import unicodedata

from pydantic import Field, model_validator
from src.answering.catalog import Reference, METADATA
from src.answering.contracts import Strict
from src.paths import REPOSITORY_ROOT
from src.semantics.corpus import decode_json

logger = logging.getLogger(__name__)
DEFAULT_PATH = REPOSITORY_ROOT / 'artifacts/retrieval/catalog.json'
DEFAULT_LEXICON = REPOSITORY_ROOT / 'artifacts/semantics/lexicon.json'
Family = Literal['leave', 'resignation', 'attendance']
System = Literal['workit', 'eleave']
Subtype = Literal['annual', 'unpaid', 'maternity', 'sick', 'personal']
Operation = Literal['request', 'policy', 'approval', 'cancel', 'support']


def folded(text):
    text = unicodedata.normalize('NFD', text.casefold()).replace('đ', 'd')
    text = ''.join(c for c in text if not unicodedata.combining(c))
    return ' '.join(re.sub(r'[^a-z0-9/.,]+', ' ', text).split())


def spans(text, phrase):
    return [m.span() for m in re.finditer(r'(?<!\w)' + re.escape(folded(phrase)) + r'(?!\w)', text)]


class Rule(Strict):
    id: str = Field(pattern=r'^[a-z0-9.-]{1,100}$')
    concept_ids: list[str] = Field(min_length=1)


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
    ref: Reference

    @model_validator(mode='after')
    def source_shape(self):
        if set(self.ref.metadata) != set(METADATA) or not self.ref.quote.strip():
            raise ValueError('invalid_policy_reference')
        return self


class Route(Strict):
    id: str = Field(pattern=r'^[a-z0-9.-]{1,100}$')
    subtype: Subtype
    operation: Operation
    system: System | None
    granularity: Literal['day','session'] | None = None
    task_id: str = Field(pattern=r'^[a-z0-9.-]{1,100}$')


class Catalog(Strict):
    schema_version: Literal[1]
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
        addresses = [(b.ref.document_id, b.ref.revision_id, b.ref.chunk_index) for b in self.bindings]
        routes = [(r.subtype, r.operation, r.system, r.granularity) for r in self.routes]
        if len(set(addresses)) != len(addresses) or len(set(routes)) != len(routes):
            raise ValueError('ambiguous_policy_binding')
        for name, allowed in (('families', {'leave','resignation','attendance'}),
                              ('systems', {'workit','eleave'}),
                              ('subtypes', {'annual','unpaid','maternity','sick','personal'}),
                              ('operations', {'request','policy','approval','cancel','support'})):
            if any(r.id not in allowed for r in getattr(self, name)):
                raise ValueError('invalid_policy_rule')
        return self


def validate_catalog(data, lexicon, lexical_hash, answers=None):
    catalog = Catalog.model_validate(data)
    concepts = {c['id'] for c in lexicon['concepts']}
    if catalog.lexicon_sha256 != lexical_hash:
        raise ValueError('retrieval_lexicon_mismatch')
    for collection in ('families','systems','subtypes','operations'):
        if any(not set(rule.concept_ids) <= concepts for rule in getattr(catalog, collection)):
            raise ValueError('unknown_policy_concept')
    if answers is not None and any(r.task_id not in {c.id for c in answers.cards} for r in catalog.routes):
        raise ValueError('unknown_policy_task')
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


@dataclass(frozen=True)
class Resolution:
    state: str = 'not_applicable'
    task_id: str | None = None
    message: str = ''


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
    session_words = any(spans(text, p) for p in ('buoi sang','buoi chieu','nghi sang','nghi chieu','nua ngay'))
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
            self._bindings = {(b.ref.document_id,b.ref.revision_id,b.ref.chunk_index): b for b in catalog.bindings}
            self.fingerprint = hashlib.sha256(raw).hexdigest()
        except Exception:
            self.close()
            logger.warning('retrieval_policy state=unavailable')

    def analyze(self, question):
        if self.catalog is None:
            return Query()
        text = folded(question)
        hits = {name: rule_matches(text, getattr(self.catalog, name), self.concepts)
                for name in ('families','systems','subtypes','operations')}
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
        return Query(next(iter(families)) if len(families)==1 else None,
                     next(iter(subtypes)) if len(subtypes)==1 else None,
                     next(iter(systems)) if len(systems)==1 else None,
                     next(iter(explicit)) if len(explicit)==1 else 'request',
                     days,sessions,granularity,ambiguous,'leave' in families or unclear_resignation)

    def resolve(self, question):
        query = self.analyze(question)
        if not query.involves_leave:
            return Resolution()
        if query.ambiguous:
            return Resolution('clarify', message='Bạn đang hỏi thủ tục xin nghỉ, đi trễ/về sớm hay thôi việc? '
                              'Vui lòng nêu riêng nội dung và trường hợp cần hướng dẫn.')
        duration_text = f' trong {query.days:g} ngày' if query.days is not None else (
            f' trong {query.sessions:g} buổi' if query.sessions is not None else '')
        if query.subtype is None:
            message = 'Bạn muốn đăng ký nghỉ phép hay nghỉ không lương'+duration_text+'?'
            if query.system is None and query.operation != 'policy':
                message += ' Bạn đang dùng Workit hay E-Leave để gửi đơn?'
            return Resolution('clarify', message=message)
        if query.system is None and query.operation != 'policy':
            return Resolution('clarify', message='Bạn đang dùng Workit hay E-Leave để đăng ký nghỉ'+duration_text+'?')
        routes = [r for r in self.catalog.routes if r.subtype==query.subtype and r.operation==query.operation
                  and r.system==(None if query.operation=='policy' else query.system)]
        routes = [r for r in routes if r.granularity==query.granularity] or [r for r in routes if r.granularity is None]
        if len(routes)!=1:
            return Resolution('unsupported', message='Mình đã nhận ra yêu cầu xin nghỉ, nhưng chưa có mẫu đủ '
                              'bằng chứng cho loại nghỉ hoặc thao tác này trên hệ thống bạn chọn. '
                              'Bạn hãy nêu rõ thao tác cần hỏi hoặc yêu cầu tìm tài liệu tương ứng.')
        return Resolution('task', routes[0].task_id)

    def reorder(self, question, matches):
        query = self.analyze(question)
        if self.catalog is None or query.family != 'leave' or query.ambiguous:
            return matches
        from src.answering.pipeline import matches_reference

        def key(match):
            binding = self._bindings.get((match.get('document_id'),match.get('revision_id'),match.get('chunk_index')))
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

    def close(self):
        self.catalog = None
        self.concepts = {}
        self._bindings = {}
        self.fingerprint = None
