"""Source-bound contextual composition. A profile is evidence, never a policy or instruction."""
from dataclasses import dataclass
from datetime import date, datetime
import re
from zoneinfo import ZoneInfo
import string

from .context_spec import ContextSpec
from .context_contracts import Application, MissingInput
from .calculations import calculate
from .slots import parse_inputs, SLOT_LABELS
from .planning import RequestAnalysis, analyze_question
from src.type_b.tasks import normalized, phrase

LABELS = {
    'employee_name': 'Họ tên', 'employee_code': 'Mã nhân viên', 'job_title': 'Chức danh',
    'job_role_name': 'Vai trò công việc', 'department_name': 'Phòng ban', 'work_email': 'Email công việc',
    'phone': 'Điện thoại công việc', 'manager_name': 'Quản lý trực tiếp', 'manager_email': 'Email quản lý',
    'probation_date': 'Ngày bắt đầu thử việc', 'probation_end_date': 'Ngày kết thúc thử việc',
    'official_date': 'Ngày chính thức', 'resignation_date': 'Ngày nghỉ việc',
    'employee_type': 'Loại nhân viên', 'contract_kind': 'Loại hợp đồng',
    'social_insurance_number': 'Số bảo hiểm xã hội', 'social_insurance_date': 'Ngày tham gia bảo hiểm xã hội',
    'health_insurance_number': 'Số bảo hiểm y tế', 'health_clinic': 'Nơi đăng ký khám chữa bệnh',
    'identification_id': 'Số giấy tờ định danh', 'id_issue_date': 'Ngày cấp giấy tờ',
    'id_issue_place': 'Nơi cấp giấy tờ', 'tax_id': 'Mã số thuế',
    'bank_account_number': 'Số tài khoản ngân hàng', 'bank_account_name': 'Tên chủ tài khoản',
    'bank_name': 'Ngân hàng', 'bank_branch': 'Chi nhánh ngân hàng',
}
PERSONAL = ('cua toi', 'cua minh', 'toi co duoc', 'truong hop cua toi', 'giup toi', 'cho toi',
            'toi', 'minh', 'toi muon', 'toi xin', 'toi da', 'toi dang', 'soan', 'dien san', 'ban nhap')


@dataclass(frozen=True)
class ContextPlan:
    question: str
    card: object | None
    spec: ContextSpec
    direct_fields: tuple[str, ...] = ()
    unsupported_person: bool = False
    hypothetical: bool = False
    clarification: str | None = None
    analysis: RequestAnalysis | None = None


def prepare(question, catalog=None, lexicon=None, policy=None):
    query = normalized(question)
    hypothetical = any(phrase(query, term) for term in ('gia su', 'gia dinh rang', 'neu toi'))
    analysis = analyze_question(question, catalog, lexicon, policy)
    other = analysis.unsupported_person
    self_request = any(phrase(query, term) for term in PERSONAL)
    direct = analysis.direct_fields
    cards, ambiguous = analysis.cards, analysis.ambiguous
    card = cards[0] if len(cards) == 1 and not ambiguous else None
    spec = getattr(card, 'context', None) or ContextSpec(note='No contextual requirements for this task.')
    generic = any(phrase(query, term) for term in ('chi quy dinh chung', 'khong dung ho so', 'chi trich dan'))
    if generic or other or analysis.document_lookup or analysis.clarification or (spec.mode in ('branch', 'calculation') and not self_request):
        spec = ContextSpec(note='The request does not require individual application.')
    # Clauses asking work facts may accompany a document task; both are composed later.
    if other:
        direct = ()
    if analysis.clarification:
        direct = ()
    return ContextPlan(question, card, spec, direct, other, hypothetical, analysis.clarification, analysis)


def requirements(plan):
    fields = set(plan.spec.facts)
    fields.update(name for _, name, _, _ in string.Formatter().parse(plan.spec.template) if name)
    if plan.spec.date_field:
        fields.add(plan.spec.date_field)
    if plan.spec.family_summary:
        fields.add('families')
    fields.update(field for field in plan.direct_fields if not field.startswith('manager_'))
    manager = plan.spec.manager or any(field.startswith('manager_') for field in plan.direct_fields)
    return tuple(sorted(field for field in fields if not field.startswith('manager_'))), manager


def explicit_dates(question):
    values = []
    for match in re.finditer(r'\b(\d{4}-\d{2}-\d{2}|\d{1,2}/\d{1,2}/\d{4})\b', question):
        raw = match.group(0)
        try:
            value = date.fromisoformat(raw) if '-' in raw else datetime.strptime(raw, '%d/%m/%Y').date()
        except ValueError:
            continue
        values.append(value.isoformat())
    return values


def document_result(row):
    """Convert checked internal document rendering without relabeling employee data as quotes."""
    evidence, claims = [], []
    for claim in row.claims:
        refs = []
        for citation in claim.citations:
            source_id = citation.source_id
            key = next((item['id'] for item in evidence if item['source_id'] == source_id and
                        item['quote'] == citation.quote), None)
            if key is None:
                key = f'D{len(evidence)+1}'
                evidence.append(dict(kind='document', id=key, source_id=source_id, quote=citation.quote))
            refs.append(key)
        claims.append(dict(id=f'C{len(claims)+1}', text=claim.text, evidence_refs=refs))
    return evidence, claims


def compose(plan, snapshot, state, evidence, claims, *, calculate_values=True):
    application = Application(mode='hypothetical' if plan.hypothetical else 'general', state=state)
    fields, manager_needed = requirements(plan)
    if not fields and not manager_needed:
        return application, []
    if snapshot is None:
        application.state = state
        return application, ['Chưa đọc được hồ sơ hiện tại để áp dụng phần trả lời cho cá nhân bạn.']
    values = dict(snapshot.values)
    relation = snapshot.manager
    if manager_needed:
        values['manager_name'] = relation.manager.employee_name if relation and relation.status == 'found' else None
        values['manager_email'] = relation.manager.work_email if relation and relation.status == 'found' else None
    application.mode = 'hypothetical' if plan.hypothetical else 'personalized'
    application.state = 'applied'
    limitations, fact_refs = [], {}

    def fact(field, value, *, subject='self', label=None, updated=None):
        if value is not None and len(str(value)) > 2000:
            value = None
            application.state = 'partial'
            limitations.append(f'Thông tin {LABELS.get(field, field)} vượt giới hạn hiển thị; chưa thể dùng giá trị này trong lượt trả lời.')
        key = f'F{sum(item["kind"] == "employee" for item in evidence)+1}'
        evidence.append(dict(kind='employee', id=key, field=field, subject=subject,
            label=label or LABELS.get(field, field), value=None if value is None else str(value),
            observed_at=snapshot.observed_at, updated_at=(relation.manager.updated_at if subject == 'manager' and relation and relation.manager else snapshot.updated_at) if updated is None else updated))
        fact_refs[field] = key
        return key

    def add(text, refs):
        if len(text) > 2000:
            application.state = 'partial'
            limitations.append('Nội dung áp dụng hồ sơ vượt giới hạn một nhận định; cần thu hẹp yêu cầu để trình bày đầy đủ.')
            return
        claims.append(dict(id=f'C{len(claims)+1}', text=text, evidence_refs=list(dict.fromkeys(refs))))

    for field in plan.direct_fields:
        value = values.get(field)
        ref = fact(field, value, subject='manager' if field.startswith('manager_') else 'self')
        display = value
        if value is not None and field.endswith('_date'):
            display = datetime.strptime(value, '%Y-%m-%d').strftime('%d/%m/%Y')
        add(f'{LABELS.get(field, field)} của bạn: {display}.' if value is not None else
            f'Hồ sơ hiện chưa có thông tin về {LABELS.get(field, field).lower()}.', [ref])
        if value is None:
            application.state = 'partial'
    if plan.spec.template:
        names = [name for _, name, _, _ in string.Formatter().parse(plan.spec.template) if name]
        used, replacements = [], {}
        for name in names:
            value = values.get(name)
            if name not in fact_refs:
                fact(name, value, subject='manager' if name.startswith('manager_') else 'self')
            used.append(fact_refs[name])
            replacements[name] = str(value) if value is not None else 'chưa có dữ liệu'
            if value is None:
                application.state = 'partial'
        if used:
            add(plan.spec.template.format_map(replacements), used)
    selected = [field for field in plan.spec.facts if field not in fact_refs]
    if selected:
        refs, parts = [], []
        for field in selected:
            value = values.get(field)
            refs.append(fact(field, value))
            parts.append(f'{LABELS.get(field, field)}: {value if value is not None else "chưa có dữ liệu"}')
            if value is None:
                application.state = 'partial'
        add('Hồ sơ hiện tại ghi nhận: ' + '; '.join(parts) + '.', refs)
    if plan.spec.family_summary:
        family = values.get('families')
        if family is None:
            application.state = 'partial'
            limitations.append('Chưa có danh sách thân nhân; không coi dữ liệu thiếu là không có con hoặc người phụ thuộc.')
        elif not family:
            application.state = 'partial'
            limitations.append('Danh sách thân nhân hiện trống; cần xác nhận đối tượng và chứng từ của sự kiện.')
        else:
            for index, member in enumerate(family[:3]):
                refs, parts = [], []
                for key, label in (('name', 'Họ tên thân nhân'), ('relation_type_display', 'Quan hệ'), ('birthday', 'Ngày sinh thân nhân')):
                    value = getattr(member, key)
                    refs.append(fact('families.' + key, value, subject=f'family:{index+1}', label=label))
                    parts.append(f'{label}: {value if value is not None else "chưa có dữ liệu"}')
                add('; '.join(parts) + '. Quan hệ và ngày sinh chưa xác nhận đủ điều kiện hưởng hoặc chứng từ.', refs)
            if len(family) > 3:
                limitations.append('Chỉ trình bày ba thân nhân đầu tiên trong lượt này; chưa đánh giá đầy đủ danh sách.')
            application.state = 'partial'
    # Conditions requiring operational/historical data remain explicit inputs, not inferred flags.
    if plan.spec.mode in ('branch', 'calculation'):
        application.mode = 'hypothetical' if plan.hypothetical else 'conditional'
        inputs = parse_inputs(plan.question)
        for slot, value in inputs.items():
            if slot in plan.spec.prompts:
                key = f'U{sum(item["kind"] == "user_input" for item in evidence)+1}'
                evidence.append(dict(kind='user_input', id=key, slot=slot, label=SLOT_LABELS[slot],
                                     value=value, hypothetical=plan.hypothetical))
                add(f'{SLOT_LABELS[slot]} bạn cung cấp: {value}. Thông tin này chưa được xác thực từ hệ thống nghiệp vụ.', [key])
        application.missing_inputs = [MissingInput(slot=slot, prompt=prompt) for slot, prompt in plan.spec.prompts.items() if slot not in inputs]
        if plan.spec.mode == 'branch':
            application.state = 'partial'
            limitations.append('Đối chiếu trên là hướng dẫn có điều kiện; hồ sơ hiện tại chưa xác nhận toàn bộ điều kiện và phê duyệt của nghiệp vụ.')
        if application.missing_inputs:
            application.state = 'partial'
            limitations.extend(item.prompt for item in application.missing_inputs)
    query = normalized(plan.question)
    if phrase(query, 'toi da chinh thuc') and values.get('official_date') is None:
        application.state = 'conflict'
        application.conflicts = ['Bạn cho biết đã chính thức nhưng hồ sơ chưa có ngày chính thức.']
        limitations += application.conflicts
    if manager_needed and (not relation or relation.status != 'found'):
        application.state = 'partial'
        limitations.append('Chưa xác định duy nhất được liên kết quản lý; không tự chọn người duyệt thay thế.')
    if any(phrase(query, term) for term in ('tham nien', 'bao lau', 'bao nhieu ngay', 'bao nhieu thang')):
        if not explicit_dates(plan.question) and not phrase(query, 'hom nay'):
            application.state = 'partial'
            application.missing_inputs.append(MissingInput(slot='as_of', prompt='Ngày xét: bạn muốn tính đến ngày nào?'))
            limitations.append('Cần ngày xét cụ thể để tính khoảng thời gian từ mốc hồ sơ.')
        if phrase(query, 'tham nien'):
            application.state = 'partial'
            limitations.append('Chưa xác nhận mốc và quy tắc thâm niên của chính sách; không mặc định ngày chính thức là thâm niên hưởng chế độ.')
    dates = explicit_dates(plan.question)
    date_field = plan.spec.date_field
    operation = plan.spec.operation
    # Explicit work-date arithmetic is distinct from policy tenure or leave balances.
    if not operation and calculate_values and any(phrase(query, term) for term in ('bao nhieu ngay', 'bao nhieu thang')):
        date_field = next((field for field in plan.direct_fields if field.endswith('_date')), None)
        operation = 'completed_months' if phrase(query, 'bao nhieu thang') else 'interval_days'
    if calculate_values and date_field and operation and values.get(date_field) and (dates or phrase(query, 'hom nay')):
        as_of = dates[-1] if dates else datetime.now(ZoneInfo('Asia/Ho_Chi_Minh')).date().isoformat()
        if date_field not in fact_refs:
            fact(date_field, values[date_field])
        uid = f'U{sum(item["kind"] == "user_input" for item in evidence)+1}'
        evidence.append(dict(kind='user_input', id=uid, slot='as_of', label='Ngày xét', value=as_of,
                             hypothetical=plan.hypothetical))
        try:
            value, unit = calculate(operation, [values[date_field]], as_of=as_of)
        except ValueError:
            limitations.append('Ngày xét không tạo được khoảng thời gian hợp lệ với mốc hồ sơ.')
            application.state = 'partial'
        else:
            key = f'K{sum(item["kind"] == "calculation" for item in evidence)+1}'
            evidence.append(dict(kind='calculation', id=key, operation=operation,
                label='Khoảng thời gian theo mốc được chọn', value=value, unit=unit,
                input_refs=[fact_refs[date_field], uid], rule_refs=[], as_of=as_of))
            add(f'Từ {values[date_field]} đến {as_of}: {value} {"ngày" if unit == "day" else "tháng tròn"}. '
                'Đây là khoảng thời gian theo mốc hồ sơ, chưa phải số phép hoặc thâm niên hưởng chế độ.', [key])
    if plan.hypothetical:
        limitations.append('Phần áp dụng đang xét theo tình huống giả định; không thay đổi hồ sơ hoặc xác nhận quyền lợi thực tế.')
    if application.conflicts:
        application.state = 'conflict'
    return application, limitations
