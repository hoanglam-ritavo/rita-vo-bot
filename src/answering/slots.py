"""Bounded labelled user inputs. Never execute or promote assertions to HRM facts."""
import re
from src.type_b.tasks import normalized

SLOT_LABELS = {
    'leave_dates': 'Ngày nghỉ', 'leave_type': 'Loại nghỉ', 'leave_reason': 'Lý do',
    'work_schedule': 'Lịch làm việc', 'as_of': 'Ngày xét', 'contract_at_date': 'Hợp đồng tại ngày xét',
    'tenure_basis': 'Mốc tính thâm niên', 'trip_details': 'Thông tin chuyến đi',
    'job_mapping': 'Nhóm chức danh', 'issued_uniforms': 'Lịch sử cấp phát',
    'benefit_event': 'Sự kiện hưởng chính sách', 'recruitment_case': 'Hồ sơ giới thiệu',
    'authority': 'Phân công hoặc ủy quyền', 'site': 'Địa điểm áp dụng',
}


def parse_inputs(text):
    result = {}
    for part in re.split(r'[;\n]', text):
        label, sep, value = part.partition(':')
        if not sep or not value.strip() or len(value.strip()) > 240:
            continue
        key = next((key for key, name in SLOT_LABELS.items() if normalized(label.strip()) in
                    (normalized(name), key.replace('_', ' '))), None)
        if key:
            result[key] = value.strip()
    return result
