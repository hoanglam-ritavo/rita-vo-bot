"""One conservative request analysis shared by HTTP and conversation consumers."""
from dataclasses import dataclass
import re

from src.type_b.tasks import TASKS, POLICY, normalized, phrase, route_question


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
        for part in re.split(r'\b(?:va|dong thoi|ngoai ra|kem|cung nhu)\b', alias):
            part = part.strip()
            if part and phrase(remainder, part):
                remainder = re.sub(r'(?<!\w)' + re.escape(part) + r'(?!\w)', ' ', remainder)
                removed = True
    allowed = {'thong', 'tin', 've', 'noi', 'dung', 'toi', 'muon', 'hoi', 'xem', 'doc', 'tim',
               'hieu', 'biet', 'cac', 'buoc', 'thuc', 'hien', 'la', 'gi', 'dieu', 'kien',
               'ap', 'dung', 'cho', 'ai', 'can', 'giay', 'to', 'ho', 'so', 'gom', 'nhung',
               'nao', 'bao', 'nhieu', 'tien', 'muc', 'tro', 'thu', 'tuc', 'lam', 'the', 'quy', 'trinh'}
    return removed and set(remainder.split()) <= allowed


def analyze_question(question, catalog=None, lexicon=None, policy=None):
    from .pipeline import select_cards, is_document_lookup, requested_aspects
    from src.retrieval.language import positive_question
    from .slots import parse_inputs
    lookup = is_document_lookup(question)
    # A reference to the reader in a document query does not authorize a profile read.
    if lookup:
        return RequestAnalysis(document_lookup=True)
    question = positive_question(question)
    query = normalized(question)
    cards, ambiguous = select_cards(question, catalog, lexicon, policy,
        contextual=True) if catalog and lexicon else ([], False)
    clauses = []
    for segment in re.split(r'[;\n]', question):
        if parse_inputs(segment):
            clauses.append(segment)
        else:
            clauses.extend(part.strip() for part in re.split(
                r'[?!]|(?<!\d)\.(?!\d)|\b(?:và|va|đồng thời|dong thoi|ngoài ra|ngoai ra|kèm|kem|cũng như|cung nhu)\b',
                segment, flags=re.IGNORECASE) if part.strip())
    fields, other, unclear = [], False, False
    unknown = False
    independent_task = False
    negated = False
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
        aliases = [task for task in TASKS if any(phrase(text, alias) for alias in task.aliases)]
        manager_email = any(phrase(text, term) for term in ('email quan ly', 'email cua quan ly'))
        identity = text in ('toi la ai', 'toi ten gi')
        personal = any(phrase(text, term) for term in ('toi', 'minh', 'ban than'))
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
    if fields and not policy_request and not independent_task:
        cards = []
        ambiguous = False
    if cards and not fields:
        unclear = False
    unresolved = (bool(fields or cards) and unknown or
                  bool(fields) and (negated or ambiguous or policy_request and not cards))
    if other:
        fields = []
    elif unclear or unresolved:
        clarification = 'Bạn hãy nêu rõ từng thông tin cần hỏi và đối tượng; mình chưa xác định đủ toàn bộ yêu cầu.'
    elif ambiguous:
        labels = [f'{index}. {card.label}' for index, card in enumerate(cards[:5], 1)]
        clarification = ('Bạn muốn hỏi nội dung nào? Hãy chọn số hoặc tên đầy đủ: ' + '; '.join(labels)
                         if labels else 'Bạn hãy nêu rõ chủ đề, đối tượng và thông tin cần hỏi.')
        if policy is not None:
            resolution = policy.resolve(question)
            if resolution.state == 'clarify' and (getattr(resolution, 'missing_slots', ()) or not labels):
                clarification = resolution.message
    reason = ('leave_needs_clarification' if policy is not None and ambiguous and
              policy.analyze(question).involves_leave else 'task_needs_clarification')
    return RequestAnalysis(tuple(cards), ambiguous, tuple(dict.fromkeys(fields)), False,
                           other, clarification, unresolved, reason)
