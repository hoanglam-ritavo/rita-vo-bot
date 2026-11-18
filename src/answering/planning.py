"""One conservative request analysis shared by HTTP and conversation consumers."""
from dataclasses import dataclass
import re
import unicodedata

from src.type_b.tasks import TASKS, POLICY, RESTRICTED, normalized, phrase, route_question


@dataclass(frozen=True)
class RequestAnalysis:
    cards: tuple = ()
    ambiguous: bool = False
    direct_fields: tuple[str, ...] = ()
    document_lookup: bool = False
    unsupported_person: bool = False
    clarification: str | None = None
    unresolved: bool = False
    clarification_reason: str = 'task_needs_clarification'
    question: str = ''
    aspects: tuple[str, ...] = ('overview',)
    document_question: str = ''
    user_inputs: tuple[tuple[str, str], ...] = ()
    missing_slots: tuple[str, ...] = ()
    document_message: str | None = None
    document_reason: str | None = None
    parts: tuple[str, ...] = ()
    general_guidance: bool = False
    personal_request: bool = False


def _covered_fragment(text, query, cards, lexicon):
    """Allow discourse/aspect words around pieces of an authored conjunction."""
    concepts = {item['id']: item for item in (lexicon or {}).get('concepts', [])}
    phrases = [alias for card in cards for alias in card.aliases]
    for card in cards:
        for key in card.concept_ids:
            concept = concepts.get(key, {})
            phrases.extend([concept.get('canonical', ''), *concept.get('aliases', [])])
    remainder, removed = text, False
    for alias in sorted({normalized(alias) for alias in phrases if alias}, key=len, reverse=True):
        if not phrase(query, alias):
            continue
        for part in re.split(r'\b(?:va|dong thoi|ngoai ra|kem|cung nhu|cung)\b', alias):
            part = part.strip()
            if part and phrase(remainder, part):
                remainder = re.sub(r'(?<!\w)' + re.escape(part) + r'(?!\w)', ' ', remainder)
                removed = True
    allowed = {'thong', 'tin', 've', 'noi', 'dung', 'toi', 'muon', 'hoi', 'xem', 'doc', 'tim',
               'hieu', 'biet', 'cac', 'buoc', 'thuc', 'hien', 'la', 'gi', 'dieu', 'kien',
               'ap', 'dung', 'cho', 'ai', 'can', 'giay', 'to', 'ho', 'so', 'gom', 'nhung',
               'nao', 'bao', 'nhieu', 'tien', 'muc', 'tro', 'thu', 'tuc', 'lam', 'the', 'quy', 'trinh'}
    return removed and set(remainder.split()) <= allowed


def analyze_question(question, catalog=None, lexicon=None, policy=None, *, request=None):
    from .pipeline import select_cards, is_document_lookup, requested_aspects, resolve_policy
    from src.retrieval.language import positive_question
    from .slots import parse_inputs
    request = request or {}
    question = positive_question(question)
    query = normalized(question)
    lookup = is_document_lookup(question)
    cards, ambiguous = ([], False) if lookup else select_cards(question, catalog, lexicon, policy,
        contextual=True) if catalog and lexicon else ([], False)
    resolution = None
    if not lookup and policy is not None and catalog and lexicon:
        resolution = (policy.resolve(question, slots=request['slots']) if request.get('slots') else
                      resolve_policy(question, catalog, lexicon, policy))
        if request.get('slots') and resolution is not None:
            cards = [card for card in catalog.cards if card.id == resolution.task_id or card.id in resolution.candidates]
            ambiguous = resolution.state == 'clarify'
    if request.get('selected_task') and catalog is not None:
        cards = [card for card in catalog.cards if card.id == request['selected_task']]
        ambiguous = not bool(cards)
        resolution = None
    clauses = []
    for segment in re.split(r'[;\n]', question):
        if parse_inputs(segment):
            clauses.append(segment)
        else:
            clauses.extend(part.strip() for part in re.split(
                r'[?!]|(?<!\d)\.(?!\d)|\b(?:và|va|đồng thời|dong thoi|ngoài ra|ngoai ra|kèm|kem|cũng như|cung nhu|cùng|cung)\b',
                segment, flags=re.IGNORECASE) if part.strip())
    fields, other, unclear = [], False, False
    unknown = False
    independent_task = False
    negated = False
    ambiguous_owner = False
    documents = []
    personal_request = False
    fillers = {'cam on', 'xin cam on', 'cam on ban', 'toi muon biet', 'cho toi biet',
               'vui long', 'lam on', 'xin hoi'}
    for clause in clauses:
        text = normalized(clause)
        if parse_inputs(clause):
            continue
        if re.fullmatch(r'nguon [0-9a-f]{8}(?: [0-9a-f]{4}){3} [0-9a-f]{12}', text):
            continue  # Source selection is revalidated by policy/evidence readers.
        if text.startswith('toi khong hoi '):
            continue  # Explicitly rejected topics are not outstanding requests.
        if is_document_lookup(clause):
            documents.append(clause)
            continue
        aliases = [task for task in TASKS if any(phrase(text, alias) for alias in task.aliases)]
        manager_email = any(phrase(text, term) for term in ('email quan ly', 'email cua quan ly'))
        identity = text in ('toi la ai', 'toi ten gi')
        raw_clause = unicodedata.normalize('NFC', clause)
        personal = bool(re.search(r'\b(?:tôi|toi|mình)\b', raw_clause,
                                 flags=re.IGNORECASE)) or bool(request.get('self_reference'))
        personal_request |= personal
        local_ambiguous_owner = bool(re.search(r'\b(?:của|cua)\s+minh\b', raw_clause, flags=re.IGNORECASE))
        ambiguous_owner |= local_ambiguous_owner
        if local_ambiguous_owner:
            personal = False  # "Minh" and unaccented "minh" must not become "mình".
        if aliases and any(phrase(query, term) for term in ('cua toi', 'cua minh')) and any(
                text == alias for task in aliases for alias in task.aliases):
            personal = True  # "Chức danh và phòng ban của tôi" shares an owner.
        # Inspect the owner attached to each requested field, not a trailing "I want".
        named = bool(re.search(r'\bcua (?!(?:toi|minh|ban than|cong ty|tap doan|doanh nghiep|quan ly(?: truc tiep)? cua toi)\b)', text))
        named = named or any(phrase(text, term) for term in ('dong nghiep', 'nguoi khac'))
        manager_subject = bool(re.search(r'\bcua quan ly(?: truc tiep)? cua (?:toi|minh)\b', text))
        if manager_subject and any(task.field != 'manager_name' for task in aliases):
            named = True  # Only the declared manager name/email tasks are exposed.
        if aliases or manager_email or identity:
            # Unsupported personnel domains remain unresolved even without an
            # explicit conjunction (e.g. job title "with" a leave balance).
            date_request = bool(aliases) and all(task.field.endswith('_date') for task in aliases)
            for term in RESTRICTED:
                if not phrase(text, term):
                    continue
                # An explicit work-date anchor can be reported with missing
                # as-of/rule limitations; compose never certifies policy tenure.
                if date_request and term in ('bao nhieu ngay', 'bao nhieu thang', 'bao lau', 'tham nien'):
                    continue
                if manager_email and term == 'email':
                    continue
                if _covered_fragment(term, query, cards, lexicon):
                    continue
                unknown = True
            negated |= any(phrase(text, term) for term in ('khong', 'chua', 'ngoai tru'))
            other |= named
            if not named and personal:
                fields.extend(task.field for task in aliases if not manager_email or task.field != 'manager_name')
                if manager_email:
                    fields.append('manager_email')
                if identity:
                    fields.append('employee_name')
            elif not named:
                unclear = True
        elif text not in fillers:
            if lookup:
                if any(phrase(text, term) for term in ('cua toi', 'cua minh')):
                    unknown = True
                else:
                    documents.append(clause)
                continue
            clause_cards, clause_ambiguous = select_cards(clause, catalog, lexicon, policy,
                contextual=True) if catalog and lexicon else ([], False)
            if not clause_cards and not clause_ambiguous:
                # Labelled contextual inputs belong to the current task.
                aspect_words = {'can', 'giay', 'to', 'gi', 'ho', 'so', 'gom', 'nhung', 'nao',
                    'bao', 'nhieu', 'tien', 'muc', 'tro', 'dieu', 'kien', 'ap', 'dung', 'cho',
                    'ai', 'thu', 'tuc', 'cac', 'buoc', 'lam', 'the', 'quy', 'trinh'}
                aspect_only = requested_aspects(clause) != ['overview'] and set(text.split()) <= aspect_words
                # Conjunctions and punctuation inside an authored task phrase
                # are not new clauses (e.g. "install and update").
                covered = _covered_fragment(text, query, cards, lexicon)
                unknown |= not (bool(parse_inputs(clause)) or aspect_only or covered)
            else:
                independent_task = True
    clarification = None
    if route_question(question).reason == 'ambiguous_employment_date':
        clarification = 'Bạn muốn dùng ngày bắt đầu thử việc, kết thúc thử việc hay ngày chính thức?'
    # Preserve a recognized policy even when it shares a noun with a profile field.
    policy_request = any(phrase(query, term) for term in POLICY + ('phuc loi', 'ho tro', 'soan'))
    if fields and not policy_request and not independent_task and not request.get('selected_task'):
        cards = []
        ambiguous = False
    if cards and not fields and not ambiguous_owner:
        unclear = False
    if request.get('source_id') and cards:
        cards = [card for card in cards if any(ref.document_id == request['source_id']
            for section in card.sections.values() for unit in section.units for ref in unit.refs)]
        ambiguous = len(cards) != 1
        resolution = None
    unresolved = (bool(fields or cards or lookup) and unknown or
                  bool(fields) and (negated or ambiguous or policy_request and not cards and not lookup))
    if other:
        fields = []
    elif unclear or unresolved:
        clarification = 'Bạn hãy nêu rõ từng thông tin cần hỏi và đối tượng; mình chưa xác định đủ toàn bộ yêu cầu.'
    elif ambiguous:
        labels = [f'{index}. {card.label}' for index, card in enumerate(cards[:5], 1)]
        clarification = ('Bạn muốn hỏi nội dung nào? Hãy chọn số hoặc tên đầy đủ: ' + '; '.join(labels)
                         if labels else 'Bạn hãy nêu rõ chủ đề, đối tượng và thông tin cần hỏi.')
        if policy is not None:
            if resolution is not None and resolution.state == 'clarify' and (getattr(resolution, 'missing_slots', ()) or not labels):
                clarification = resolution.message
    reason = ('leave_needs_clarification' if policy is not None and ambiguous and
              policy.analyze(question).involves_leave else 'task_needs_clarification')
    inputs = {**parse_inputs(question), **request.get('user_inputs', {})}
    aspects = tuple(request.get('aspects') or requested_aspects(question))
    unsupported = resolution is not None and resolution.state == 'unsupported' and not fields and not cards
    parts = tuple((['documents'] if lookup or cards or unsupported else []) +
                  (['profile'] if fields or unclear or ambiguous_owner else []) + (['unresolved'] if unresolved else []))
    return RequestAnalysis(cards=tuple(cards), ambiguous=ambiguous, direct_fields=tuple(dict.fromkeys(fields)),
        document_lookup=lookup, unsupported_person=other, clarification=clarification, unresolved=unresolved,
        clarification_reason=reason, question=question, aspects=aspects,
        document_question=question if lookup and not fields else '; '.join(documents) or question, user_inputs=tuple(inputs.items()),
        missing_slots=tuple(resolution.missing_slots) if resolution is not None else (),
        document_message=resolution.message if unsupported else None,
        document_reason=('leave_task_unsupported' if policy.analyze(question).involves_leave else 'task_unsupported') if unsupported else None,
        parts=parts, personal_request=personal_request,
        general_guidance=bool(catalog and lexicon and policy is not None and policy.analyze(question).involves_leave and
            not any(phrase(query, term) for term in ('cua toi', 'cua minh', 'truong hop cua toi', 'toi co duoc'))))
