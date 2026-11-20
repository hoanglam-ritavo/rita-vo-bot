"""One conservative request analysis shared by HTTP and conversation consumers."""
from dataclasses import dataclass
from pathlib import PurePosixPath
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
    document_clarification: str | None = None
    parts: tuple[str, ...] = ()
    general_guidance: bool = False
    personal_request: bool = False
    choice_options: tuple[tuple[str, str], ...] = ()
    focus_unit: str | None = None
    check_scopes: bool = False
    refresh_choices: bool = False
    leave_platforms: tuple[tuple[str, str | None], ...] = ()
    scope_intro: str | None = None
    scope_limits: tuple[str, ...] = ()
    scope_system: str | None = None
    recognized_topic: bool = False
    topic_unmatched: bool = False
    discovery_options: bool = False
    focused_policy: bool = False


def choice_labels(cards, *, include_filenames=False):
    def duplicate_labels(options):
        labels = [normalized(label) for _, label in options]
        return {label for label in labels if labels.count(label) > 1}

    options = [(card.id, card.label.strip()) for card in cards]
    duplicates = duplicate_labels(options)
    if duplicates:
        options = []
        for card in cards:
            label = card.label.strip()
            if normalized(label) in duplicates:
                sources = sorted({', '.join(filter(None, (ref.metadata.get('title'),
                    ref.metadata.get('version_label'), ref.metadata.get('applicability'))))
                    for section in getattr(card, 'sections', {}).values()
                    for unit in section.units for ref in unit.refs} - {''})
                if sources:
                    label = (label + ' — ' + ' / '.join(sources))[:240].rstrip()
            options.append((card.id, label))
    duplicates = duplicate_labels(options)
    if duplicates and include_filenames:
        for index, (task_id, label) in enumerate(options):
            if normalized(label) not in duplicates:
                continue
            card = cards[index]
            files = sorted({PurePosixPath(ref.metadata['source_path'].replace('\\', '/')).name
                            for section in card.sections.values() for unit in section.units
                            for ref in unit.refs if ref.metadata.get('source_path')})
            if files:
                suffix = ' — ' + ' / '.join(files)[:180]
                options[index] = (task_id, card.label.strip()[:240 - len(suffix)].rstrip() + suffix)
    return tuple(options) if not duplicate_labels(options) and all(label for _, label in options) else ()


def unknown_topic_prompt(question, policy):
    query = policy.analyze(question) if policy is not None else None
    systems = {'workit': 'Workit', 'workit-mobile': 'Workit Mobile', 'eleave': 'E-Leave',
               'epostal': 'E-Postal', 'eworkdate': 'E-Workdate', 'outlook': 'Outlook', 'bravo': 'Bravo'}
    families = {'vehicles': 'sử dụng xe', 'attendance': 'chấm công', 'travel': 'công tác phí',
                'administration': 'hành chính', 'benefits': 'phúc lợi', 'labor': 'nội quy lao động',
                'safety': 'an toàn nơi làm việc'}
    if query and query.system in systems:
        return f'Bạn cần hướng dẫn thao tác nào trên {systems[query.system]}? Hãy nêu tên chức năng hoặc việc cần làm.'
    if query and query.family in families:
        return f'Bạn muốn xem quy định hay cách thực hiện về {families[query.family]}? Hãy nêu việc cần làm.'
    return ('Bạn hãy nêu rõ chủ đề và việc cần làm. Nếu muốn xem nguồn, '
            'hãy bắt đầu câu hỏi bằng “tìm tài liệu” và tên chủ đề.')


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
               'nao', 'bao', 'nhieu', 'tien', 'muc', 'tro', 'thu', 'tuc', 'lam', 'the', 'quy', 'trinh',
               'chung', 'tu'}
    return removed and set(remainder.split()) <= allowed


def analyze_question(question, catalog=None, lexicon=None, policy=None, *, request=None):
    from .pipeline import select_cards, is_document_lookup, requested_aspects, resolve_policy
    from src.retrieval.language import positive_question
    from src.retrieval.policy import clarification_for
    from .slots import parse_inputs
    request = request or {}
    question = positive_question(question)
    query = normalized(question)
    lookup = is_document_lookup(question)
    registry = policy if policy is not None and catalog is not None and lexicon is not None else None
    focused = getattr(registry, 'focused_task', None)
    if not lookup and focused is not None and not request.get('source_id') and not any(
            phrase(query, marker) for marker in ('hay bo qua', 'bo qua quyen', 'ignore',
                                                'system prompt', 'mat khau hien tai')):
        selected = request.get('selected_task')
        if selected:
            task_ids = [selected] if registry.is_focused_task(selected) else []
        else:
            # Merge only recognized independent questions. A broad match must
            # not hide a separate unknown or personnel request.
            clauses = [part.strip() for part in re.split(r'[?!;\n]', question) if part.strip()]
            task_ids = [focused(clause) for clause in clauses] if 0 < len(clauses) <= 3 else []
        if task_ids and all(task_ids):
            indexed = {card.id: card for card in catalog.cards if card.routed_only}
            cards = tuple(indexed[key] for key in dict.fromkeys(task_ids) if key in indexed)
            if len(cards) == len(set(task_ids)):
                aspects = tuple(request.get('aspects') or ('overview',))
                references = {(ref.document_id, ref.revision_id, ref.chunk_index) for card in cards
                              for aspect in aspects if aspect in card.sections
                              for unit in card.sections[aspect].units for ref in unit.refs}
                if len(references) <= 10:
                    return RequestAnalysis(cards=cards, question=question, parts=('documents',),
                                           aspects=aspects, general_guidance=True, focused_policy=True)
    cards, ambiguous = ([], False) if lookup else select_cards(question, catalog, lexicon, policy,
        contextual=True) if catalog and lexicon else ([], False)
    resolution = None
    if not lookup and policy is not None and catalog and lexicon:
        resolution = (policy.resolve(question, slots=request['slots']) if request.get('slots') else
                      resolve_policy(question, catalog, lexicon, policy))
        if request.get('slots') and resolution is not None:
            cards = [card for card in catalog.cards if card.id == resolution.task_id or card.id in resolution.candidates]
            ambiguous = resolution.state == 'clarify'
    restatement_message = resolution.message if resolution and resolution.requires_restatement else None
    needs_restatement = bool(restatement_message)
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
    unknown_documents = []
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
                    'ai', 'thu', 'tuc', 'cac', 'buoc', 'lam', 'the', 'quy', 'trinh', 'chung', 'tu'}
                aspect_only = requested_aspects(clause) != ['overview'] and set(text.split()) <= aspect_words
                # Conjunctions and punctuation inside an authored task phrase
                # are not new clauses (e.g. "install and update").
                covered = _covered_fragment(text, query, cards, lexicon)
                if not (aspect_only or covered):
                    # Only a separate, recognized policy clause can leave an explicit self-fact answerable.
                    family, prompt = None, None
                    if registry is not None and len(clauses) > 1:
                        family = registry.analyze(clause).family
                        prompt = clarification_for(registry.catalog, clause, family)
                    if ((family or prompt) and route_question(clause).source == 'type_a'
                            and not any(phrase(text, term) for term in ('khong', 'chua', 'ngoai tru', *RESTRICTED))):
                        unknown_documents.append((clause, prompt))
                    else:
                        unknown = True
            else:
                independent_task = True
    clarification = None
    recognized_topic = False
    options = ()
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
    partial_profile = (len(unknown_documents) == 1 and bool(fields) and not (
        unknown or other or unclear or ambiguous_owner or negated or cards or ambiguous or lookup
        or needs_restatement or clarification or request.get('document_guidance')))
    document_clarification = None
    if partial_profile:
        clause, prompt = unknown_documents[0]
        message = prompt or unknown_topic_prompt(clause, policy)
        document_clarification = 'Về phần chính sách, ' + message[0].lower() + message[1:]
    shared_policy_scope = (bool(fields) and independent_task and len(cards) > 1
                           and len({card.label for card in cards}) == 1 and not needs_restatement)
    unresolved = (bool(fields or cards or lookup) and (unknown or bool(unknown_documents)) or
                  bool(fields) and (negated or (ambiguous and not shared_policy_scope)
                                    or policy_request and not cards and not lookup))
    operation = re.search(
        r'\b(?:so sanh|tinh chinh xac|tinh tong|cong tong|tong cong|'
        r'tong (?:muc|tien|so|chi phi|cac|khoan|ho tro|phu cap|tro cap)|'
        r'bao nhieu (?:muc|loai)|co may loai)\b', query)
    selected_context = getattr(cards[0], 'context', None) if len(cards) == 1 and not ambiguous else None
    supported_calculation = ((fields and all(field.endswith('_date') for field in fields))
                             or getattr(selected_context, 'mode', None) == 'calculation')
    if other:
        fields = []
    elif not lookup and operation and (operation[0] != 'tinh chinh xac' or not supported_calculation):
        clarification = 'Mình chưa hỗ trợ so sánh, cộng tổng hoặc tính chính xác cho yêu cầu này. Bạn có thể hỏi riêng từng quy định hoặc phần cần tra cứu.'
        needs_restatement = True
    elif restatement_message and ambiguous:
        clarification = restatement_message
    elif needs_restatement and request.get('selected_task'):
        clarification = 'Bạn hãy nêu lại phần muốn xem theo quy định chung, hoặc chỉ rõ trường hợp cần hỏi.'
    elif unclear or (unresolved and not partial_profile):
        clarification = 'Bạn hãy nêu rõ từng thông tin cần hỏi và đối tượng; mình chưa xác định đủ toàn bộ yêu cầu.'
    elif not cards and not lookup and not fields and not ambiguous and len(clauses) == 1 and registry is not None:
        family = registry.analyze(question).family
        clarification = clarification_for(registry.catalog, question, family)
        recognized_topic = bool(family or clarification)
    elif ambiguous:
        options = choice_labels(cards[:5])
        labels = [f'{index}. {label}' for index, (_, label) in enumerate(options, 1)]
        clarification = ('Bạn muốn hỏi nội dung nào? Hãy chọn số hoặc tên đầy đủ: ' + '; '.join(labels)
                         if labels else 'Bạn hãy nêu rõ chủ đề, đối tượng hoặc tài liệu bạn muốn hỏi.')
        if resolution is not None and resolution.state == 'clarify' and (
                getattr(resolution, 'missing_slots', ()) or not labels):
            clarification = resolution.message
            options = ()
    reason = ('leave_needs_clarification' if policy is not None and ambiguous and
              policy.analyze(question).involves_leave else 'task_needs_clarification')
    # Conversation reducers may deliberately discard inputs owned by a previous
    # task. Do not resurrect those values from the original stored question.
    inputs = dict(request['user_inputs']) if 'user_inputs' in request else parse_inputs(question)
    aspects = tuple(request.get('aspects') or requested_aspects(question))
    if (len(cards) == 1 and len(aspects) == 1 and not (ambiguous or clarification or other or fields)
            and not request.get('source_id')):
        related = getattr(cards[0], 'related_aspects', {}).get(aspects[0])
        if related:
            cards = [card for card in catalog.cards if card.id == related]
    unsupported = resolution is not None and resolution.state == 'unsupported' and not fields and not cards
    parts = tuple((['documents'] if lookup or cards or unsupported else []) +
                  (['profile'] if fields or unclear or ambiguous_owner else []) + (['unresolved'] if unresolved else []))
    check_scopes = (ambiguous and len(cards) > 1 and not needs_restatement and not unresolved
                    and not unclear and not other and (not fields or shared_policy_scope)
                    and not (resolution and resolution.missing_slots))
    leave_platforms = ()
    if (catalog and policy is not None and resolution is not None
            and resolution.missing_slots == ('system',) and not (other or fields or unresolved or needs_restatement)):
        platforms = ('workit', 'eleave')
        leave_platforms = tuple((system, policy.resolve_leave(
            question, slots={**request.get('slots', {}), 'system': system}).task_id) for system in platforms)
    leave_query = policy.analyze(question) if catalog and lexicon and policy is not None else None
    slots = request.get('slots', {})
    # A continuation explicitly asking where to submit needs only authored
    # instructions. Preserve established profiles for other standalone tasks.
    general_leave = (leave_query is not None and leave_query.involves_leave and (
        slots.get('intent') == 'submission' or
        (not leave_query.platform_requested and not slots.get('system'))))
    direct_fields = () if request.get('document_guidance') else tuple(dict.fromkeys(fields))
    return RequestAnalysis(cards=tuple(cards), ambiguous=ambiguous, direct_fields=direct_fields,
        document_lookup=lookup, unsupported_person=other, clarification=clarification, unresolved=unresolved,
        clarification_reason=reason, question=question, aspects=aspects,
        document_question=question if lookup and not fields else '; '.join(documents) or question, user_inputs=tuple(inputs.items()),
        missing_slots=tuple(resolution.missing_slots) if resolution is not None else (),
        document_message=resolution.message if unsupported else None,
        document_reason=('leave_task_unsupported' if policy.analyze(question).involves_leave else 'task_unsupported') if unsupported else None,
        parts=parts, personal_request=personal_request, choice_options=options, check_scopes=check_scopes,
        document_clarification=document_clarification, recognized_topic=recognized_topic,
        leave_platforms=leave_platforms,
        general_guidance=bool(request.get('document_guidance') or general_leave and
            not any(phrase(query, term) for term in ('cua toi', 'cua minh', 'truong hop cua toi', 'toi co duoc'))))
