"""Edit the settings below, then run this UTF-8 file with the repository's env Python."""
import os
import math
import sys
from contextlib import redirect_stdout, redirect_stderr
from pathlib import Path, PurePosixPath
from types import SimpleNamespace

REPOSITORY_ROOT = Path(__file__).resolve().parents[1]
if __package__ in (None, ''):
    sys.path.insert(0, str(REPOSITORY_ROOT))

# Edit these values in your IDE. Keep this file saved as UTF-8.
QUESTION = "công ty có bao nhiêu phúc lợi cho nhân viên?"
MODEL_REVISION = "614241f622f53c4eeff9890bdc4f31cfecc418b3"
MODEL_PATH = REPOSITORY_ROOT / ".tmp/models/multilingual-e5-small" / MODEL_REVISION
TOP_K = 5


def configure_console():
    for stream in (sys.stdout, sys.stderr):
        reconfigure = getattr(stream, 'reconfigure', None)
        if callable(reconfigure):
            try:
                reconfigure(encoding='utf-8', errors='strict')
            except (OSError, ValueError, TypeError):
                pass  # Some IDE consoles cannot change their encoding.


def format_ranges(ranges):
    merged = []
    for start, end in sorted(set(ranges)):
        if merged and start <= merged[-1][1] + 1:
            merged[-1] = (merged[-1][0], max(merged[-1][1], end))
        else:
            merged.append((start, end))
    return ', '.join(str(start) if start == end else f'{start}–{end}' for start, end in merged)


def source_label(spans):
    pages, lines, cells = [], [], set()
    for span in spans:
        location = span.get('location', {})
        kind = location.get('kind')
        if kind in ('pdf', 'text', 'csv'):
            prefix = 'page' if kind == 'pdf' else 'line'
            start, end = location.get(prefix + '_start'), location.get(prefix + '_end')
            if type(start) is int and type(end) is int and 0 < start <= end:
                (pages if kind == 'pdf' else lines).append((start, end))
        elif kind == 'xlsx':
            sheet, row = location.get('sheet'), location.get('row')
            start, end = location.get('column_start'), location.get('column_end')
            if sheet and type(row) is int and row > 0 and type(start) is int and type(end) is int and 0 < start <= end:
                cells.add((sheet, row, start, end))
    labels = []
    if pages:
        labels.append('trang ' + format_ranges(pages))
    if lines:
        labels.append('dòng ' + format_ranges(lines))
    for sheet, row, start, end in sorted(cells):
        columns = str(start) if start == end else f'{start}–{end}'
        labels.append(f'Sheet {sheet}, dòng {row}, cột {columns}')
    return '; '.join(labels) or 'Chưa có vị trí nguồn chi tiết'


def format_result(question, result):
    """Render candidates without changing their contents or retrieval order."""
    heading = f'Câu hỏi: {question}\n\n'
    messages = {
        'no_eligible_documents': 'Không có tài liệu public còn hiệu lực để tìm kiếm.',
        'model_mismatch': 'Model hiện tại không tương thích với dữ liệu đã index.',
        'no_matches': 'Không tìm được đoạn tài liệu khả dụng.',
    }
    status = result.get('status')
    if status in messages:
        return heading + messages[status]
    if status != 'matches_found':
        errors = {
            'invalid_question': 'Câu hỏi phải là chuỗi văn bản không rỗng.',
            'question_too_long': 'Câu hỏi vượt giới hạn 8.000 ký tự.',
            'question_exceeds_model_limit': 'Câu hỏi vượt giới hạn token của model; hãy nhập câu ngắn hơn.',
            'invalid_top_k': 'TOP_K phải là số nguyên từ 1 đến 20.',
            'invalid_model_revision': 'MODEL_REVISION phải là commit gồm 40 ký tự hex.',
            'local_model_missing': 'Không tìm thấy thư mục model local; hãy kiểm tra MODEL_PATH.',
            'local_model_empty': 'Thư mục model local chưa có dữ liệu.',
            'model_load_failed': 'Không thể nạp model local; hãy kiểm tra model và môi trường Python.',
            'model_dimensions': 'Model không có kích thước vector phù hợp.',
            'model_changed': 'Model đã thay đổi trong lúc nạp; hãy chạy lại với bộ file ổn định.',
            'invalid_model_token_limit': 'Giới hạn token của model không hợp lệ.',
            'invalid_query_vector': 'Model trả về vector câu hỏi không hợp lệ.',
            'query_embedding_failed': 'Không thể tạo embedding cho câu hỏi.',
            'database_search_failed': 'Không thể tìm kiếm trong database; hãy kiểm tra kết nối và cấu hình.',
            'service_not_started': 'Dịch vụ tìm kiếm chưa được khởi tạo.',
            'shutdown_failed': 'Không thể đóng đầy đủ tài nguyên sau khi tìm kiếm.',
        }
        return heading + errors.get(result.get('code'), 'Không thể hoàn tất tìm kiếm. Hãy kiểm tra cấu hình và thử lại.')
    matches = result.get('matches', [])
    if not matches:
        return heading + messages['no_matches']
    sections = [heading + 'Các đoạn tài liệu tìm được\nĐây là kết quả tìm kiếm, chưa phải câu trả lời tổng hợp.']
    for index, match in enumerate(matches[:3], 1):
        title = match.get('title') or PurePosixPath(match.get('source_path') or '').name or 'Tài liệu chưa có tên'
        source = source_label(match.get('source_spans') or [])
        sections.append(f'{index}. {title}\n   Nguồn: {source}\n\n' + match['content'])
    sections.append(f'Hiển thị {min(3, len(matches))}/{len(matches)} kết quả tìm được.')
    return '\n\n'.join(sections)


def format_timings(result):
    """Show only completed measurements; missing values are not zero durations."""
    timings = result.get('timings') or {}
    if not isinstance(timings, dict):
        timings = {}
    lines = ['Thời gian xử lý:']
    for key, label in (
        ('model_initialization_seconds', 'Khởi tạo model'),
        ('retrieval_seconds', 'Tìm kiếm sau khi model sẵn sàng'),
    ):
        value = timings.get(key)
        if type(value) in (int, float) and math.isfinite(value) and value >= 0:
            duration = f'{value:.3f}'.replace('.', ',') + ' giây'
        else:
            duration = 'Chưa có số đo hoàn tất'
        lines.append(f'- {label}: {duration}')
    return '\n'.join(lines)


def print_result(output):
    encoding = getattr(sys.stdout, 'encoding', None) or 'ascii'
    try:
        # Check before writing, including streams configured to replace characters.
        if getattr(sys.stdout, 'encoding', None):
            output.encode(encoding, errors='strict')
        print(output)
    except UnicodeEncodeError:
        print(output.encode(encoding, errors='backslashreplace').decode(encoding))


def main():
    configure_console()
    args = SimpleNamespace(question=QUESTION, model_path=MODEL_PATH,
                           model_revision=MODEL_REVISION, top_k=TOP_K)
    try:
        with open(os.devnull, 'w') as sink, redirect_stdout(sink), redirect_stderr(sink):
            from src.cli.retrieve_documents import run
            from src.database.runtime import run_async
            result, code = run_async(run(args))
    except Exception:
        result, code = {'status': 'error', 'code': 'retrieval_failed', 'matches': []}, 2
    print_result(format_result(QUESTION, result) + '\n\n' + format_timings(result))
    return code


if __name__ == '__main__':
    raise SystemExit(main())
