"""Author explicit context dispositions from the reviewed corpus groups; never edits chunks.

Run explicitly before build/check. Each generated card remains a reviewable source input.
No policy threshold, authority or employee entitlement is inferred by this utility.
"""
import json
from pathlib import Path
from src.paths import REPOSITORY_ROOT
from src.type_b.tasks import normalized


def disposition(card):
    label = normalized(card['label'])
    documents = {ref['document_id'] for section in card['sections'].values()
                 for unit in section['units'] for ref in unit['refs']}
    spec = dict(mode='none', groups=[], manager=False, prompts={}, template='', operation=None,
                date_field=None, note='General document/software instruction; personnel cannot establish current permissions or operational state.')
    work = 'Khi chuẩn bị thông tin cho yêu cầu, bạn có thể dùng: {employee_name} · {employee_code} · {job_title} · {department_name}.'
    def set_spec(mode, groups, template=work, manager=False, prompts=None, note=''):
        spec.update(mode=mode, groups=groups, template=template, manager=manager, prompts=prompts or {},
                    note=note or 'Read only fields needed for contextual guidance. Actual entitlement/authority requires the declared additional evidence.')
    if any(term in label for term in ('nghi phep', 'nghi khong luong', 'di tre', 've som', 'bo sung cong', 'quen cham cong')):
        set_spec('branch', ['work'], manager=True, prompts={
            'leave_dates': 'Ngày nghỉ: bạn cần áp dụng cho ngày hoặc khoảng thời gian nào?',
            'leave_type': 'Loại nghỉ: bạn chọn loại nghỉ hoặc loại xác nhận nào?',
            'work_schedule': 'Lịch làm việc: cần lịch ca/ngày làm áp dụng để tính ngày làm việc hoặc hạn gửi.',
        })
    elif any(term in label for term in ('nghi viec', 'thoi viec', 'ban giao', 'hop dong', 'thu viec')):
        set_spec('branch', ['work', 'employment'], manager=True, prompts={
            'contract_at_date': 'Hợp đồng tại ngày xét: cần loại và hiệu lực hợp đồng thực tế để chọn nhánh báo trước.',
            'leave_dates': 'Ngày nghỉ: ngày dự kiến và ngày thông báo là ngày nào?',
        })
    elif 'd1ed5f3f-57ed-465d-a080-97c4a39efd8d' in documents or any(t in label for t in ('phuc loi', 'sinh con', 'tang gia', 'ket hon', 'nam vien')):
        groups = ['work', 'employment']
        if any(t in label for t in ('con nhan vien', 'cho con', 'thieu nhi', 'trung thu', 'gia dinh')):
            groups.append('family')
        set_spec('branch', groups, prompts={
            'as_of': 'Ngày xét: cần ngày phát sinh sự kiện hoặc ngày lễ cụ thể.',
            'contract_at_date': 'Hợp đồng tại ngày xét: hồ sơ hiện tại chưa thay thế được lịch sử hợp đồng.',
            'benefit_event': 'Sự kiện hưởng chính sách: cần xác nhận sự kiện, đối tượng và chứng từ theo tài liệu.',
        })
        if any(t in label for t in ('tham nien', 'le tet', 'ngay le')):
            spec['prompts']['tenure_basis'] = 'Mốc tính thâm niên: cần xác nhận mốc áp dụng và quy tắc làm tròn; không mặc định ngày chính thức.'
    elif documents.intersection({'1c42626a-099e-463f-8097-4b64afe29bac', '91ae6b2d-ec63-4463-bafc-d9c8370fa722', '57cc6d6b-64eb-4c61-a462-3e961e68044d'}):
        set_spec('branch', ['work'], prompts={
            'job_mapping': 'Nhóm chức danh: cần xác nhận nhóm theo chính sách và chức vụ kiêm nhiệm nếu có.',
            'trip_details': 'Thông tin chuyến đi: cần nơi đi/đến, ngày giờ, cự ly, phương tiện và chứng từ liên quan.',
        })
    elif 'gioi thieu' in label and 'nhan' in label:
        set_spec('branch', ['work', 'employment'], prompts={
            'recruitment_case': 'Hồ sơ giới thiệu: cần vị trí ứng viên, kết quả tuyển, mốc thử việc và vai trò của bạn trong tuyển chọn.',
        })
    elif 'b020eea1-3bed-43a3-b18d-359c7a236e95' in documents:
        set_spec('branch', ['work', 'employment'], prompts={
            'job_mapping': 'Nhóm chức danh: cần xác nhận nhóm công việc theo bảng đồng phục.',
            'issued_uniforms': 'Lịch sử cấp phát: cần ngày nhận và số lượng thực tế nếu hỏi cấp lại hoặc bồi hoàn.',
        })
    elif any(t in label for t in ('bao hiem', 'bhxh')):
        set_spec('branch', ['work', 'insurance'], prompts={
            'benefit_event': 'Sự kiện hưởng chính sách: số bảo hiểm/ngày tham gia không thay thế lịch sử đóng và hồ sơ được duyệt.',
        })
    elif any(t in label for t in ('thong tin nhan su', 'ho so ca nhan', 'gui thu', 'nhan thu', 'de nghi', 'phieu di cong tac', 'dat xe', 'dat phong hop', 'suat an')):
        set_spec('fill', ['work'])
    elif any(t in label for t in ('toa nha', 'bao ve', 'niem phong', 'con dau', 'thanh ly')):
        set_spec('branch', ['work'], prompts={
            'site': 'Địa điểm áp dụng: bạn đang hỏi cho cơ sở hoặc mục tiêu nào?',
            'authority': 'Phân công hoặc ủy quyền: chức danh/đơn vị chưa chứng minh quyền thực hiện thao tác này.',
        })
    spec['facts'] = []
    spec['family_summary'] = 'family' in spec['groups']
    if 'employment' in spec['groups']:
        spec['facts'] = ['probation_date', 'probation_end_date', 'official_date', 'resignation_date', 'employee_type', 'contract_kind']
    if 'insurance' in spec['groups']:
        spec['facts'] += ['social_insurance_number', 'social_insurance_date', 'health_insurance_number', 'health_clinic']
    if spec['manager']:
        spec['template'] += ' Quản lý liên kết trong hồ sơ: {manager_name}; email công việc: {manager_email}. Người phê duyệt thực tế còn theo phân quyền của quy trình.'
    return spec


def main():
    root = REPOSITORY_ROOT / 'artifacts/answers/source'
    count = 0
    for path in sorted((root/'cards').glob('*.json')):
        data = json.loads(path.read_text(encoding='utf-8'))
        data['context'] = disposition(data)
        path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
        count += 1
    path = root/'index.json'
    data = json.loads(path.read_text(encoding='utf-8'))
    data['metadata']['schema_version'] = 3
    path.write_text(json.dumps(data, ensure_ascii=False, indent=2)+'\n', encoding='utf-8', newline='\n')
    print(json.dumps({'context_cards': count}))


if __name__ == '__main__':
    main()
