"""Manual entrypoint wiring and Unicode output, without a model or database."""
import io
from pathlib import Path
import subprocess
import sys
from unittest.mock import AsyncMock

import pytest
from scripts import try_retrieval as demo
from src.cli import retrieve_documents as cli


def test_timing_format():
    output = demo.format_timings({'timings': {'model_initialization_seconds': 12.3501,
                                             'retrieval_seconds': 0.1804}})
    assert output == ('Thời gian xử lý:\n- Khởi tạo model: 12,350 giây\n'
                      '- Tìm kiếm sau khi model sẵn sàng: 0,180 giây')
    assert '0,000 giây' in demo.format_timings({'timings': {'retrieval_seconds': 0.0}})


@pytest.mark.parametrize('result', [{}, {'timings': None}, {'timings': {}},
                                   {'timings': {'retrieval_seconds': None}}])
def test_missing_timings(result):
    assert demo.format_timings(result).count('Chưa có số đo hoàn tất') == 2


@pytest.mark.parametrize('status', ['matches_found', 'no_matches', 'error'])
def test_timings_footer_is_last(monkeypatch, capsys, status):
    result = {'status': status, 'matches': [],
              'timings': {'model_initialization_seconds': 1.23456, 'retrieval_seconds': None}}
    monkeypatch.setattr(cli, 'run', AsyncMock(return_value=(result, 0)))
    demo.main()
    output = capsys.readouterr().out
    assert output.endswith(demo.format_timings(result) + '\n')
    assert '1,235 giây' in output


def test_compact_view_preserves_content_order_and_input():
    import copy
    matches = [{'title': f'Policy {i}', 'content': f'Content {i}\nCondition\nException',
                'chunk_id': 'hidden-uuid', 'cosine_distance': 0.12345,
                'fingerprint': 'hidden-fingerprint',
                'source_spans': [{'location': {'kind': 'pdf', 'page_start': 3, 'page_end': 5}},
                                 {'location': {'kind': 'pdf', 'page_start': 1, 'page_end': 1}},
                                 {'location': {'kind': 'pdf', 'page_start': 3, 'page_end': 3}}]}
               for i in range(5)]
    result = {'status': 'matches_found', 'matches': matches}
    before = copy.deepcopy(result)
    output = demo.format_result('Question', result)
    assert result == before
    assert 'Hiển thị 5/5' in output
    assert output.index('Policy 0') < output.index('Policy 1') < output.index('Policy 2')
    for match in matches:
        assert match['content'] in output
    for unwanted in ['hidden-uuid', '0.12345', 'hidden-fingerprint', 'cosine_distance']:
        assert unwanted not in output
    assert output.count('Nguồn: trang 1, 3–5') == 5


@pytest.mark.parametrize('kind', ['text', 'csv'])
def test_line_source_labels(kind):
    spans = [{'location': {'kind': kind, 'line_start': 2, 'line_end': 4}}] * 2
    assert demo.source_label(spans) == 'dòng 2–4'


def test_xlsx_and_fallback_labels():
    spans = [{'location': {'kind': 'xlsx', 'sheet': 'Benefits', 'row': 3,
                           'column_start': 2, 'column_end': 4, 'header_row': 1}}] * 2
    assert demo.source_label(spans) == 'Sheet Benefits, dòng 3, cột 2–4'
    result = {'status': 'matches_found', 'matches': [
        {'source_path': 'policies/example.txt', 'content': 'Full text'}]}
    output = demo.format_result('Question', result)
    assert '1. example.txt' in output
    assert 'Chưa có vị trí nguồn chi tiết' in output
    assert 'Hiển thị 1/1' in output


@pytest.mark.parametrize('status,code,expected', [
    ('no_eligible_documents', None, 'Không có tài liệu public còn hiệu lực'),
    ('model_mismatch', None, 'Model hiện tại không tương thích'),
    ('no_matches', None, 'Không tìm được đoạn tài liệu'),
    ('matches_found', None, 'Không tìm được đoạn tài liệu'),
    ('error', 'invalid_question', 'Câu hỏi phải là chuỗi'),
    ('error', 'invalid_top_k', 'TOP_K phải là số nguyên'),
    ('error', 'question_too_long', '8.000 ký tự'),
    ('error', 'question_exceeds_model_limit', 'giới hạn token'),
    ('error', 'model_load_failed', 'Không thể nạp model local'),
    ('error', 'database_search_failed', 'Không thể tìm kiếm trong database'),
    ('error', 'secret-unknown-code', 'Không thể hoàn tất tìm kiếm'),
])
def test_safe_status_messages(status, code, expected):
    output = demo.format_result('Question', {'status': status, 'code': code, 'matches': [],
                                            'exception': 'private credentials'})
    assert expected in output
    assert 'private' not in output and 'secret' not in output


@pytest.mark.parametrize('code', [0, 1, 2])
def test_params_and_exit_code(monkeypatch, capsys, code):
    monkeypatch.setattr(demo, 'QUESTION', 'Nhân viên kết hôn được hỗ trợ bao nhiêu?')
    runner = AsyncMock(return_value=({'status': 'matches_found', 'matches': [{'content': demo.QUESTION}]}, code))
    monkeypatch.setattr(cli, 'run', runner)
    assert demo.main() == code
    args = runner.call_args.args[0]
    assert args.question == 'Nhân viên kết hôn được hỗ trợ bao nhiêu?'
    assert args.top_k == 5
    assert args.model_path == demo.REPOSITORY_ROOT / '.tmp/models/multilingual-e5-small' / args.model_revision
    output = capsys.readouterr().out
    assert demo.QUESTION in output
    assert output.count(demo.QUESTION) == 2


def test_ascii_stream_falls_back_losslessly(monkeypatch):
    buffer = io.BytesIO()
    stream = io.TextIOWrapper(buffer, encoding='ascii', errors='replace')
    monkeypatch.setattr(sys, 'stdout', stream)
    demo.print_result(demo.QUESTION)
    stream.flush()
    output = buffer.getvalue().decode('ascii')
    assert '\\u' in output
    assert output.rstrip().encode('ascii').decode('unicode_escape') == demo.QUESTION


def test_stream_without_encoding_or_reconfigure(monkeypatch):
    stream = io.StringIO()
    monkeypatch.setattr(sys, 'stdout', stream)
    demo.configure_console()
    demo.print_result(demo.QUESTION)
    assert stream.getvalue() == demo.QUESTION + '\n'


def test_dependency_diagnostics_suppressed(monkeypatch, capsys):
    async def fail(args):
        print('private diagnostic')
        print('private diagnostic', file=sys.stderr)
        raise RuntimeError('private diagnostic')
    monkeypatch.setattr(cli, 'run', fail)
    assert demo.main() == 2
    output = capsys.readouterr()
    assert 'private' not in output.out + output.err
    assert 'retrieval_failed' not in output.out
    assert output.out.endswith(demo.format_timings({}) + '\n')


@pytest.mark.parametrize('mode', ['direct', 'module', 'import'])
def test_entrypoints_from_other_directory(tmp_path, mode):
    root = Path(__file__).resolve().parents[2]
    # Inject only the runner/runtime; runpy exercises the actual entrypoint guard.
    script = '''
import asyncio, json, os, runpy, sys, types
root, mode = sys.argv[1:]
before = os.getcwd()
if mode != 'direct': sys.path.insert(0, root)
if mode == 'import':
    encoding = sys.stdout.encoding
    import scripts.try_retrieval
    assert sys.stdout.encoding == encoding
    assert not any(m in sys.modules for m in ('src.database', 'sentence_transformers', 'sqlalchemy', 'dotenv'))
else:
    async def run(args):
        assert args.model_path.is_absolute()
        return {'status': 'matches_found', 'matches': [{'content': args.question}]}, 0
    sys.modules['src.cli.retrieve_documents'] = types.SimpleNamespace(run=run)
    sys.modules['src.database.runtime'] = types.SimpleNamespace(run_async=asyncio.run)
    try:
        if mode == 'direct': runpy.run_path(os.path.join(root, 'scripts', 'try_retrieval.py'), run_name='__main__')
        else: runpy.run_module('scripts.try_retrieval', run_name='__main__')
    except SystemExit as exc:
        assert exc.code == 0
assert os.getcwd() == before
'''
    result = subprocess.run([sys.executable, '-B', '-c', script, str(root), mode],
                            cwd=tmp_path, capture_output=True, encoding='utf-8')
    assert result.returncode == 0, result.stderr
    if mode != 'import':
        assert demo.QUESTION in result.stdout
