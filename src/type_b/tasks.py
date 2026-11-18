"""Versioned, conservative self-reference tasks. No learned permissions or field inference."""
from dataclasses import dataclass
import re
import unicodedata

TASK_VERSION = 2


@dataclass(frozen=True)
class Task:
    field: str
    label: str
    aliases: tuple[str, ...]
    date: bool = False


TASKS = (
    Task('employee_name', 'Họ tên', ('ho ten', 'ten cua toi')),
    Task('employee_code', 'Mã nhân viên', ('ma nhan vien', 'ma so nhan vien')),
    Task('job_title', 'Chức danh', ('chuc danh', 'chuc vu', 'vi tri cong viec')),
    Task('department_name', 'Phòng ban', ('phong ban', 'bo phan', 'thuoc phong', 'thuoc don vi', 'don vi cong tac')),
    Task('manager_name', 'Quản lý trực tiếp', ('quan ly truc tiep', 'nguoi quan ly', 'cap tren truc tiep', 'sep truc tiep')),
    Task('probation_date', 'Ngày bắt đầu thử việc', ('ngay bat dau thu viec', 'bat dau thu viec', 'ngay thu viec', 'thu viec tu ngay nao'), True),
    Task('probation_end_date', 'Ngày kết thúc thử việc', ('ngay ket thuc thu viec', 'ket thuc thu viec', 'thu viec ket thuc', 'het thu viec ngay nao'), True),
    Task('official_date', 'Ngày chính thức', ('ngay chinh thuc', 'ngay vao lam chinh thuc', 'ngay lam viec chinh thuc', 'chinh thuc tu ngay nao'), True),
    Task('resignation_date', 'Ngày nghỉ việc', ('ngay nghi viec', 'ngay thoi viec'), True),
)
BY_FIELD = {task.field: task for task in TASKS}


def normalized(text):
    text = unicodedata.normalize('NFD', text.casefold()).replace('đ', 'd')
    return ' '.join(re.sub(r'[^a-z0-9]+', ' ', ''.join(c for c in text if not unicodedata.combining(c))).split())


def phrase(text, term):
    return f' {term} ' in f' {text} '


@dataclass(frozen=True)
class Route:
    source: str
    fields: tuple[str, ...] = ()
    reason: str | None = None


POLICY = ('chinh sach', 'quy dinh', 'quyen loi', 'che do', 'duoc huong', 'duoc ho tro',
          'thu tuc', 'dieu kien', 'bao nhieu tien', 'toi co duoc', 'duoc tro cap',
          'tro cap', 'duoc nghi', 'nghi phep', 'phep nam', 'huong dan', 'quy trinh',
          'cach', 'lam the nao', 'su dung')
RESTRICTED = ('ngan hang', 'tai khoan ngan hang', 'so tai khoan', 'cccd', 'can cuoc',
              'giay to tuy than', 'suc khoe', 'nhom mau', 'gia dinh', 'than nhan',
              'ngay sinh', 'bao hiem', 'ma so thue', 'dia chi', 'email', 'dien thoai',
              'luong', 'ngay phep', 'so du phep', 'cham cong', 'thoi gian cong tac', 'tham nien',
              'bao nhieu ngay', 'bao nhieu nam', 'bao lau',
              'bang cap', 'dao tao', 'hoc van', 'quoc tich', 'ton giao', 'dan toc', 'trang thai',
              'loai hop dong', 'loai nhan vien', 'nhom nhan vien', 'ngay ky hop dong',
              'lich su', 'ho so cua toi', 'toan bo ho so', 'tat ca thong tin')


def route_question(text, *, document_task=False):
    from src.retrieval.language import positive_question
    text = positive_question(text)
    query = normalized(text)
    original = unicodedata.normalize('NFC', text.casefold())
    self_reference = bool(re.search(r'\b(?:tôi|toi|mình)\b', original)) or (
        phrase(query, 'cua minh') or phrase(query, 'ban than') and not phrase(query, 'ban than nhan vien')
        or query.startswith('minh '))
    policy = any(phrase(query, value) for value in POLICY)
    fields = tuple(task.field for task in TASKS if any(phrase(query, alias) for alias in task.aliases))
    if query in ('toi la ai', 'toi ten gi'):
        fields = ('employee_name',)
    # A recognized software/policy instruction mentioning email, phones or
    # family is not by itself a request to read those personnel fields.
    named_subject = bool(re.search(r'\bcua (?!(?:toi|minh|ban than|cong ty|doanh nghiep)\b)', query))
    if document_task and (not fields or not self_reference and not named_subject) and not any(phrase(query, marker) for marker in
            ('cua toi', 'cua minh', 'toi co duoc', 'chac chan', 'duoc huong',
             'cua dong nghiep', 'cua nguoi khac', 'cua tai khoan', 'noi dung email', 'tu gui', 'tu duyet')):
        return Route('type_a')
    source = 'mixed' if policy and fields and self_reference else 'type_b'
    # A named subject plus a trailing "I want to know" is not a self-reference.
    nonself = r'\bcua (?!(?:toi|minh|ban than)\b)'
    if policy:
        nonself = r'\bcua (?!(?:toi|minh|ban than|cong ty|doanh nghiep)\b)'
    third_person = bool(re.search(nonself, query))
    if third_person and fields:
        return Route(source, reason='other_employee_unsupported')
    restricted = any(phrase(query, term) for term in RESTRICTED)
    if restricted and self_reference and (fields or not policy or phrase(query, 'va')):
        if policy:
            source = 'mixed'
        return Route(source, reason='employee_task_unsupported')
    if self_reference and phrase(query, 'ngay vao lam') and not any(
            field in fields for field in ('official_date', 'probation_date')):
        return Route(source, reason='ambiguous_employment_date')
    if fields and self_reference:
        if not policy:
            clauses = re.split(r'[;?!.]|\b(?:và|va|đồng thời|dong thoi|ngoài ra|ngoai ra)\b',
                               text, flags=re.IGNORECASE)
            fillers = {'', 'cam on', 'xin cam on', 'cam on ban', 'toi muon biet',
                       'cho toi biet', 'cho minh biet', 'vui long', 'lam on', 'xin hoi'}
            for clause in clauses:
                part = normalized(clause)
                if part not in fillers and part not in ('toi la ai', 'toi ten gi') and not any(
                        phrase(part, alias) for task in TASKS for alias in task.aliases):
                    return Route(source, reason='employee_task_unclear')
        # Policy wording alone does not request the individual's field value.
        if policy and not any(phrase(query, marker) for marker in (
                'va', 'dong thoi', 'cho biet', 'la gi', 'la ai', 'ngay nao', 'voi', 'thuoc', 'toi co duoc')):
            return Route('type_a')
        return Route(source, fields)
    if fields and not policy:
        return Route('type_b', reason='employee_reference_unclear')
    if self_reference and not policy and any(phrase(query, term) for term in (
            'vao cong ty', 'ho so', 'thong tin cong viec', 'thu viec', 'ngay bat dau lam')):
        return Route('type_b', reason='employee_task_unclear')
    return Route('type_a')
